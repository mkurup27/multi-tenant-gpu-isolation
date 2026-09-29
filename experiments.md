# Execution runbook: Multi-Tenant GPU Isolation

This is the corrected **lean design**. It replaces the earlier 8× H100/Bare
Metal plan.

## What this design can establish

- Tenant-visible, window-to-window variance on one H100 GPU Droplet over 14
  days.
- Whether that variance is structured by UTC hour or day.
- Which host and GPU signals coincide with slow windows.
- Reference signatures produced by deliberate SM, HBM, PCIe, CPU, storage,
  network-delay, and power-limit interventions on a separate H100.
- Self-inflicted queue, mixed-prompt, and KV-pressure symptoms.
- MIG and MPS behavior only if a disposable-host permission test succeeds.

It **cannot identify neighbors or prove that a neighbor caused a slow
request**. The H100 is passed through to a KVM guest, but CPU, RAM, NIC,
storage, PCIe, and physical placement guarantees still come from documentation
or an approved DigitalOcean statement, not from these measurements.

Absolute cross-tier comparisons are allowed only when model, revision, prompt,
output policy, offered rate, and region match. Otherwise serverless and
dedicated endpoints are reported only against their own history.

## Locked hardware design

- Existing `mk-test-droplet`: H100 80 GB, NYC2, Track C only for 14 days.
  Never run an antagonist, MIG, MPS, power-cap change, or stress test here.
- Existing `isolation-control`: 2 vCPU, 4 GB, NYC2, same VPC. It schedules
  windows and holds results.
- One additional 1× H100 in the same NYC2 VPC: Tracks B, E, and optional G,
  sequentially. Destroy it after those tracks.
- No 8× H100 and no Bare Metal. There is no single-tenant control, so the
  article must not claim one.

Current verified facts:

- H100: NVIDIA H100 80GB HBM3, driver 580.173.02, PCI passthrough, MIG disabled.
- Supported MIG profiles include two `3g.40gb` instances (profile ID 9).
- Docker 29.1.3 and NVIDIA Container Toolkit 1.19.1 are installed.
- Controller-to-H100 VPC connectivity and key-based SSH as `sammy` work.
- `sammy` needs a sudo password. Track C does not require unattended sudo on
  the H100 after vLLM is started manually.

---

# Phase 0 — Publication, budget, and comparability gates

Do not start the 14-day production clock until Steps 1–4 are complete.

## Step 1 — Freeze the claims and analysis

Open [`analysis-plan.md`](./analysis-plan.md). Fill the three minimum meaningful
effect margins and approver/date fields. The experimental unit is a scheduled
host-window, not a request.

After approval:

```bash
cd /path/to/multi-tenant-gpu-isolation
shasum -a 256 analysis-plan.md | tee preregistration.sha256
```

Do not rewrite the approved plan after production data arrive. Append dated
amendments instead.

## Step 2 — Decide which managed arms are comparable

For each serverless or dedicated endpoint, record:

- exact model ID and immutable revision, if exposed
- region
- precision/quantization, if exposed
- support for temperature 0, seed, streaming, and fixed output length

Use `/models` when the endpoint exposes it:

```bash
curl -s "$ENDPOINT_URL/models" \
  -H "Authorization: Bearer $ENDPOINT_KEY" | jq
```

If Qwen3-8B and the same generation controls are unavailable, set that arm's
`COMPARISON_GROUP=within-tier-only`. Do not compare its absolute latency or
throughput with the GPU Droplet.

## Step 3 — Approve a spend ceiling

Record current prices with screenshots or dated exports. The ceiling is:

```text
existing H100 price × 336 hours
+ controller price × 336 hours
+ intervention H100 price × planned intervention hours
+ managed endpoint charges
+ optional reprovisioning rounds
+ 20% retry/overrun reserve
```

Set a billing alert below that ceiling. Tag every new resource
`experiment:gpu-isolation` and give disposable resources an explicit destruction
date. The intervention host is created only after Track C is healthy.

## Step 4 — Record documentation guarantees separately

Archive the product/docs pages named in the article brief under `sources/` with
capture dates. Build the tier × isolation-layer grid with only:

- documented, with URL and exact quote
- CPTO-reviewed, with reviewer and date
- tenant-observed, from the experiments
- not specified

Do not turn observed low variance into an architectural guarantee.

---

# Phase 1 — Prepare the existing Track C H100

## Step 5 — Copy the host-side files

From the Mac, using the H100's public address without posting it:

