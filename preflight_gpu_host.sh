#!/usr/bin/env bash
# Preflight capture for the multi-tenant GPU isolation experiments.
#
# Run this on EVERY Linux GPU host in the study (each GPU Droplet, the Bare
# Metal instance, and any candidate box) BEFORE provisioning the real runs.
#
#   scp preflight_gpu_host.sh root@<host>:/tmp/
#   ssh root@<host> 'bash /tmp/preflight_gpu_host.sh' | tee preflight_<label>.txt
#
# It is read-only: nothing here installs, modifies, or loads the machine.
# Every check is allowed to fail; missing tools are reported, not fatal.
#
# The output doubles as the Track A introspection record, so keep the file.

LABEL="${1:-$(hostname)}"

have() { command -v "$1" >/dev/null 2>&1; }

section() {
  printf '\n\n=== %s %s\n' "$1" "$(printf '=%.0s' $(seq 1 $((60 - ${#1}))))"
}

show() {
  # show <description> <command...>
  local desc="$1"; shift
  printf -- '--- %s\n' "$desc"
  if have "${1}"; then
    "$@" 2>&1 | sed 's/^/    /'
  else
    printf '    [MISSING COMMAND: %s]\n' "$1"
  fi
  printf '\n'
}

MISSING=()
need() {
  # need <command> <package-hint> <why>
  if have "$1"; then
    printf '  [ok]      %-16s %s\n' "$1" "$3"
  else
    printf '  [MISSING] %-16s %s  (install: %s)\n' "$1" "$3" "$2"
    MISSING+=("$2")
  fi
}

printf '########################################################\n'
printf '# GPU HOST PREFLIGHT — %s\n' "$LABEL"
printf '# captured: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
printf '########################################################\n'


section "1. IDENTITY AND OS"
show "hostname"        hostname -f
show "kernel"          uname -a
show "distro"          cat /etc/os-release
show "uptime"          uptime
printf -- '--- root access\n'
if [ "$(id -u)" -eq 0 ]; then
  printf '    running as root\n'
elif have sudo && sudo -n true 2>/dev/null; then
  printf '    passwordless sudo available\n'
else
  printf '    NOT root and no passwordless sudo — several experiments need it\n'
  printf '    (drop_caches, tc netem, nvidia-smi -pl, MIG reconfiguration)\n'
fi


section "2. VIRTUALIZATION — is this a guest, and under what?"
show "systemd-detect-virt" systemd-detect-virt
show "virt-what"           virt-what
printf -- '--- hypervisor fields from lscpu\n'
if have lscpu; then
  lscpu | grep -iE 'hypervisor|virtuali[sz]ation|model name|^cpu\(s\)|socket|thread|core' | sed 's/^/    /'
else
  printf '    [MISSING COMMAND: lscpu]\n'
fi
printf '\n'
show "DMI system info"   dmidecode -t system
show "DMI bios vendor"   dmidecode -s bios-vendor


section "3. CPU AND MEMORY"
show "cpu model"        lscpu
show "NUMA layout"      numactl --hardware
printf -- '--- steal time snapshot (10s) — the key co-tenancy signal\n'
if have mpstat; then
  mpstat 1 10 2>&1 | tail -n 5 | sed 's/^/    /'
  printf '\n    NOTE: %%steal above ~0.5%% at idle means the hypervisor is\n'
  printf '    descheduling this vCPU. That is Track B host-CPU evidence.\n'
else
  printf '    [MISSING COMMAND: mpstat — from the sysstat package]\n'
  printf '    fallback, raw steal field from /proc/stat (col 8):\n'
  grep '^cpu ' /proc/stat | sed 's/^/    /'
fi
printf '\n'
show "memory"           free -h
printf -- '--- cgroup version (decides the host-CPU throttling method)\n'
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then
  printf '    cgroup v2 (unified) — use cpu.max for quota\n'
  printf '    controllers: %s\n' "$(cat /sys/fs/cgroup/cgroup.controllers)"
else
  printf '    cgroup v1 — use cpu.cfs_quota_us / cpu.cfs_period_us\n'
