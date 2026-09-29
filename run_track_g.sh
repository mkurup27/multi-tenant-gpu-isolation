#!/usr/bin/env bash
# Track G: does MIG actually contain a neighbour, and what does it cost?
# Run from the intervention controller. Never point this at the Track C host.
#
# Four cases, three repeats, in a fixed order:
#   g1_mig_solo        victim on slice A, slice B idle
#   g2_mig_antagonist  victim on slice A, HBM antagonist on slice B
#   g3_mig_dual        a victim on each slice, 1 rps each
#   g4_undivided       MIG off, one victim at 2 rps
#
# g2 against g1 is the isolation test, read against Track B's whole-GPU HBM
# result. g3 against g4 is the capacity test at equal offered load.
set -Eeuo pipefail

CONFIG="${1:-/etc/isolation/intervention.env}"
INSTALL_DIR="${ISOLATION_INSTALL_DIR:-/opt/isolation}"
RESULTS_ROOT="${ISOLATION_RESULTS_DIR:-/var/lib/isolation/results/track-g}"
PYTHON="${ISOLATION_PYTHON:-$INSTALL_DIR/venv/bin/python}"
REPEATS="${TRACK_G_REPEATS:-3}"
REMOTE_RESULTS=/var/lib/isolation-intervention/results

# shellcheck disable=SC1090
source "$CONFIG"
mkdir -p "$RESULTS_ROOT"

SSH=(ssh -n -i "$INTERVENTION_SSH_KEY" -o BatchMode=yes -o IdentitiesOnly=yes
     -o StrictHostKeyChecking=yes "${INTERVENTION_USER}@${INTERVENTION_HOST}")
RSYNC_E="ssh -i $INTERVENTION_SSH_KEY -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"
MIG=/opt/isolation/mig_control.sh
HOST="${INTERVENTION_HOST}"
PORT_A=8000
PORT_B=8001
ACTIVE=0

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ "$ACTIVE" -eq 1 ]]; then
    "${SSH[@]}" "$MIG stop-workloads" || true
    "${SSH[@]}" "/opt/isolation/telemetry.sh stop" || true
  fi
  # The GPU must never be left partitioned for the next operator.
  "${SSH[@]}" "$MIG disable" || true
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

bench() {
  # bench <out-prefix> <port> <rate> <label>
  local out="$1" port="$2" rate="$3" label="$4"
  "$PYTHON" "$INSTALL_DIR/isolation_bench.py" \
    --base-url "http://${HOST}:${port}/v1" \
    --metrics-url "http://${HOST}:${port}/metrics" \
    --model "$INTERVENTION_MODEL" \
    --arrival poisson --rate "$rate" --max-outstanding 64 \
    --warmup 30 --duration 120 --max-tokens 256 --prompt-words 380 \
    --seed 20260903 --ignore-eos --tier mig-partition \
    --comparison-group induced-qwen3-8b \
    --label "$label" --out "$out" > "${out}.stdout" 2>&1
}

start_case() {
  local tag="$1"
  ACTIVE=1
  "${SSH[@]}" "$MIG stop-workloads >/dev/null 2>&1 || true"
  "${SSH[@]}" "/opt/isolation/telemetry.sh stop >/dev/null 2>&1 || true"
  "${SSH[@]}" "/opt/isolation/telemetry.sh start ${REMOTE_RESULTS}/${tag}"
}

finish_case() {
  local tag="$1" out="$2" failed="$3"
  "${SSH[@]}" "$MIG stop-workloads"
  "${SSH[@]}" "/opt/isolation/telemetry.sh stop"
  ACTIVE=0
  rsync -a -e "$RSYNC_E" \
    "${INTERVENTION_USER}@${INTERVENTION_HOST}:${REMOTE_RESULTS}/${tag}.*" "$out/" || {
      echo "telemetry_copy_status=failed" >> "$out/case.meta"
      failed=1
    }
  echo "ended_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$out/case.meta"
  if [[ "$failed" -ne 0 ]]; then
    echo "case $tag failed; fix and rerun to resume" >&2
    exit 1
  fi
  touch "$out/COMPLETE"
  sleep 45
}

run_case() {
  local kind="$1" repeat="$2"
  local tag="${kind}_r${repeat}"
  local out="$RESULTS_ROOT/$tag"
  if [[ -f "$out/COMPLETE" && -s "$out/victim.summary.json" ]]; then
    echo "skipping completed case $tag"
    return
  fi
  mkdir -p "$out"
  {
    echo "case=$kind"
    echo "repeat=$repeat"
    echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$out/case.meta"

  local failed=0
  start_case "$tag"

  case "$kind" in
    g1_mig_solo)
      "${SSH[@]}" "$MIG serve a $PORT_A" | tee -a "$out/case.meta"
      bench "$out/victim" "$PORT_A" 2 "$tag" || failed=1
      ;;
    g2_mig_antagonist)
      "${SSH[@]}" "$MIG serve a $PORT_A" | tee -a "$out/case.meta"
      "${SSH[@]}" "$MIG antagonist b 1.0 900" | tee -a "$out/case.meta"
      sleep 15
      bench "$out/victim" "$PORT_A" 2 "$tag" || failed=1
      ;;
    g3_mig_dual)
      "${SSH[@]}" "$MIG serve a $PORT_A" | tee -a "$out/case.meta"
      "${SSH[@]}" "$MIG serve b $PORT_B" | tee -a "$out/case.meta"
      # Both slices are driven at once; the pair is the unit of comparison.
      bench "$out/victim" "$PORT_A" 1 "${tag}_a" &
      local pid_a=$!
      bench "$out/victim_b" "$PORT_B" 1 "${tag}_b" &
      local pid_b=$!
      wait "$pid_a" || failed=1
      wait "$pid_b" || failed=1
      ;;
    g4_undivided)
      "${SSH[@]}" "$MIG serve full $PORT_A" | tee -a "$out/case.meta"
      bench "$out/victim" "$PORT_A" 2 "$tag" || failed=1
      ;;
    *)
      echo "unknown case: $kind" >&2; exit 2 ;;
  esac

  if [[ "$failed" -eq 0 ]]; then
    echo "benchmark_status=ok" >> "$out/case.meta"
  else
    echo "benchmark_status=failed" >> "$out/case.meta"
  fi
  finish_case "$tag" "$out" "$failed"
}

echo "== partitioned phase =="
"${SSH[@]}" "$MIG enable"
for repeat in $(seq 1 "$REPEATS"); do
  run_case g1_mig_solo "$repeat"
  run_case g2_mig_antagonist "$repeat"
  run_case g3_mig_dual "$repeat"
done

echo "== undivided phase =="
"${SSH[@]}" "$MIG disable"
for repeat in $(seq 1 "$REPEATS"); do
  run_case g4_undivided "$repeat"
done

echo "Track G complete"
