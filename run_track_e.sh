#!/usr/bin/env bash
# Self-inflicted contention controls on the disposable intervention H100.
set -Eeuo pipefail

CONFIG="${1:-/etc/isolation/intervention.env}"
INSTALL_DIR="${ISOLATION_INSTALL_DIR:-/opt/isolation}"
RESULTS_ROOT="${ISOLATION_RESULTS_DIR:-/var/lib/isolation/results/track-e}"
PYTHON="${ISOLATION_PYTHON:-$INSTALL_DIR/venv/bin/python}"
# shellcheck disable=SC1090
source "$CONFIG"
mkdir -p "$RESULTS_ROOT"
SSH=(ssh -n -i "$INTERVENTION_SSH_KEY" -o BatchMode=yes -o IdentitiesOnly=yes
     -o StrictHostKeyChecking=yes "${INTERVENTION_USER}@${INTERVENTION_HOST}")
TELEMETRY=0

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ "$TELEMETRY" -eq 1 ]]; then
    "${SSH[@]}" "/opt/isolation/telemetry.sh stop" || true
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

run_case() {
  local tag="$1"; shift
  local out="$RESULTS_ROOT/$tag"
  if [[ -f "$out/COMPLETE" && -s "$out/victim.summary.json" ]]; then
    echo "skipping completed case $tag"
    return
  fi
  mkdir -p "$out"
  # Armed first so an abort during the start still tears telemetry down, and a
  # capture left by a killed predecessor cannot stall every remaining case.
  TELEMETRY=1
  "${SSH[@]}" "/opt/isolation/telemetry.sh stop >/dev/null 2>&1 || true"
  "${SSH[@]}" "/opt/isolation/telemetry.sh start /var/lib/isolation-intervention/results/e_${tag}"
  timeout --signal=TERM --kill-after=60s 20m \
    "$PYTHON" "$INSTALL_DIR/isolation_bench.py" \
      --base-url "$INTERVENTION_URL" \
      --metrics-url "$INTERVENTION_METRICS_URL" \
      --model "$INTERVENTION_MODEL" \
      --warmup 30 --duration 180 --max-tokens 256 --seed 20260903 \
      --ignore-eos --tier self-inflicted-control \
      --comparison-group induced-qwen3-8b \
      --label "$tag" --out "$out/victim" "$@" > "$out/victim.stdout" 2>&1
  "${SSH[@]}" "/opt/isolation/telemetry.sh stop"
  TELEMETRY=0
  rsync -a -e "ssh -i $INTERVENTION_SSH_KEY -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes" \
    "${INTERVENTION_USER}@${INTERVENTION_HOST}:/var/lib/isolation-intervention/results/e_${tag}.*" \
    "$out/"
  touch "$out/COMPLETE"
  sleep 60
}

# Closed-loop saturation sweep in a frozen randomized order. Levels below
# max_num_seqs are controls, not evidence of queue contention.
mapfile -t RAMP_ORDER < <(python3 - <<'PY'
import random
levels = [1, 8, 32, 64, 128, 256, 512]
random.Random(20260904).shuffle(levels)
print(*levels, sep="\n")
PY
)
printf '%s\n' "${RAMP_ORDER[@]}" > "$RESULTS_ROOT/ramp_order.txt"
for concurrency in "${RAMP_ORDER[@]}"; do
  run_case "ramp_c${concurrency}" --arrival closed --concurrency "$concurrency"
done

# A genuinely mixed distribution: short and long prompts coexist in each run.
for repeat in 1 2 3; do
  run_case "mixed_lengths_r${repeat}" --arrival poisson --rate 2 \
    --max-outstanding 64 --prompt-words-set 100,1600
done

# Long generations at high closed-loop concurrency create KV pressure. Whether
# they actually do is decided from the continuous KV gauge and preemption delta.
for repeat in 1 2 3; do
  run_case "kv_pressure_r${repeat}" --arrival closed --concurrency 128 \
    --max-tokens 2048 --duration 300
done