fi
printf '\n'


section "4. GPU — model, count, isolation mode"
if ! have nvidia-smi; then
  printf '    [MISSING COMMAND: nvidia-smi] — no NVIDIA driver on this host.\n'
  printf '    Everything GPU-side below will be empty.\n'
else
  show "driver and GPU summary" nvidia-smi
  printf -- '--- per-GPU identity (keep this: GPU UUID detects host re-use in Track D)\n'
  nvidia-smi --query-gpu=index,name,uuid,serial,pci.bus_id,memory.total,\
vbios_version,driver_version,persistence_mode,ecc.mode.current \
    --format=csv 2>&1 | sed 's/^/    /'
  printf '\n'

  printf -- '--- virtualization mode (Pass-Through vs VGPU vs None)\n'
  nvidia-smi -q 2>/dev/null | grep -iE 'virtualization mode|host vgpu mode|gpu virtualization' \
    | sed 's/^/    /' || printf '    (field not reported by this driver)\n'
  printf '\n'

  printf -- '--- PCIe link: negotiated vs maximum\n'
  nvidia-smi --query-gpu=pcie.link.gen.current,pcie.link.gen.max,\
pcie.link.width.current,pcie.link.width.max --format=csv 2>&1 | sed 's/^/    /'
  printf '\n'

  printf -- '--- MIG capability and current mode\n'
  nvidia-smi --query-gpu=mig.mode.current,mig.mode.pending --format=csv 2>&1 | sed 's/^/    /'
  nvidia-smi mig -lgip 2>&1 | head -n 20 | sed 's/^/    /'
  printf '\n'

  printf -- '--- multi-GPU topology (decides whether the PCIe/NVLink layer is testable)\n'
  nvidia-smi topo -m 2>&1 | sed 's/^/    /'
  printf '\n'
  nvidia-smi nvlink -s 2>&1 | head -n 20 | sed 's/^/    /'
  printf '\n'

  printf -- '--- power limit range (needed for the thermal/power sweep)\n'
  nvidia-smi --query-gpu=power.default_limit,power.min_limit,power.max_limit,power.limit \
    --format=csv 2>&1 | sed 's/^/    /'
  printf '\n'

  printf -- '--- throttle-reason field naming (differs by driver generation)\n'
  if nvidia-smi --query-gpu=clocks_event_reasons.sw_power_cap --format=csv >/dev/null 2>&1; then
    printf '    USE: clocks_event_reasons.*\n'
  elif nvidia-smi --query-gpu=clocks_throttle_reasons.sw_power_cap --format=csv >/dev/null 2>&1; then
    printf '    USE: clocks_throttle_reasons.*\n'
  else
    printf '    WARNING: neither naming accepted — telemetry needs a different source\n'
  fi
  printf '\n'

  printf -- '--- anything already running on the GPU (should be empty before a run)\n'
  nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv 2>&1 | sed 's/^/    /'
  printf '\n'
fi

show "DCGM (preferred telemetry source)" dcgmi discovery -l


section "5. STORAGE — where weights live, and what backs it"
show "block devices"    lsblk -o NAME,SIZE,TYPE,ROTA,MOUNTPOINT,MODEL
show "nvme devices"     nvme list
show "filesystem usage" df -hT
printf -- '--- device backing the likely weights path\n'
for p in /root /home /var/lib/docker /mnt; do
  [ -d "$p" ] && printf '    %-20s -> %s\n' "$p" "$(df -h "$p" 2>/dev/null | awk 'NR==2{print $1" "$4" free"}')"
done
printf '\n'
printf -- '--- can the page cache be dropped? (needed for cold-read arms)\n'
if [ -w /proc/sys/vm/drop_caches ]; then
  printf '    yes — /proc/sys/vm/drop_caches is writable\n'
else
  printf '    NO — not writable. Cold-cache conditions cannot be forced on this host.\n'
fi
printf '\n'


