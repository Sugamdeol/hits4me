#!/usr/bin/env bash
set -eu

# FeelingSurf's official variable is lower-case. Also accept ACCESS_TOKEN so
# secrets can follow the more common upper-case naming convention.
if [ -z "${access_token:-}" ] && [ -n "${ACCESS_TOKEN:-}" ]; then
  export access_token="$ACCESS_TOKEN"
fi

if [ -z "${access_token:-}" ]; then
  echo "[feelingsurf] ERROR: set the access_token secret in SnapDeploy." >&2
  exit 1
fi

PORT="${PORT:-3000}"
case "$PORT" in
  ''|*[!0-9]*)
    echo "[feelingsurf] ERROR: PORT must be a number." >&2
    exit 1
    ;;
esac

# Remove a lock left behind if the platform restarts the process in-place.
rm -f /tmp/.X99-lock

/usr/bin/Xvfb :99 -screen 0 1920x1080x24 -nolisten unix &
xvfb_pid=$!

cleanup() {
  kill -TERM "$xvfb_pid" 2>/dev/null || true
}
trap cleanup EXIT TERM INT

sleep 1
export DISPLAY=:99
export healthcheck=true
export healthcheck_port="$PORT"

echo "[feelingsurf] starting viewer; health endpoint is on 0.0.0.0:$PORT"

/usr/bin/FeelingSurfViewer \
  --disable-dev-shm-usage \
  --no-sandbox \
  --use-gl=angle \
  --use-angle=swiftshader &
viewer_pid=$!

# Keeping the shell as PID 1 lets it clean up Xvfb and forward termination.
trap 'kill -TERM "$viewer_pid" 2>/dev/null || true; cleanup' TERM INT
wait "$viewer_pid"
