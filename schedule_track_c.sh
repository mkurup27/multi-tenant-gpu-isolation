#!/usr/bin/env bash
# Long-running, restart-safe scheduler. Install as a systemd service.
set -Eeuo pipefail

INSTALL_DIR="${ISOLATION_INSTALL_DIR:-/opt/isolation}"
STATE_DIR="${ISOLATION_STATE_DIR:-/var/lib/isolation}"
CONFIG="${ISOLATION_CONFIG:-/etc/isolation/track_c.env}"
INTERVAL_S="${TRACK_C_INTERVAL_S:-1800}"
WINDOW_TIMEOUT="${TRACK_C_WINDOW_TIMEOUT:-20m}"
mkdir -p "$STATE_DIR"

while true; do
  now="$(date +%s)"
  next="$(( (now / INTERVAL_S + 1) * INTERVAL_S ))"
  sleep "$((next - now))"

  scheduled_utc="$(date -u -d "@$next" +%Y-%m-%dT%H:%M:%SZ)"
  {
    echo "scheduled_utc=$scheduled_utc"
    echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$STATE_DIR/heartbeat"

  if flock -n "$STATE_DIR/window.lock" \
      timeout --signal=TERM --kill-after=60s "$WINDOW_TIMEOUT" \
      "$INSTALL_DIR/run_track_c_window.sh" "$CONFIG"; then
    echo "status=ok" >> "$STATE_DIR/heartbeat"
  else
    status=$?
    echo "status=failed:$status" >> "$STATE_DIR/heartbeat"
  fi
  echo "finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$STATE_DIR/heartbeat"
done
