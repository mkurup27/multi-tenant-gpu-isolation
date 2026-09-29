#!/usr/bin/env bash
# Track G MPS arm. Run from the intervention controller.
#
# Three cases, three repeats:
#   m1_undivided_solo  victim alone at 2 requests/s
#   m2_time_sharing    victim plus HBM antagonist, normal CUDA scheduling
#   m3_mps             victim plus HBM antagonist, both registered with MPS
set -Eeuo pipefail

CONFIG="${1:-/etc/isolation/intervention.env}"
INSTALL_DIR="${ISOLATION_INSTALL_DIR:-/opt/isolation}"
RESULTS_ROOT="${ISOLATION_RESULTS_DIR:-/var/lib/isolation/results/track-mps}"
PYTHON="${ISOLATION_PYTHON:-$INSTALL_DIR/venv/bin/python}"
REPEATS="${TRACK_MPS_REPEATS:-3}"
REMOTE_RESULTS=/var/lib/isolation-intervention/results
PORT=8000

# shellcheck disable=SC1090
source "$CONFIG"
mkdir -p "$RESULTS_ROOT"

SSH=(ssh -n -i "$INTERVENTION_SSH_KEY" -o BatchMode=yes -o IdentitiesOnly=yes
     -o StrictHostKeyChecking=yes "${INTERVENTION_USER}@${INTERVENTION_HOST}")
RSYNC_E="ssh -i $INTERVENTION_SSH_KEY -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"
MPS=/opt/isolation/mps_control.sh
ACTIVE=0

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ "$ACTIVE" -eq 1 ]]; then
    "${SSH[@]}" "$MPS stop" || true
    "${SSH[@]}" "/opt/isolation/telemetry.sh stop" || true
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

bench() {
  local out="$1" label="$2"
  "$PYTHON" "$INSTALL_DIR/isolation_bench.py" \
    --base-url "http://${INTERVENTION_HOST}:${PORT}/v1" \
    --metrics-url "http://${INTERVENTION_HOST}:${PORT}/metrics" \
    --model "$INTERVENTION_MODEL" \
    --arrival poisson --rate 2 --max-outstanding 64 \
    --warmup 30 --duration 120 --max-tokens 256 --prompt-words 380 \
    --seed 20260903 --ignore-eos --tier mps-scheduling \
    --comparison-group induced-qwen3-8b \
    --label "$label" --out "$out" > "${out}.stdout" 2>&1
}

start_case() {
  local tag="$1"
  ACTIVE=1
  "${SSH[@]}" "$MPS stop >/dev/null 2>&1 || true"
  "${SSH[@]}" "/opt/isolation/telemetry.sh stop >/dev/null 2>&1 || true"
  "${SSH[@]}" "/opt/isolation/telemetry.sh start ${REMOTE_RESULTS}/${tag}"
}

finish_case() {
  local tag="$1" out="$2" failed="$3"
  "${SSH[@]}" "$MPS stop"
  "${SSH[@]}" "/opt/isolation/telemetry.sh stop"
  ACTIVE=0
  rsync -a -e "$RSYNC_E" \
    "${INTERVENTION_USER}@${INTERVENTION_HOST}:${REMOTE_RESULTS}/${tag}.*" \
    "$out/" || {
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
    m1_undivided_solo)
      "${SSH[@]}" "$MPS serve plain $PORT" | tee -a "$out/case.meta"
      bench "$out/victim" "$tag" || failed=1
      ;;
    m2_time_sharing)
      "${SSH[@]}" "$MPS serve plain $PORT" | tee -a "$out/case.meta"
      "${SSH[@]}" "$MPS antagonist plain 1.0 900" | tee -a "$out/case.meta"
      sleep 15
      bench "$out/victim" "$tag" || failed=1
      ;;
    m3_mps)
      # This ordering is the validity condition: daemon first, then both CUDA
      # contexts. The benchmark cannot begin until each container has been
      # matched to a client PID reported by the MPS control interface.
      "${SSH[@]}" "$MPS start-daemon" | tee -a "$out/case.meta"
      "${SSH[@]}" "$MPS serve mps $PORT" | tee -a "$out/case.meta"
      "${SSH[@]}" "$MPS antagonist mps 1.0 900" | tee -a "$out/case.meta"
      sleep 15
      "${SSH[@]}" "$MPS verify-clients" | tee -a "$out/mps-clients.txt"
      bench "$out/victim" "$tag" || failed=1
      ;;
    *)
      echo "unknown case: $kind" >&2
      exit 2
      ;;
  esac

  if [[ "$failed" -eq 0 ]]; then
    echo "benchmark_status=ok" >> "$out/case.meta"
  else
    echo "benchmark_status=failed" >> "$out/case.meta"
  fi
  finish_case "$tag" "$out" "$failed"
}

for repeat in $(seq 1 "$REPEATS"); do
  run_case m1_undivided_solo "$repeat"
  run_case m2_time_sharing "$repeat"
  run_case m3_mps "$repeat"
done

echo "Track G MPS arm complete"