```bash
cd /path/to/multi-tenant-gpu-isolation
ssh sammy@<H100_PUBLIC_IP> 'mkdir -p ~/isolation ~/isolation-results/track-c'
scp telemetry.sh preflight_gpu_host.sh \
  sammy@<H100_PUBLIC_IP>:~/isolation/
```

## Step 6 — Install low-overhead telemetry tools

On the H100:

```bash
sudo apt-get update
sudo apt-get install -y sysstat numactl jq git
command -v mpstat iostat nvidia-smi
```

Do not run the old preflight's Docker smoke test; the revised preflight is
read-only.

## Step 7 — Choose and pin vLLM and the model

Choose an approved **versioned** vLLM tag; never use `latest`. On the H100:

```bash
export VLLM_TAG='REPLACE_WITH_APPROVED_VERSIONED_TAG'
sudo docker pull "vllm/vllm-openai:$VLLM_TAG"
export VLLM_IMAGE=$(
  sudo docker image inspect "vllm/vllm-openai:$VLLM_TAG" \
    --format '{{index .RepoDigests 0}}'
)
sudo docker image inspect "$VLLM_IMAGE" >/dev/null
printf '%s\n' "$VLLM_IMAGE"
```

Resolve and record the Hugging Face commit:

```bash
export MODEL_ID='Qwen/Qwen3-8B'
export MODEL_REVISION=$(
  git ls-remote "https://huggingface.co/${MODEL_ID}.git" HEAD | awk '{print $1}'
)
test -n "$MODEL_REVISION"
printf 'model=%s\nrevision=%s\nimage=%s\n' \
  "$MODEL_ID" "$MODEL_REVISION" "$VLLM_IMAGE" | tee ~/isolation/versions.env
```

Copy `versions.env` back into this project folder for provenance. The image
digest and model commit must be reused on the intervention H100.

## Step 8 — Verify GPU-container access

This is the intentional mutating smoke test:

```bash
sudo docker run --rm --gpus all "$VLLM_IMAGE" nvidia-smi -L
```

It must print one H100.

## Step 9 — Start vLLM manually

Find the H100's VPC address without posting it:

```bash
export H100_VPC_IP=$(ip -4 -o address show eth1 | awk '{print $4}' | cut -d/ -f1)
source ~/isolation/versions.env
sudo docker run -d --name isolation-vllm --restart unless-stopped \
  --gpus all --ipc=host \
  -p "${H100_VPC_IP}:8000:8000" \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  "$VLLM_IMAGE" \
  --model "$MODEL_ID" --revision "$MODEL_REVISION" \
  --max-num-seqs 128 --disable-log-requests
```

Wait and capture resolved configuration:

```bash
until curl -sf "http://${H100_VPC_IP}:8000/health" >/dev/null; do
  sudo docker logs --tail 20 isolation-vllm
  sleep 15
done
sudo docker logs isolation-vllm 2>&1 \
  | grep -iE 'version|backend|max_num_seqs|gpu_memory' \
  | tee ~/isolation/engine-config.txt
```

Copy `engine-config.txt` back into this folder.

---

# Phase 2 — Install the restart-safe controller

## Step 10 — Copy and install controller files

From the Mac:

```bash
cd /path/to/multi-tenant-gpu-isolation
scp isolation_bench.py requirements.txt run_track_c_window.sh \
  schedule_track_c.sh isolation-track-c.service track_c.env.example \
  validate_track_c.py \
  sammy@<CONTROL_PUBLIC_IP>:~/
```

On the controller:

```bash
sudo apt-get update
sudo apt-get install -y python3-venv rsync util-linux coreutils
sudo mkdir -p /opt/isolation /etc/isolation /var/lib/isolation/results/track-c
sudo install -m 755 isolation_bench.py run_track_c_window.sh \
  schedule_track_c.sh validate_track_c.py /opt/isolation/
sudo install -m 644 requirements.txt /opt/isolation/
sudo install -m 644 isolation-track-c.service \
  /etc/systemd/system/isolation-track-c.service
sudo python3 -m venv /opt/isolation/venv
sudo /opt/isolation/venv/bin/pip install -r /opt/isolation/requirements.txt
```

## Step 11 — Install the dedicated controller key

The key already exists in `~/.ssh/isolation_harness` on the controller:

```bash
sudo install -m 600 ~/.ssh/isolation_harness /etc/isolation/isolation_harness
```

Verify the H100 host-key fingerprint through the existing trusted session:

```bash
ssh -i ~/.ssh/isolation_harness sammy@<H100_VPC_IP> \
  'ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub'
ssh-keyscan -t ed25519 <H100_VPC_IP> 2>/dev/null | ssh-keygen -lf -
```

The fingerprints must match. Then install the scanned key globally:

