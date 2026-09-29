#!/usr/bin/env bash
# telemetry.sh — start/stop the GPU and host samplers around a measured window.
#
#   bash telemetry.sh start results/run1
#   ... run the benchmark ...
#   bash telemetry.sh stop
#
# Writes four CSVs alongside the run:
#   <out>.gpu.csv    1 Hz nvidia-smi: clocks, temp, power, util, throttle flags
#   <out>.cpu.csv    1 Hz mpstat, including %steal — the co-tenancy signal
#   <out>.disk.csv   1 Hz iostat
#   <out>.net.csv    1 Hz interface counters
#
# Auto-detects the throttle-reason field naming, which differs by driver
# generation and silently yields empty columns if you guess wrong.

set -u

ACTION="${1:?usage: telemetry.sh start <out-prefix> | stop}"
STATE_DIR="${ISOLATION_STATE_DIR:-/tmp/isolation-telemetry}"
PIDFILE="$STATE_DIR/pids"
mkdir -p "$STATE_DIR"

if [ "$ACTION" = "stop" ]; then
  if [ -f "$PIDFILE" ]; then
    while read -r p; do
      kill -TERM "$p" 2>/dev/null || true
      for _ in $(seq 1 20); do
        kill -0 "$p" 2>/dev/null || break
        sleep 0.1
      done
      kill -KILL "$p" 2>/dev/null || true
    done < "$PIDFILE"
    rm -f "$PIDFILE"
    echo "telemetry stopped"
  else
    echo "no telemetry running"
  fi
  exit 0
fi

OUT="${2:?usage: telemetry.sh start <out-prefix>}"
mkdir -p "$(dirname "$OUT")"
if [ -s "$PIDFILE" ]; then
  echo "telemetry already running; stop it before starting another capture" >&2
  exit 1
fi
: > "$PIDFILE"

{
  echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "hostname=$(hostname -f)"
  echo "kernel=$(uname -r)"
  nvidia-smi --query-gpu=name,uuid,driver_version,power.limit \
    --format=csv,noheader 2>/dev/null | sed 's/^/gpu=/'
} > "${OUT}.telemetry.meta"

# --- GPU ---------------------------------------------------------------
if nvidia-smi --query-gpu=clocks_event_reasons.sw_power_cap --format=csv >/dev/null 2>&1; then
  THROTTLE="clocks_event_reasons"
elif nvidia-smi --query-gpu=clocks_throttle_reasons.sw_power_cap --format=csv >/dev/null 2>&1; then
  THROTTLE="clocks_throttle_reasons"
else
  THROTTLE=""
fi

FIELDS="timestamp,index,clocks.sm,clocks.mem,temperature.gpu,power.draw,utilization.gpu,utilization.memory,memory.used"
if [ -n "$THROTTLE" ]; then
  FIELDS="$FIELDS,${THROTTLE}.sw_power_cap,${THROTTLE}.hw_thermal_slowdown,${THROTTLE}.sw_thermal_slowdown,${THROTTLE}.hw_slowdown"
fi

nvidia-smi --query-gpu="$FIELDS" --format=csv -l 1 > "${OUT}.gpu.csv" 2>&1 &
echo $! >> "$PIDFILE"

# DCGM adds SM occupancy and DRAM active (the HBM bandwidth proxy), which
# nvidia-smi cannot report. Optional — absent on most stock images.
if command -v dcgmi >/dev/null 2>&1; then
  dcgmi dmon -e 1002,1003,1005,1009,1011,1012 -d 1000 > "${OUT}.dcgm.csv" 2>&1 &
  echo $! >> "$PIDFILE"
fi

# --- Host --------------------------------------------------------------
if command -v mpstat >/dev/null 2>&1; then
  mpstat -P ALL 1 > "${OUT}.cpu.csv" 2>&1 &
  echo $! >> "$PIDFILE"
fi

if command -v iostat >/dev/null 2>&1; then
  iostat -x 1 > "${OUT}.disk.csv" 2>&1 &
  echo $! >> "$PIDFILE"
fi

( while true; do
    awk -v ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
      NR > 2 {
        gsub(/:/, "", $1)
        if ($1 != "lo") print ts, $0
      }' /proc/net/dev
    sleep 1
  done ) > "${OUT}.net.csv" 2>&1 &
echo $! >> "$PIDFILE"

echo "telemetry started -> ${OUT}.{gpu,cpu,disk,net}.csv  (throttle fields: ${THROTTLE:-NONE})"
