#!/usr/bin/env bash
# MIG lifecycle and workload placement for Track G.
# Run as root on the disposable intervention H100 only. Never on the Track C host.
set -Eeuo pipefail

ACTION="${1:?usage: mig_control.sh enable|disable|uuids|serve <a|b|full> <port>|antagonist <a|b> <intensity> <duration>|stop-workloads|status}"
STATE_DIR="${ISOLATION_MIG_STATE:-/run/isolation-mig}"
INSTALL_DIR="${ISOLATION_INSTALL_DIR:-/opt/isolation}"
CONFIG="${ISOLATION_INTERVENTION_CONFIG:-/etc/isolation/intervention.env}"
VERSIONS="${ISOLATION_VERSIONS_ENV:-/home/sammy/isolation/versions.env}"
PROFILE="${MIG_PROFILE_ID:-9}"          # 3g.40gb, two per H100
HEALTH_TIMEOUT="${MIG_HEALTH_TIMEOUT:-600}"

mkdir -p "$STATE_DIR"
[[ -r "$CONFIG" ]] && { . "$CONFIG"; }
[[ -r "$VERSIONS" ]] && { . "$VERSIONS"; }

: "${VLLM_IMAGE:?VLLM_IMAGE unset; check $VERSIONS}"
: "${MODEL_ID:?MODEL_ID unset; check $VERSIONS}"
: "${MODEL_REVISION:?MODEL_REVISION unset; check $VERSIONS}"

VPC_IP="$(ip -4 -o address show "${NET_IF:-eth1}" | awk '{print $4}' | cut -d/ -f1)"
HF_CACHE="${MIG_HF_CACHE:-/home/sammy/.cache/huggingface}"

# Every container this script creates carries this prefix so stop-workloads
# never has to guess and never touches anything else on the host.
PREFIX="trackg"

list_uuids() {
  nvidia-smi -L | grep -o 'MIG-[0-9a-f-]*'
}

require_two_instances() {
  local count
  count="$(list_uuids | wc -l | tr -d '[:space:]')"
  if [[ "$count" -ne 2 ]]; then
    echo "expected 2 MIG instances, found $count; run 'enable' first" >&2
    exit 1
  fi
}

slice_uuid() {
  case "$1" in
    a) list_uuids | sed -n 1p ;;
    b) list_uuids | sed -n 2p ;;
    *) echo "unknown slice: $1" >&2; exit 2 ;;
  esac
}

stop_workloads() {
  local ids
  ids="$(docker ps -aq --filter "name=^${PREFIX}-" || true)"
  if [[ -n "$ids" ]]; then
    # shellcheck disable=SC2086
    docker rm -f $ids >/dev/null 2>&1 || true
  fi
}

wait_healthy() {
  local port="$1" name="$2" deadline=$((SECONDS + HEALTH_TIMEOUT))
  until curl -sf "http://${VPC_IP}:${port}/health" >/dev/null 2>&1; do
    if [[ "$(docker inspect --format '{{.State.Running}}' "$name" 2>/dev/null)" != "true" ]]; then
      echo "$name exited before becoming healthy" >&2
      docker logs --tail 40 "$name" >&2 || true
      exit 1
    fi
    if (( SECONDS > deadline )); then
      echo "$name did not become healthy within ${HEALTH_TIMEOUT}s" >&2
      docker logs --tail 40 "$name" >&2 || true
      exit 1
    fi
    sleep 10
  done
}