```bash
ssh-keyscan -H -t ed25519 <H100_VPC_IP> 2>/dev/null \
  | sudo tee -a /etc/ssh/ssh_known_hosts >/dev/null
```

## Step 12 — Configure Track C without committing secrets

```bash
sudo cp track_c.env.example /etc/isolation/track_c.env
sudo chmod 600 /etc/isolation/track_c.env
sudo nano /etc/isolation/track_c.env
```

Fill the H100 VPC address. Leave managed arms disabled until Step 2 is settled.
If enabling one, place its key only in this root-readable file. Never put a
populated environment file in the project folder.

The initial self-hosted offered rate is only a pilot value. Step 14 calibrates
it below saturation.

## Step 13 — Check the private API and one benchmark window

On the controller:

```bash
curl -sf http://<H100_VPC_IP>:8000/health
sudo /opt/isolation/run_track_c_window.sh /etc/isolation/track_c.env
sudo find /var/lib/isolation/results/track-c -maxdepth 2 -type f -print
```

A complete window contains raw request JSONL, summary JSON, vLLM metrics JSONL,
GPU/CPU/disk/network telemetry, metadata, and `COMPLETE`.

## Step 14 — Calibrate offered load before the clock starts

On the controller, run 120-second Poisson windows at 0.5, 1, 2, and 4 requests/s
against the existing H100. Keep these under `results/pilot-rate/`, not Track C.
Use the exact production prompt and output settings:

```bash
sudo mkdir -p /var/lib/isolation/results/pilot-rate
for rate in 0.5 1 2 4; do
  sudo /opt/isolation/venv/bin/python /opt/isolation/isolation_bench.py \
    --base-url http://<H100_VPC_IP>:8000/v1 \
    --metrics-url http://<H100_VPC_IP>:8000/metrics \
    --model Qwen/Qwen3-8B --arrival poisson --rate "$rate" \
    --max-outstanding 64 --warmup 30 --duration 120 \
    --max-tokens 256 --prompt-words 380 --seed 20260903 --ignore-eos \
    --tier pilot --comparison-group matched-qwen3-8b \
    --label "pilot_rate_${rate}" \
    --out "/var/lib/isolation/results/pilot-rate/rate_${rate}"
done
```

Choose the highest rate with:

- zero client admission drops
- zero vLLM preemption delta
- waiting-request gauge staying at zero
- controller CPU not saturated

Set `SELF_HOSTED_RATE_RPS` to that value. Matched managed arms use the same
offered rate; unmatched arms may use their own fixed rate but remain
within-tier-only.

---

# Phase 3 — Pilot, then run Track C for 14 clean days

## Step 15 — Start the 48-hour pilot

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now isolation-track-c.service
sudo systemctl status isolation-track-c.service
sudo journalctl -u isolation-track-c.service -f
```

The service aligns windows to UTC half-hours, prevents overlapping windows,
times out hung runs, and restarts after controller reboot.

## Step 16 — Check the pilot daily

```bash
sudo cat /var/lib/isolation/heartbeat
sudo find /var/lib/isolation/results/track-c -name COMPLETE | wc -l
sudo /opt/isolation/venv/bin/python /opt/isolation/validate_track_c.py \
  /var/lib/isolation/results/track-c \
  --start-utc <PILOT_START_UTC> --days 2
```

The pilot is a feasibility check, not a power calculation. Check:

- expected windows exist
- no admission drops
- no empty raw files
- GPU, CPU, disk, network, and vLLM metrics were captured
- no unrelated process used the H100

If configuration changes, stop the service, move the pilot directory aside,
and restart a fresh production series. Never mix configurations.

## Step 17 — Start the production clock

At an exact UTC half-hour after the pilot passes:

```bash
sudo systemctl stop isolation-track-c.service
sudo mv /var/lib/isolation/results/track-c \
  "/var/lib/isolation/results/track-c-pilot-$(date -u +%Y%m%dT%H%M%SZ)"
sudo mkdir -p /var/lib/isolation/results/track-c
NEXT=$(( ($(date +%s) / 1800 + 1) * 1800 ))
date -u -d "@$NEXT" +%Y-%m-%dT%H:%M:%SZ \
  | sudo tee /var/lib/isolation/production-start-utc
sudo systemctl start isolation-track-c.service
```

Run for 14 complete UTC days. Do not run any intervention on this H100.

## Step 18 — Back up results daily to this folder

From the Mac:

```bash
cd /path/to/multi-tenant-gpu-isolation
mkdir -p results/track-c
rsync -az sammy@<CONTROL_PUBLIC_IP>:/var/lib/isolation/results/track-c/ \
  results/track-c/
