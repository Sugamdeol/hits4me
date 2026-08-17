#!/usr/bin/env bash
set -eu

# ============================================================
# Notesly front + FeelingSurf viewer.
#
# The public container port serves the Notesly notes website, so the
# SnapDeploy URL looks like a normal notes app. Meanwhile the FeelingSurf
# viewer keeps running headlessly on the container's IP, giving views, with
# its own internal health server on VIEWER_HEALTH_PORT.
# ============================================================

# FeelingSurf's official variable is lower-case. Also accept ACCESS_TOKEN so
# secrets can follow the more common upper-case naming convention.
if [ -z "${access_token:-}" ] && [ -n "${ACCESS_TOKEN:-}" ]; then
  export access_token="$ACCESS_TOKEN"
fi

if [ -z "${access_token:-}" ]; then
  echo "[notesly] ERROR: set the access_token secret in SnapDeploy." >&2
  exit 1
fi

PORT="${PORT:-3000}"
VIEWER_HEALTH_PORT="${VIEWER_HEALTH_PORT:-3100}"
SUPERVISOR_DELAY="${SUPERVISOR_DELAY:-10}"

for var in PORT VIEWER_HEALTH_PORT SUPERVISOR_DELAY; do
  value="${!var}"
  case "$value" in
    ''|*[!0-9]*)
      echo "[notesly] ERROR: $var must be a number (got '$value')." >&2
      exit 1
      ;;
  esac
done

if [ "$PORT" = "$VIEWER_HEALTH_PORT" ]; then
  echo "[notesly] ERROR: PORT and VIEWER_HEALTH_PORT must differ." >&2
  exit 1
fi

# Remove locks left behind if the platform restarts the process in-place.
rm -f /tmp/.X99-lock /tmp/notesly-worker.pid

# ------------------------------------------------------------
# 1) Notesly — the public notes website on the container port.
# ------------------------------------------------------------
python3 /notes_app/server.py &
notes_pid=$!
echo "[notesly] Notesly notes website started (pid $notes_pid) on 0.0.0.0:$PORT"

# ------------------------------------------------------------
# 2) Xvfb for the headless viewer.
# ------------------------------------------------------------
/usr/bin/Xvfb :99 -screen 0 1920x1080x24 -nolisten unix &
xvfb_pid=$!

sleep 1
export DISPLAY=:99

viewer_pid=""

cleanup() {
  trap - TERM INT
  if [ -n "$viewer_pid" ]; then
    kill -TERM "$viewer_pid" 2>/dev/null || true
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
  export healthcheck_port="$VIEWER_HEALTH_PORT"

  /usr/bin/FeelingSurfViewer \
    --disable-dev-shm-usage \
    --no-sandbox \
    --use-gl=angle \
    --use-angle=swiftshader &
  viewer_pid=$!
  echo "$viewer_pid" > /tmp/notesly-worker.pid
  echo "[notesly] worker session started (pid $viewer_pid); internal health port $VIEWER_HEALTH_PORT"

  if wait "$viewer_pid"; then
    rc=0
  else
    rc=$?
  fi
  viewer_pid=""

  restarts=$((restarts + 1))
  echo "[notesly] worker exited (rc=$rc); restarting in ${SUPERVISOR_DELAY}s (restart #$restarts)"
  sleep "$SUPERVISOR_DELAY"
done
