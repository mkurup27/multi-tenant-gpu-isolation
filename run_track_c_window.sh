#!/usr/bin/env bash
# Run one synchronized Track C window from the NYC2 control Droplet.
set -Eeuo pipefail

CONFIG="${1:-/etc/isolation/track_c.env}"
INSTALL_DIR="${ISOLATION_INSTALL_DIR:-/opt/isolation}"
RESULTS_ROOT="${ISOLATION_RESULTS_DIR:-/var/lib/isolation/results/track-c}"
PYTHON="${ISOLATION_PYTHON:-$INSTALL_DIR/venv/bin/python}"

if [[ ! -r "$CONFIG" ]]; then
  echo "configuration is not readable: $CONFIG" >&2
  exit 2
fi
# shellcheck disable=SC1090
source "$CONFIG"

required=(
  H100_HOST H100_USER H100_SSH_KEY H100_RESULTS_DIR
  SELF_HOSTED_URL SELF_HOSTED_METRICS_URL SELF_HOSTED_MODEL
  SELF_HOSTED_RATE_RPS WINDOW_WARMUP_S WINDOW_DURATION_S MAX_OUTSTANDING
)
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "required configuration is empty: $name" >&2
    exit 2
  fi
done

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$RESULTS_ROOT/$STAMP"
mkdir -p "$RUN_DIR"
SSH=(ssh -i "$H100_SSH_KEY" -o BatchMode=yes -o IdentitiesOnly=yes
     -o StrictHostKeyChecking=yes "${H100_USER}@${H100_HOST}")
REMOTE_PREFIX="${H100_RESULTS_DIR}/${STAMP}"
TELEMETRY_STARTED=0
PIDS=()

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  if [[ "$TELEMETRY_STARTED" -eq 1 ]]; then
    "${SSH[@]}" "bash /home/${H100_USER}/isolation/telemetry.sh stop" || true
  fi
  {
    echo "ended_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "exit_status=$status"
  } >> "$RUN_DIR/window.meta"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

{
  echo "run_id=$STAMP"
  echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "controller=$(hostname -f)"
  echo "harness_sha256=$(sha256sum "$INSTALL_DIR/isolation_bench.py" | awk '{print $1}')"
} > "$RUN_DIR/window.meta"

"${SSH[@]}" "mkdir -p '$H100_RESULTS_DIR' && \
  bash /home/${H100_USER}/isolation/telemetry.sh start '$REMOTE_PREFIX'"
TELEMETRY_STARTED=1

COMMON=(
  "$PYTHON" "$INSTALL_DIR/isolation_bench.py"
  --arrival poisson
  --duration "$WINDOW_DURATION_S"
  --warmup "$WINDOW_WARMUP_S"
  --max-outstanding "$MAX_OUTSTANDING"
  --max-tokens 256
  --prompt-words 380
  --seed 20260903
)

"${COMMON[@]}" \
  --base-url "$SELF_HOSTED_URL" \
  --model "$SELF_HOSTED_MODEL" \
  --rate "$SELF_HOSTED_RATE_RPS" \
  --ignore-eos \
  --metrics-url "$SELF_HOSTED_METRICS_URL" \
  --tier gpu-droplet \
  --comparison-group matched-qwen3-8b \
  --label "gpu-droplet_${STAMP}" \
  --out "$RUN_DIR/gpu-droplet" > "$RUN_DIR/gpu-droplet.stdout" 2>&1 &
PIDS+=("$!")

if [[ "${SERVERLESS_ENABLED:-0}" == "1" ]]; then
  if [[ -z "${SERVERLESS_URL:-}" || -z "${SERVERLESS_MODEL:-}" || -z "${SERVERLESS_API_KEY:-}" ]]; then
    echo "serverless is enabled but its URL, model, or key is empty" >&2
    exit 2
  fi
  if [[ "${SERVERLESS_COMPARISON_GROUP:-within-tier-only}" != "within-tier-only" &&
        "${SERVERLESS_FIXED_OUTPUT_CONFIRMED:-0}" != "1" ]]; then
    echo "serverless cross-tier group requires SERVERLESS_FIXED_OUTPUT_CONFIRMED=1" >&2
    exit 2
  fi
  API_KEY="$SERVERLESS_API_KEY" "${COMMON[@]}" \
    --base-url "$SERVERLESS_URL" \
    --model "$SERVERLESS_MODEL" \
    --rate "${SERVERLESS_RATE_RPS:-$SELF_HOSTED_RATE_RPS}" \
    --tier serverless \
    --comparison-group "${SERVERLESS_COMPARISON_GROUP:-within-tier-only}" \
    --label "serverless_${STAMP}" \
    --out "$RUN_DIR/serverless" > "$RUN_DIR/serverless.stdout" 2>&1 &
  PIDS+=("$!")
fi

if [[ "${DEDICATED_ENABLED:-0}" == "1" ]]; then
  if [[ -z "${DEDICATED_URL:-}" || -z "${DEDICATED_MODEL:-}" || -z "${DEDICATED_API_KEY:-}" ]]; then
    echo "dedicated is enabled but its URL, model, or key is empty" >&2
    exit 2
  fi
  if [[ "${DEDICATED_COMPARISON_GROUP:-within-tier-only}" != "within-tier-only" &&
        "${DEDICATED_FIXED_OUTPUT_CONFIRMED:-0}" != "1" ]]; then
    echo "dedicated cross-tier group requires DEDICATED_FIXED_OUTPUT_CONFIRMED=1" >&2
    exit 2
  fi
  API_KEY="$DEDICATED_API_KEY" "${COMMON[@]}" \
    --base-url "$DEDICATED_URL" \
    --model "$DEDICATED_MODEL" \
    --rate "${DEDICATED_RATE_RPS:-$SELF_HOSTED_RATE_RPS}" \
    --tier dedicated-endpoint \
    --comparison-group "${DEDICATED_COMPARISON_GROUP:-within-tier-only}" \
    --label "dedicated_${STAMP}" \
    --out "$RUN_DIR/dedicated" > "$RUN_DIR/dedicated.stdout" 2>&1 &
  PIDS+=("$!")
fi

failed=0
for pid in "${PIDS[@]}"; do
  wait "$pid" || failed=1
done
PIDS=()

"${SSH[@]}" "bash /home/${H100_USER}/isolation/telemetry.sh stop"
TELEMETRY_STARTED=0

rsync -a -e "ssh -i $H100_SSH_KEY -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes" \
  "${H100_USER}@${H100_HOST}:${REMOTE_PREFIX}.*" "$RUN_DIR/" || failed=1

if [[ "$failed" -ne 0 ]]; then
  echo "one or more benchmark arms failed; inspect $RUN_DIR" >&2
  exit 1
fi
touch "$RUN_DIR/COMPLETE"