```

Do not wait until day 14 for the first copy.

---

# Phase 4 — Prepare the separate intervention H100

Create one additional 1× H100 in NYC2, in the same team and VPC. Tag it
`experiment:gpu-isolation` and record its destruction date. Tracks B, E, and G run
here only.

## Step 19 — Permission and capability gate

Run the revised preflight and verify:

- H100 80 GB and PCI passthrough
- Docker NVIDIA runtime
- profile `3g.40gb` exists
- sufficient free disk
- VPC interface name

Before relying on MIG or power controls, stop any workload and test on this
disposable host:

```bash
sudo nvidia-smi -i 0 -pl 650
sudo nvidia-smi -i 0 -pl 700
```

For MIG, do not test until Track B/E are complete. If enabling MIG or resetting
the GPU is denied, mark Track G unavailable and retain the error as evidence.

## Step 20 — Install intervention tooling and root-only controller access

Install:

```bash
sudo apt-get update
sudo apt-get install -y sysstat numactl jq stress-ng fio iproute2
```

Copy `telemetry.sh`, `antagonists.py`, and `intervention_control.sh` to
`/opt/isolation`; copy `intervention.env.example` to
`/etc/isolation/intervention.env`, fill the pinned image digest and VPC
interface, and set mode 600.

Use a separate controller SSH key authorized for root on this disposable host,
restricted to the controller's VPC source address. Do not reuse or expose the
Track C host's credentials.

Start vLLM with the same image digest, model commit, and engine flags as Track C.
Run the benchmark from the controller, not localhost, so network-delay
treatments affect the measured path.

## Step 21 — Prepare the fixed storage file outside measurements

```bash
sudo /opt/isolation/intervention_control.sh prepare
```

This creates one fixed file once. Track B performs read-only direct I/O against
it; it does not create a fresh file during each measured window.

---

# Phase 5 — Track B: randomized signature library

## Step 22 — Generate and freeze the schedule

On the controller:

```bash
python3 /opt/isolation/generate_track_b_schedule.py \
  --seed 20260904 --repeats 3 \
  --output /opt/isolation/track_b_schedule.csv
sha256sum /opt/isolation/track_b_schedule.csv \
  | tee /var/lib/isolation/track_b_schedule.sha256
```

The schedule randomizes treatments within repeat blocks and interleaves
controls. Do not sort it before execution.

## Step 23 — Verify safe cleanup before the full run

Run one low-intensity treatment manually:

```bash
sudo /opt/isolation/intervention_control.sh start cpu 0.25 30
sleep 10
sudo /opt/isolation/intervention_control.sh stop
pgrep -af 'stress-ng|antagonists.py'
```

The final command should show no antagonist. Confirm the original GPU power
limit and qdisc are unchanged.

## Step 24 — Run Track B

Install `run_track_b.sh`, `generate_track_b_schedule.py`, and a root-readable
`intervention.env` on the controller, then:

```bash
sudo systemd-run --unit=isolation-track-b --collect \
  /opt/isolation/run_track_b.sh \
  /etc/isolation/intervention.env \
  /opt/isolation/track_b_schedule.csv
sudo journalctl -u isolation-track-b -f
```

The runner uses exact PIDs/container names, restores the original power limit,
removes only a qdisc it successfully created, captures telemetry throughout,
stops on cleanup failure, and resumes by skipping only cells with verified
summary files and `COMPLETE` markers. It never calls a public iperf server.

GPU-side SM/HBM/PCIe interventions are controlled positive signatures because
we create the competing process on the passed-through GPU. CPU, storage, and
network interventions on a virtualized host are upper-bound demonstrations:
unknown real host activity can still contribute. Do not describe those rows as
pure measurements of neighbor effects.

---

# Phase 6 — Track E: self-inflicted controls

## Step 25 — Run Track E

```bash
sudo systemd-run --unit=isolation-track-e --collect \
  /opt/isolation/run_track_e.sh /etc/isolation/intervention.env
