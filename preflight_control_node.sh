#!/usr/bin/env bash
# Preflight for the CONTROL NODE — the machine that drives the 14-day
# schedule, provisions and destroys instances, and collects results.
#
# This can be your Mac, but for the longitudinal track it should be a
# machine that stays up for 14 days. A laptop that sleeps will punch
# holes in the diurnal coverage the study depends on.
#
#   bash preflight_control_node.sh | tee preflight_control.txt

have() { command -v "$1" >/dev/null 2>&1; }

MISSING=()
need() {
  if have "$1"; then
    printf '  [ok]      %-16s %s  (%s)\n' "$1" "$3" "$($1 --version 2>&1 | head -n1)"
  else
    printf '  [MISSING] %-16s %s  (install: %s)\n' "$1" "$3" "$2"
    MISSING+=("$2")
  fi
}

printf '########################################################\n'
printf '# CONTROL NODE PREFLIGHT\n'
printf '# captured: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
printf '########################################################\n'

printf '\n=== 1. HOST\n'
printf '  os:     %s\n' "$(uname -srm)"
printf '  host:   %s\n' "$(hostname)"

printf '\n=== 2. REQUIRED TOOLING\n'
need doctl   "brew install doctl"      "provision/destroy instances (Track D)"
need ssh     "openssh"                 "reach the GPU hosts"
need python3 "brew install python"     "analysis"
need git     "brew install git"        "harness versioning"
need jq      "brew install jq"         "parsing API + results"
need rsync   "rsync"                   "pulling result files back"

printf '\n=== 3. DIGITALOCEAN API ACCESS\n'
if have doctl; then
  if doctl account get >/dev/null 2>&1; then
    printf '  [ok] authenticated\n'
    doctl account get 2>/dev/null | sed 's/^/       /'
    printf '\n  --- GPU-capable sizes visible to this account\n'
    doctl compute size list --format Slug,Memory,VCPUs,Disk,PriceMonthly,PriceHourly 2>/dev/null \
      | grep -iE 'gpu|^Slug' | sed 's/^/       /'
    printf '\n  --- existing droplets\n'
    doctl compute droplet list --format ID,Name,Region,Size,Status 2>/dev/null | sed 's/^/       /'
  else
    printf '  [MISSING] doctl is installed but not authenticated.\n'
    printf '            run: doctl auth init\n'
  fi
else
  printf '  [MISSING] doctl — cannot enumerate available GPU sizes or prices.\n'
fi

printf '\n=== 4. SSH KEY\n'
if ls ~/.ssh/id_* >/dev/null 2>&1; then
  for k in ~/.ssh/id_*.pub; do
    [ -f "$k" ] && printf '  [ok] %s\n' "$k"
  done
else
  printf '  [MISSING] no ssh keypair found in ~/.ssh — needed to reach instances\n'
fi

printf '\n=== 5. WILL THIS MACHINE STAY UP FOR 14 DAYS?\n'
if [ "$(uname -s)" = "Darwin" ]; then
  printf '  macOS detected. Check sleep settings:\n'
  pmset -g 2>/dev/null | grep -iE 'sleep|standby|hibernate' | sed 's/^/       /'
  printf '\n  If sleep is enabled, either run "caffeinate -dimsu" for the duration\n'
  printf '  or move the scheduler onto a small always-on Droplet. Gaps in the\n'
  printf '  schedule bias the diurnal analysis, which is the whole point of it.\n'
fi

if [ ${#MISSING[@]} -gt 0 ]; then
  printf '\n\nMISSING: %s\n' "$(printf '%s\n' "${MISSING[@]}" | sort -u | tr '\n' ' ')"
fi

printf '\n########################################################\n'
printf '# END CONTROL NODE PREFLIGHT\n'
printf '########################################################\n'
