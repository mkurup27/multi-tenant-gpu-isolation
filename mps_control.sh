#!/usr/bin/env bash
# Validity-checked MPS lifecycle for Track G.
# Run as root on the disposable intervention H100 only.
set -Eeuo pipefail

ACTION="${1:?usage: mps_control.sh start-daemon|stop|serve <plain|mps> <port>|antagonist <plain|mps> <intensity> <duration>|verify-clients|status}"
INSTALL_DIR="${ISOLATION_INSTALL_DIR:-/opt/isolation}"
CONFIG="${ISOLATION_INTERVENTION_CONFIG:-/etc/isolation/intervention.env}"
VERSIONS="${ISOLATION_VERSIONS_ENV:-/home/sammy/isolation/versions.env}"
PIPE_DIR="${ISOLATION_MPS_PIPE_DIR:-/tmp/isolation-mps/pipe}"
LOG_DIR="${ISOLATION_MPS_LOG_DIR:-/tmp/isolation-mps/log}"
THREAD_PERCENTAGE="${MPS_ACTIVE_THREAD_PERCENTAGE:-50}"
HEALTH_TIMEOUT="${MPS_HEALTH_TIMEOUT:-600}"
PREFIX="trackmps"

[[ -r "$CONFIG" ]] && { . "$CONFIG"; }
[[ -r "$VERSIONS" ]] && { . "$VERSIONS"; }

: "${VLLM_IMAGE:?VLLM_IMAGE unset; check $VERSIONS}"
: "${MODEL_ID:?MODEL_ID unset; check $VERSIONS}"
: "${MODEL_REVISION:?MODEL_REVISION unset; check $VERSIONS}"

VPC_IP="$(ip -4 -o address show "${NET_IF:-eth1}" | awk '{print $4}' | cut -d/ -f1)"
HF_CACHE="${MPS_HF_CACHE:-/home/sammy/.cache/huggingface}"

mps_control() {
  CUDA_MPS_PIPE_DIRECTORY="$PIPE_DIR" \
    CUDA_MPS_LOG_DIRECTORY="$LOG_DIR" \
    nvidia-cuda-mps-control
}

stop_containers() {
  local ids
  ids="$(docker ps -aq --filter "name=^${PREFIX}-" || true)"
  if [[ -n "$ids" ]]; then
    # shellcheck disable=SC2086
    docker rm -f $ids >/dev/null 2>&1 || true
  fi
}

stop_daemon() {
  if [[ -S "$PIPE_DIR/control" || -f "$PIPE_DIR/nvidia-cuda-mps-control.pid" ]]; then
    printf 'quit\n' | mps_control >/dev/null 2>&1 || true
  fi
  for _ in $(seq 1 30); do
    if ! pgrep -f '(^|/)nvidia-cuda-mps-(control|server)( |$)' >/dev/null; then
      break
    fi
    sleep 1
  done
  if pgrep -f '(^|/)nvidia-cuda-mps-(control|server)( |$)' >/dev/null; then
    echo "MPS daemon or server did not stop cleanly" >&2
    pgrep -af '(^|/)nvidia-cuda-mps-(control|server)( |$)' >&2 || true
    return 1
  fi
  rm -rf "$(dirname "$PIPE_DIR")"
}

wait_healthy() {
  local port="$1" name="$2" deadline=$((SECONDS + HEALTH_TIMEOUT))
  until curl -sf "http://${VPC_IP}:${port}/health" >/dev/null 2>&1; do
    if [[ "$(docker inspect --format '{{.State.Running}}' "$name" 2>/dev/null)" != "true" ]]; then
      echo "$name exited before becoming healthy" >&2
      docker logs --tail 60 "$name" >&2 || true
      exit 1
    fi
    if ((SECONDS > deadline)); then
      echo "$name did not become healthy within ${HEALTH_TIMEOUT}s" >&2
      docker logs --tail 60 "$name" >&2 || true
      exit 1
    fi
    sleep 10
  done
}

server_pids() {
  printf 'get_server_list\n' | mps_control 2>/dev/null |
    awk '$1 ~ /^[0-9]+$/ {print $1}'
}

client_pids() {
  local server
  while read -r server; do
    [[ -n "$server" ]] || continue
    printf 'get_client_list %s\n' "$server" | mps_control 2>/dev/null |
      awk '$1 ~ /^[0-9]+$/ {print $1}'
  done < <(server_pids)
}

container_has_client() {
  local name="$1" client pid
  while read -r client; do
    while read -r pid; do
      [[ "$client" == "$pid" ]] && return 0
    done < <(docker top "$name" -eo pid 2>/dev/null | awk 'NR > 1 {print $1}')
  done < <(client_pids)
  return 1
}