sudo journalctl -u isolation-track-e -f
```

This produces:

- a closed-loop concurrency ramp through and beyond `max_num_seqs`
- genuinely mixed short/long prompts in the same windows
- repeated long-generation windows intended to create KV pressure

Whether KV pressure was actually created is decided from continuous gauge and
preemption data. The old synthetic “24-hour diurnal” loop was removed because
it was only a one-hour load-shape sweep.

---

# Phase 7 — Optional Track G: MPS and MIG

Track G is exploratory and runs only after B/E. Never run these commands on the
Track C H100.

## Step 26 — MPS validity requirements

Stop vLLM first. Start the MPS daemon **before** creating either victim or
antagonist CUDA context. Both containers must mount the same MPS pipe/log
directories and set `CUDA_MPS_PIPE_DIRECTORY`; verify both appear as MPS
clients. Apply active-thread percentages to both, not only the antagonist.

Run three repeats of:

1. undivided GPU baseline at aggregate rate 2 requests/s
2. time-sharing victim plus antagonist
3. MPS victim plus antagonist, both started after MPS

If the container/driver combination cannot register both clients, report MPS
as unavailable rather than using the old invalid arm.

## Step 27 — MIG permission and capacity test

Stop and remove every GPU container, then:

```bash
sudo nvidia-smi -i 0 -mig 1
sudo nvidia-smi -i 0 --gpu-reset
sudo nvidia-smi mig -i 0 -cgi 9,9 -C
sudo nvidia-smi -L
```

Use the discovered MIG UUIDs; do not hard-code them. Run:

1. victim in MIG A, idle MIG B
2. victim in MIG A, HBM antagonist in MIG B
3. two simultaneous vLLM victims, one per MIG, at 1 request/s each
4. after restoring the undivided GPU, one victim at aggregate 2 requests/s

Repeat each three times. Compare two-MIG aggregate throughput with the
undivided equal-offered-load control. Wait for every antagonist, stop/remove
both containers, confirm no compute process remains, then restore:

```bash
sudo nvidia-smi mig -i 0 -dci
sudo nvidia-smi mig -i 0 -dgi
sudo nvidia-smi -i 0 -mig 0
sudo nvidia-smi -i 0 --gpu-reset
```

If any permission step fails, stop and retain the output. Do not improvise.

---

# Phase 8 — Optional provisioning-lottery sample

This is descriptive, not proof of independent physical hosts: unique GPU UUIDs
can still belong to one multi-GPU machine.

If budget permits, after B/E/G destroy the intervention H100 and perform three
sequential reprovisionings of the same SKU. For each:

1. create one tagged H100 with an automatic destruction reminder
2. pin the same image/model revisions
3. wait for health, then run the identical benchmark
4. save GPU UUID, CPU model, PCIe link, and summary
5. copy and verify data
6. destroy that instance before creating the next

Never launch eight unattended H100s. Model time-of-day explicitly because
sequential samples are not simultaneous.

---

# Phase 9 — Validate, collect, and destroy

## Step 28 — Validate Track C before teardown

On the controller:

```bash
START=$(sudo cat /var/lib/isolation/production-start-utc)
sudo /opt/isolation/venv/bin/python /opt/isolation/validate_track_c.py \
  /var/lib/isolation/results/track-c --start-utc "$START" --days 14
```

Do not destroy anything until this exits zero and daily backups exist.

## Step 29 — Calculate the cost axis

Use billed hours and retained output-token/usable-request totals, not nominal
peak throughput:

```bash
python3 cost_model.py --name gpu-droplet \
  --hourly-price <DATED_PRICE> --hours <BILLED_HOURS> \
  --output-tokens <RETAINED_OUTPUT_TOKENS> \
  --usable-requests <USABLE_REQUESTS> \
  --headroom-factor <PREREGISTERED_SLA_HEADROOM> \
  > results/gpu-droplet-cost.json
```

Run once per tier. The headroom factor is an input derived from the
preregistered SLA analysis, not a number selected to make a tier look better.

## Step 30 — Capture provenance

Save in this folder:

- preflight output for every host
- image digest and model commit
- engine logs and resolved backend
- approved analysis plan and checksum
- treatment schedule and checksum
- exact UTC production start/end
- all raw JSONL, metrics, telemetry, summaries, and failure logs
- dated prices and total billed cost
- list of excluded windows with preregistered reason

## Step 31 — Stop and tear down

```bash
sudo systemctl disable --now isolation-track-c.service
```

Destroy the intervention H100 immediately after its files are verified. Keep or
destroy the controller according to the retention decision. Confirm in the
Control Panel that no disposable GPU or orphaned resource remains billable.

---

# Operational rules

1. The Track C H100 is never an intervention host.
2. No populated secret file enters this project folder.
3. A window is identified by scheduled UTC block and never overwritten.
4. Slow valid data are never deleted as “outliers.”
5. Managed tiers with different models remain within-tier-only.
6. `inter_chunk_latency_s` is not called ITL.
7. No neighbor-causality claim is made from tenant-visible variance.
8. Any change to workload, image, model revision, rate, or engine flags starts
   a new series; it is never mixed into the 14-day production series.