case "$ACTION" in
  enable)
    stop_workloads
    current="$(nvidia-smi --query-gpu=mig.mode.current --format=csv,noheader | tr -d '[:space:]')"
    if [[ "$current" != "Enabled" ]]; then
      nvidia-smi -i 0 -mig 1
      current="$(nvidia-smi --query-gpu=mig.mode.current --format=csv,noheader | tr -d '[:space:]')"
      if [[ "$current" != "Enabled" ]]; then
        # Only reset when the mode is genuinely stuck pending; a reset with a
        # live container attached would fail anyway.
        nvidia-smi -i 0 --gpu-reset
      fi
    fi
    if [[ "$(list_uuids | wc -l | tr -d '[:space:]')" -ne 2 ]]; then
      nvidia-smi mig -i 0 -dci >/dev/null 2>&1 || true
      nvidia-smi mig -i 0 -dgi >/dev/null 2>&1 || true
      nvidia-smi mig -i 0 -cgi "${PROFILE},${PROFILE}" -C
    fi
    require_two_instances
    list_uuids > "$STATE_DIR/uuids"
    echo "MIG enabled with 2 instances"
    cat "$STATE_DIR/uuids"
    ;;

  disable)
    stop_workloads
    nvidia-smi mig -i 0 -dci >/dev/null 2>&1 || true
    nvidia-smi mig -i 0 -dgi >/dev/null 2>&1 || true
    nvidia-smi -i 0 -mig 0
    current="$(nvidia-smi --query-gpu=mig.mode.current --format=csv,noheader | tr -d '[:space:]')"
    if [[ "$current" != "Disabled" ]]; then
      nvidia-smi -i 0 --gpu-reset
    fi
    rm -f "$STATE_DIR/uuids"
    nvidia-smi --query-gpu=mig.mode.current --format=csv,noheader
    echo "undivided GPU restored"
    ;;

  uuids)
    list_uuids
    ;;

  serve)
    slot="${2:?missing slot}"; port="${3:?missing port}"
    name="${PREFIX}-vllm-${slot}"
    docker rm -f "$name" >/dev/null 2>&1 || true
    if [[ "$slot" == "full" ]]; then
      device="device=0"
    else
      require_two_instances
      device="device=$(slice_uuid "$slot")"
    fi
    docker run -d --name "$name" --ipc=host \
      --gpus "\"$device\"" \
      -p "${VPC_IP}:${port}:8000" \
      -v "${HF_CACHE}:/root/.cache/huggingface" \
      "$VLLM_IMAGE" \
      --model "$MODEL_ID" --revision "$MODEL_REVISION" \
      --max-num-seqs 128 >/dev/null
    wait_healthy "$port" "$name"
    # The KV block count is the capacity cost of partitioning, so record it
    # rather than re-deriving it from logs later.
    docker logs "$name" 2>&1 | grep -iE 'GPU KV cache size|Available KV cache memory' \
      | tail -2 > "$STATE_DIR/kv_${slot}.txt" || true
    echo "serving slot=$slot port=$port device=$device"
    cat "$STATE_DIR/kv_${slot}.txt" 2>/dev/null || true
    ;;

  antagonist)
    slot="${2:?missing slot}"; intensity="${3:-1.0}"; duration="${4:-900}"
    : "${ANTAGONIST_IMAGE:?set ANTAGONIST_IMAGE in $CONFIG}"
    require_two_instances
    name="${PREFIX}-antagonist"
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker run -d --name "$name" --gpus "\"device=$(slice_uuid "$slot")\"" \
      -v "$INSTALL_DIR:/work:ro" -w /work \
      --entrypoint python3 "$ANTAGONIST_IMAGE" \
      /work/antagonists.py hbm --intensity "$intensity" \
      --duration "$duration" >/dev/null
    sleep 5
    if [[ "$(docker inspect --format '{{.State.Running}}' "$name" 2>/dev/null)" != "true" ]]; then
      echo "MIG antagonist failed during startup" >&2
      docker logs "$name" >&2 || true
      docker rm -f "$name" >/dev/null 2>&1 || true
      exit 1
    fi
    echo "antagonist running on slice $slot"
    ;;

  stop-workloads)
    stop_workloads
    echo "track-g containers removed"
    ;;

  status)
    nvidia-smi --query-gpu=mig.mode.current --format=csv,noheader
    list_uuids || true
    docker ps --filter "name=^${PREFIX}-" --format '{{.Names}}\t{{.Status}}'
    ;;

  *)
    echo "unknown action: $ACTION" >&2
    exit 2
    ;;
esac
