#!/usr/bin/env bash
# Run the randomized signature library from the NYC2 controller.
set -Eeuo pipefail

CONFIG="${1:-/etc/isolation/intervention.env}"
SCHEDULE="${2:-/opt/isolation/track_b_schedule.csv}"
INSTALL_DIR="${ISOLATION_INSTALL_DIR:-/opt/isolation}"
RESULTS_ROOT="${ISOLATION_RESULTS_DIR:-/var/lib/isolation/results/track-b}"
PYTHON="${ISOLATION_PYTHON:-$INSTALL_DIR/venv/bin/python}"

# shellcheck disable=SC1090
source "$CONFIG"
mkdir -p "$RESULTS_ROOT"
SSH=(ssh -n -i "$INTERVENTION_SSH_KEY" -o BatchMode=yes -o IdentitiesOnly=yes
     -o StrictHostKeyChecking=yes "${INTERVENTION_USER}@${INTERVENTION_HOST}")
ACTIVE=0

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ "$ACTIVE" -eq 1 ]]; then
    "${SSH[@]}" "/opt/isolation/intervention_control.sh stop" || true
    "${SSH[@]}" "/opt/isolation/telemetry.sh stop" || true
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

while IFS=, read -r sequence repeat layer intensity; do
  intensity="${intensity%$'\r'}"
  tag="$(printf '%03d_r%s_%s_i%s' "$sequence" "$repeat" "$layer" "$intensity")"
  out="$RESULTS_ROOT/$tag"
  if [[ -f "$out/COMPLETE" && -s "$out/victim.summary.json" ]]; then
    echo "skipping completed treatment $tag"
    continue
  fi
  mkdir -p "$out"
  {
    echo "sequence=$sequence"
    echo "repeat=$repeat"
    echo "layer=$layer"
    echo "intensity=$intensity"
    echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$out/treatment.meta"

  # Armed before the first remote start so an abort between the two starts
  # still tears down telemetry on the intervention host.
  ACTIVE=1
  # systemd admits one instance of this unit at a time, so a capture still
  # running here belongs to a killed predecessor and would otherwise stall
  # every subsequent treatment.
  "${SSH[@]}" "/opt/isolation/intervention_control.sh stop >/dev/null 2>&1 || true"
  "${SSH[@]}" "/opt/isolation/telemetry.sh stop >/dev/null 2>&1 || true"
  "${SSH[@]}" "/opt/isolation/telemetry.sh start /var/lib/isolation-intervention/results/$tag"
  # Keep the treatment alive past the benchmark's worst-case drain; cleanup
  # stops it immediately after the victim finishes.
  "${SSH[@]}" "/opt/isolation/intervention_control.sh start '$layer' '$intensity' 900"
  sleep 30

  benchmark_failed=0
  if ! timeout --signal=TERM --kill-after=60s 10m \
    "$PYTHON" "$INSTALL_DIR/isolation_bench.py" \
      --base-url "$INTERVENTION_URL" \
      --metrics-url "$INTERVENTION_METRICS_URL" \
      --model "$INTERVENTION_MODEL" \
      --arrival poisson --rate 2 --max-outstanding 64 \
      --warmup 30 --duration 120 --max-tokens 256 --prompt-words 380 \
      --seed 20260903 --ignore-eos --tier induced-contention \
      --comparison-group induced-qwen3-8b \
      --label "$tag" --out "$out/victim" > "$out/victim.stdout" 2>&1; then
    echo "benchmark_status=failed" >> "$out/treatment.meta"
    benchmark_failed=1
  else
    echo "benchmark_status=ok" >> "$out/treatment.meta"
  fi

  "${SSH[@]}" "/opt/isolation/intervention_control.sh stop"
  "${SSH[@]}" "/opt/isolation/telemetry.sh stop"
  ACTIVE=0
  rsync -a -e "ssh -i $INTERVENTION_SSH_KEY -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes" \
    "${INTERVENTION_USER}@${INTERVENTION_HOST}:/var/lib/isolation-intervention/results/${tag}.*" \
    "$out/" || {
      echo "telemetry_copy_status=failed" >> "$out/treatment.meta"
      benchmark_failed=1
    }
  echo "ended_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$out/treatment.meta"
  if [[ "$benchmark_failed" -ne 0 ]]; then
    echo "treatment failed; fix and resume from $tag" >&2
    exit 1
  fi
  touch "$out/COMPLETE"

  # Fixed washout plus an exact antagonist-container check. The vLLM victim
  # remains on the GPU by design.
  sleep 60
  if [[ -n "$("${SSH[@]}" "docker ps -q --filter name=isolation-antagonist" | tr -d '[:space:]')" ]]; then
    echo "antagonist container survived cleanup; stopping at $tag" >&2
    exit 1
  fi
done < <(tail -n +2 "$SCHEDULE")
