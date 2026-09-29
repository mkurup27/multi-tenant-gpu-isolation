#!/usr/bin/env bash
# Safe start/stop wrapper for one induced-contention treatment.
# Run as root on the disposable intervention H100 only.
set -Eeuo pipefail

ACTION="${1:?usage: intervention_control.sh start <layer> <intensity> <duration> | stop | prepare}"
STATE_DIR="${ISOLATION_INTERVENTION_STATE:-/run/isolation-intervention}"
INSTALL_DIR="${ISOLATION_INSTALL_DIR:-/opt/isolation}"
CONFIG="${ISOLATION_INTERVENTION_CONFIG:-/etc/isolation/intervention.env}"
mkdir -p "$STATE_DIR"

if [[ -r "$CONFIG" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG"
fi

cleanup() {
  if [[ -f "$STATE_DIR/container" ]]; then
    docker rm -f "$(cat "$STATE_DIR/container")" >/dev/null 2>&1 || true
  fi
  if [[ -f "$STATE_DIR/pgid" ]]; then
    kill -- "-$(cat "$STATE_DIR/pgid")" >/dev/null 2>&1 || true
  fi
  if [[ -f "$STATE_DIR/qdisc_device" ]]; then
    tc qdisc del dev "$(cat "$STATE_DIR/qdisc_device")" root >/dev/null 2>&1 || true
  fi
  if [[ -f "$STATE_DIR/original_power_limit" ]]; then
    nvidia-smi -i 0 -pl "$(cat "$STATE_DIR/original_power_limit")" >/dev/null 2>&1 || true
  fi
  rm -f "$STATE_DIR/container" "$STATE_DIR/pgid" \
    "$STATE_DIR/qdisc_device" "$STATE_DIR/original_power_limit" \
    "$STATE_DIR/active"
}

abort() {
  local status=$?
  trap - ERR INT TERM
  cleanup
  if [[ "$status" -eq 0 ]]; then
    status=130
  fi
  exit "$status"
}

if [[ "$ACTION" == "stop" ]]; then
  cleanup
  echo "intervention stopped and original state restored"
  exit 0
fi

if [[ "$ACTION" == "prepare" ]]; then
  : "${FIO_FILE:=/var/lib/isolation-intervention/fio.bin}"
  mkdir -p "$(dirname "$FIO_FILE")"
  if [[ ! -f "$FIO_FILE" ]]; then
    fallocate -l "${FIO_SIZE_GIB:-8}G" "$FIO_FILE"
  fi
  echo "prepared $FIO_FILE"
  exit 0
fi

if [[ "$ACTION" != "start" ]]; then
  echo "unknown action: $ACTION" >&2
  exit 2
fi
if [[ -f "$STATE_DIR/active" ]]; then
  echo "an intervention is already active; run stop first" >&2
  exit 1
fi

LAYER="${2:?missing layer}"
INTENSITY="${3:?missing intensity}"
DURATION="${4:?missing duration}"
if [[ "$LAYER" != "control" ]]; then
python3 - "$INTENSITY" <<'PY'
import sys
value = float(sys.argv[1])
if not 0 < value <= 1:
    raise SystemExit("intensity must be > 0 and <= 1")
PY
fi

trap abort ERR INT TERM
echo "$LAYER $INTENSITY $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE_DIR/active"

case "$LAYER" in
  control)
    ;;
  sm|hbm|pcie)
    : "${ANTAGONIST_IMAGE:?set ANTAGONIST_IMAGE to a pinned image digest}"
    name="isolation-antagonist-$$"
    docker run -d --name "$name" --gpus '"device=0"' \
      -v "$INSTALL_DIR:/work:ro" -w /work \
      --entrypoint python3 "$ANTAGONIST_IMAGE" \
      /work/antagonists.py "$LAYER" --intensity "$INTENSITY" \
      --duration "$DURATION" >/dev/null
    echo "$name" > "$STATE_DIR/container"
    sleep 2
    if [[ "$(docker inspect --format '{{.State.Running}}' "$name" 2>/dev/null)" != "true" ]]; then
      echo "GPU antagonist failed during startup: $name" >&2
      docker logs "$name" >&2 || true
      cleanup
      exit 1
    fi
    ;;
  cpu)
    workers="$(python3 -c "import os; print(max(1, int(os.cpu_count() * $INTENSITY)))")"
    setsid stress-ng --cpu "$workers" --timeout "${DURATION}s" >/dev/null 2>&1 &
    echo "$!" > "$STATE_DIR/pgid"
    ;;
  storage)
    : "${FIO_FILE:=/var/lib/isolation-intervention/fio.bin}"
    [[ -f "$FIO_FILE" ]] || {
      echo "missing $FIO_FILE; run prepare before the experiment" >&2
      exit 1
    }
    jobs="$(python3 -c "print(max(1, int(8 * $INTENSITY)))")"
    # fio's --output only redirects its report; the inherited stdout and stderr
    # would keep the caller's SSH channel open until the run ended.
    setsid fio --name=isolation-hog --filename="$FIO_FILE" --readonly \
      --rw=randread --bs=1M --iodepth=32 --numjobs="$jobs" --direct=1 \
      --time_based --runtime="$DURATION" --output=/dev/null \
      >/dev/null 2>&1 &
    echo "$!" > "$STATE_DIR/pgid"
    ;;
  network_delay)
    : "${NET_IF:?set NET_IF to the VPC interface}"
    delay_ms="$(python3 -c "print(max(1, int(20 * $INTENSITY)))")"
    jitter_ms="$(python3 -c "print(max(1, int(10 * $INTENSITY)))")"
    tc qdisc add dev "$NET_IF" root handle 1: netem \
      delay "${delay_ms}ms" "${jitter_ms}ms" distribution normal
    echo "$NET_IF" > "$STATE_DIR/qdisc_device"
    ;;
  power)
    original="$(nvidia-smi -i 0 --query-gpu=power.limit --format=csv,noheader,nounits)"
    minimum="$(nvidia-smi -i 0 --query-gpu=power.min_limit --format=csv,noheader,nounits)"
    original="${original// /}"; minimum="${minimum// /}"
    echo "$original" > "$STATE_DIR/original_power_limit"
    target="$(python3 -c "print(int($original - ($original - $minimum) * $INTENSITY))")"
    nvidia-smi -i 0 -pl "$target" >/dev/null
    ;;
  *)
    echo "unsupported layer: $LAYER" >&2
    exit 2
    ;;
esac

echo "started layer=$LAYER intensity=$INTENSITY duration=$DURATION"