section "6. NETWORK"
printf -- '--- interfaces and link speed\n'
for i in /sys/class/net/*; do
  n=$(basename "$i")
  [ "$n" = "lo" ] && continue
  printf '    %-10s speed=%-8s mtu=%s\n' "$n" \
    "$(cat "$i/speed" 2>/dev/null || echo '?')" \
    "$(cat "$i/mtu" 2>/dev/null || echo '?')"
done
printf '\n'
show "routing (identifies the egress device for tc netem)" ip route
printf -- '--- qdisc support for tc netem (network-layer antagonist)\n'
if have tc; then
  tc qdisc show 2>&1 | sed 's/^/    /'
  if modinfo sch_netem >/dev/null 2>&1; then
    printf '    sch_netem module available\n'
  else
    printf '    WARNING: sch_netem not found — network antagonist unavailable\n'
  fi
else
  printf '    [MISSING COMMAND: tc — from the iproute2 package]\n'
fi
printf '\n'


section "7. CONTAINER RUNTIME AND SERVING STACK"
show "docker"            docker version
show "nvidia runtime"    nvidia-ctk --version
printf -- '--- can docker see the GPU?\n'
if have docker; then
  if docker info --format '{{json .Runtimes}}' >/tmp/isolation-docker-runtimes.$$ 2>&1; then
    sed 's/^/    /' /tmp/isolation-docker-runtimes.$$
    printf '    NVIDIA runtime listed above means GPU containers are configured;\n'
    printf '    the separate smoke test verifies an actual GPU container later.\n'
  else
    printf '    Docker socket is not readable by this user:\n'
    sed 's/^/    /' /tmp/isolation-docker-runtimes.$$
  fi
  rm -f /tmp/isolation-docker-runtimes.$$
else
  printf '    [docker not installed]\n'
fi
printf '\n'
show "python"            python3 --version
printf -- '--- relevant python packages\n'
if have python3; then
  python3 - <<'PY' 2>&1 | sed 's/^/    /'
import importlib.metadata as md
for p in ("vllm","torch","transformers","httpx","numpy","pandas","matplotlib","aiohttp"):
    try:
        print(f"{p:16s} {md.version(p)}")
    except Exception:
        print(f"{p:16s} -- not installed")
PY
fi
printf '\n'


section "8. REQUIREMENTS CHECK"
printf '\nMeasurement and telemetry:\n'
need nvidia-smi  "nvidia driver"        "GPU telemetry, power cap, MIG"
need mpstat      "sysstat"              "CPU steal time — key co-tenancy signal"
need iostat      "sysstat"              "disk telemetry"
need vmstat      "procps"               "memory pressure"
need numactl     "numactl"              "NUMA layout and pinning"
need jq          "jq"                   "parsing results"

printf '\nAntagonists (Track B — one per isolation layer):\n'
need stress-ng   "stress-ng"            "host CPU antagonist"
need fio         "fio"                  "storage antagonist"
need iperf3      "iperf3"               "network bandwidth antagonist"
need tc          "iproute2"             "network latency/jitter antagonist"

printf '\nServing and orchestration:\n'
need docker      "docker.io"            "pinned vLLM container"
need python3     "python3"              "harness"
need git         "git"                  "harness checkout"
need tmux        "tmux"                 "long runs surviving disconnect"

printf '\nOptional but useful:\n'
need dcgmi       "datacenter-gpu-manager" "SM occupancy + DRAM active (HBM bandwidth)"
need virt-what   "virt-what"              "hypervisor identification"
need dmidecode   "dmidecode"              "host hardware identification"

if [ ${#MISSING[@]} -gt 0 ]; then
  UNIQ=$(printf '%s\n' "${MISSING[@]}" | sort -u | tr '\n' ' ')
  printf '\n\nINSTALL COMMAND FOR THIS HOST:\n'
  printf '  apt-get update && apt-get install -y %s\n' "$UNIQ"
else
  printf '\n\nAll checked tooling present.\n'
fi

printf '\n\n########################################################\n'
printf '# END PREFLIGHT — %s\n' "$LABEL"
printf '########################################################\n'
