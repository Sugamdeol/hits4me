#!/usr/bin/env bash
# Run FeelingSurf in the same container as 9Hits, on its own X display.
set -u

DISPLAY_NUMBER="${FEELINGSURF_DISPLAY:-:98}"
export DISPLAY="$DISPLAY_NUMBER"
export healthcheck=true
export healthcheck_port="${FEELINGSURF_PORT:-3000}"

# The official image accepts the lower-case name. ACCESS_TOKEN is the
# deployment-friendly alias used by this repository.
export access_token="${access_token:-${ACCESS_TOKEN:-}}"

rm -f "/tmp/.X${DISPLAY_NUMBER#:}-lock"
/usr/bin/Xvfb "$DISPLAY_NUMBER" -screen 0 1920x1080x24 -nolisten unix &
xvfb_pid=$!
viewer_pid=0

cleanup() {
  if [ "$viewer_pid" -gt 1 ]; then
    kill -TERM "$viewer_pid" 2>/dev/null || true
  fi
  kill -TERM "$xvfb_pid" 2>/dev/null || true
  wait "$viewer_pid" 2>/dev/null || true
  wait "$xvfb_pid" 2>/dev/null || true
}
trap 'cleanup; exit 0' TERM INT EXIT

sleep 1
/usr/bin/FeelingSurfViewer \
  --disable-dev-shm-usage \
  --no-sandbox \
  --use-gl=angle \
  --use-angle=swiftshader &
viewer_pid=$!
wait "$viewer_pid"
exit_code=$?
trap - EXIT
cleanup
exit "$exit_code"
