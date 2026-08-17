#!/usr/bin/env bash
set -eu

# ============================================================
# Notesly front + background worker.
#
# The public container port serves the Notesly notes website, so the
# SnapDeploy URL looks like a normal notes app. A background worker session
# runs headlessly on the container's IP with its own loopback-only health
# server on WORKER_HEALTH_PORT.
# ============================================================

# The worker's authentication variable is lower-case; also accept the
# upper-case spelling so secrets can follow the common convention.
if [ -z "${access_token:-}" ] && [ -n "${ACCESS_TOKEN:-}" ]; then
  export access_token="$ACCESS_TOKEN"
fi

if [ -z "${access_token:-}" ]; then
  echo "[notesly] ERROR: set the access_token secret in SnapDeploy." >&2
  exit 1
fi

PORT="${PORT:-3000}"
WORKER_HEALTH_PORT="${WORKER_HEALTH_PORT:-3100}"
SUPERVISOR_DELAY="${SUPERVISOR_DELAY:-10}"

for var in PORT WORKER_HEALTH_PORT SUPERVISOR_DELAY; do
  value="${!var}"
  case "$value" in
    ''|*[!0-9]*)
      echo "[notesly] ERROR: $var must be a number (got '$value')." >&2
      exit 1
      ;;
  esac
done

if [ "$PORT" = "$WORKER_HEALTH_PORT" ]; then
  echo "[notesly] ERROR: PORT and WORKER_HEALTH_PORT must differ." >&2
  exit 1
fi

# Remove locks left behind if the platform restarts the process in-place, and
# reset the worker's neutral data directory so a stale profile lock can never
# block a relaunch.
rm -f /tmp/.X99-lock /tmp/notesly-worker.pid
rm -rf /tmp/notesly-worker-data

# ------------------------------------------------------------
# 1) Notesly — the public notes website on the container port.
# ------------------------------------------------------------
python3 /notes_app/server.py &
notes_pid=$!
echo "[notesly] Notesly notes website started (pid $notes_pid) on 0.0.0.0:$PORT"

# ------------------------------------------------------------
# 2) Xvfb for the headless worker.
# ------------------------------------------------------------
/usr/bin/Xvfb :99 -screen 0 1920x1080x24 -nolisten unix &
xvfb_pid=$!

sleep 1
export DISPLAY=:99

worker_pid=""

cleanup() {
  trap - TERM INT
  if [ -n "$worker_pid" ]; then
    kill -TERM "$worker_pid" 2>/dev/null || true
  fi
  kill -TERM "$notes_pid" "$xvfb_pid" 2>/dev/null || true
  wait 2>/dev/null || true
  exit 0
}
trap cleanup TERM INT

# ------------------------------------------------------------
# 3) Background worker supervisor loop — keeps the worker
#    running even if it crashes; the notes front stays up the
#    whole time. Log output stays generic on purpose.
# ------------------------------------------------------------
restarts=0
while true; do
  export healthcheck=true
  export healthcheck_port="$WORKER_HEALTH_PORT"

  /usr/bin/notesly-worker \
    --disable-dev-shm-usage \
    --no-sandbox \
    --use-gl=angle \
    --use-angle=swiftshader \
    --user-data-dir=/tmp/notesly-worker-data &
  worker_pid=$!
  echo "$worker_pid" > /tmp/notesly-worker.pid
  echo "[notesly] worker session started (pid $worker_pid); internal health port $WORKER_HEALTH_PORT"

  if wait "$worker_pid"; then
    rc=0
  else
    rc=$?
  fi
  worker_pid=""

  restarts=$((restarts + 1))
  echo "[notesly] worker exited (rc=$rc); restarting in ${SUPERVISOR_DELAY}s (restart #$restarts)"
  sleep "$SUPERVISOR_DELAY"
done