case "$ACTION" in
  start-daemon)
    stop_containers
    stop_daemon
    mig_mode="$(nvidia-smi --query-gpu=mig.mode.current --format=csv,noheader |
      tr -d '[:space:]')"
    [[ "$mig_mode" == "Disabled" ]] || {
      echo "MIG must be disabled before MPS starts" >&2
      exit 1
    }
    if nvidia-smi --query-compute-apps=pid --format=csv,noheader |
       grep -q '[0-9]'; then
      echo "a CUDA process already exists; refusing to start MPS" >&2
      exit 1
    fi
    install -d -m 0700 "$PIPE_DIR" "$LOG_DIR"
    CUDA_VISIBLE_DEVICES=0 \
      CUDA_MPS_PIPE_DIRECTORY="$PIPE_DIR" \
      CUDA_MPS_LOG_DIRECTORY="$LOG_DIR" \
      nvidia-cuda-mps-control -d
    [[ -f "$PIPE_DIR/nvidia-cuda-mps-control.pid" ]] || {
      echo "MPS daemon did not create its PID file" >&2
      exit 1
    }
    echo "MPS daemon started before any client context"
    ;;

  serve)
    mode="${2:?missing mode}"; port="${3:?missing port}"
    [[ "$mode" == "plain" || "$mode" == "mps" ]] || {
      echo "mode must be plain or mps" >&2
      exit 2
    }
    name="${PREFIX}-victim"
    docker rm -f "$name" >/dev/null 2>&1 || true
    extra=()
    if [[ "$mode" == "mps" ]]; then
      [[ -f "$PIPE_DIR/nvidia-cuda-mps-control.pid" ]] || {
        echo "start the MPS daemon before creating the victim" >&2
        exit 1
      }
      extra=(
        -e "CUDA_MPS_PIPE_DIRECTORY=$PIPE_DIR"
        -e "CUDA_MPS_LOG_DIRECTORY=$LOG_DIR"
        -e "CUDA_MPS_ACTIVE_THREAD_PERCENTAGE=$THREAD_PERCENTAGE"
        -v "$PIPE_DIR:$PIPE_DIR"
        -v "$LOG_DIR:$LOG_DIR"
      )
    fi
    docker run -d --name "$name" --ipc=host --gpus '"device=0"' \
      "${extra[@]}" \
      -p "${VPC_IP}:${port}:8000" \
      -v "$HF_CACHE:/root/.cache/huggingface" \
      "$VLLM_IMAGE" \
      --model "$MODEL_ID" --revision "$MODEL_REVISION" \
      --max-num-seqs 128 >/dev/null
    wait_healthy "$port" "$name"
    echo "victim healthy mode=$mode port=$port thread_percentage=$([[ "$mode" == "mps" ]] && echo "$THREAD_PERCENTAGE" || echo unrestricted)"
    ;;

  antagonist)
    mode="${2:?missing mode}"; intensity="${3:-1.0}"; duration="${4:-900}"
    : "${ANTAGONIST_IMAGE:?set ANTAGONIST_IMAGE in $CONFIG}"
    [[ "$mode" == "plain" || "$mode" == "mps" ]] || {
      echo "mode must be plain or mps" >&2
      exit 2
    }
    name="${PREFIX}-antagonist"
    docker rm -f "$name" >/dev/null 2>&1 || true
    extra=()
    if [[ "$mode" == "mps" ]]; then
      [[ -f "$PIPE_DIR/nvidia-cuda-mps-control.pid" ]] || {
        echo "start the MPS daemon before creating the antagonist" >&2
        exit 1
      }
      extra=(
        -e "CUDA_MPS_PIPE_DIRECTORY=$PIPE_DIR"
        -e "CUDA_MPS_LOG_DIRECTORY=$LOG_DIR"
        -e "CUDA_MPS_ACTIVE_THREAD_PERCENTAGE=$THREAD_PERCENTAGE"
        -v "$PIPE_DIR:$PIPE_DIR"
        -v "$LOG_DIR:$LOG_DIR"
      )
    fi
    docker run -d --name "$name" --ipc=host --gpus '"device=0"' \
      "${extra[@]}" \
      -v "$INSTALL_DIR:/work:ro" -w /work \
      --entrypoint python3 "$ANTAGONIST_IMAGE" \
      /work/antagonists.py hbm --intensity "$intensity" \
      --duration "$duration" >/dev/null
    sleep 5
    if [[ "$(docker inspect --format '{{.State.Running}}' "$name" 2>/dev/null)" != "true" ]]; then
      echo "antagonist failed during startup" >&2
      docker logs "$name" >&2 || true
      exit 1
    fi
    echo "antagonist running mode=$mode thread_percentage=$([[ "$mode" == "mps" ]] && echo "$THREAD_PERCENTAGE" || echo unrestricted)"
    ;;

  verify-clients)
    mapfile -t clients < <(client_pids)
    if [[ "${#clients[@]}" -lt 2 ]]; then
      echo "expected at least two MPS clients, found ${#clients[@]}: ${clients[*]-}" >&2
      exit 1
    fi
    for name in "${PREFIX}-victim" "${PREFIX}-antagonist"; do
      if ! container_has_client "$name"; then
        echo "$name does not own any reported MPS client PID" >&2
        echo "reported clients: ${clients[*]}" >&2
        docker top "$name" -eo pid,comm >&2 || true
        exit 1
      fi
    done
    echo "MPS client validation passed"
    echo "server_pids=$(server_pids | paste -sd, -)"
    echo "client_pids=${clients[*]}"
    for name in "${PREFIX}-victim" "${PREFIX}-antagonist"; do
      echo "$name"
      docker top "$name" -eo pid,comm
    done
    ;;

  stop)
    stop_containers
    stop_daemon
    if nvidia-smi --query-compute-apps=pid --format=csv,noheader |
       grep -q '[0-9]'; then
      echo "CUDA processes remain after MPS cleanup" >&2
      nvidia-smi --query-compute-apps=pid,process_name --format=csv >&2
      exit 1
    fi
    echo "MPS containers and daemon stopped; no CUDA process remains"
    ;;

  status)
    echo "mig_mode=$(nvidia-smi --query-gpu=mig.mode.current --format=csv,noheader)"
    echo "control_pid=$(cat "$PIPE_DIR/nvidia-cuda-mps-control.pid" 2>/dev/null || true)"
    echo "server_pids=$(server_pids | paste -sd, -)"
    echo "client_pids=$(client_pids | paste -sd, -)"
    docker ps --filter "name=^${PREFIX}-" --format '{{.Names}}\t{{.Status}}'
    ;;

  *)
    echo "unknown action: $ACTION" >&2
    exit 2
    ;;
esac
