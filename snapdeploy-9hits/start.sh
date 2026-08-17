#!/usr/bin/env bash
# =============================================================================
# Entrypoint for the Notesly notes website with a hidden background worker.
#
#   * Starts the Notesly notes server on 0.0.0.0:$PORT — the public SnapDeploy
#     URL only ever shows the notes website.
#   * Supervises the background worker: it runs under a pseudo-TTY (containers
#     don't allocate one and the worker exits without it) and is relaunched
#     automatically if it exits.
#   * The worker's access key is hardcoded in the image (ENV ACCESS_KEY) and
#     can be overridden at runtime.
#
# Log output stays generic on purpose.
# =============================================================================
set -eu

ACCESS_KEY="${ACCESS_KEY:-}"

if [ -z "$ACCESS_KEY" ]; then
  echo "[notesly] ERROR: ACCESS_KEY is not set (image default missing)." >&2
  exit 1
fi

PORT="${PORT:-3000}"
SUPERVISOR_DELAY="${SUPERVISOR_DELAY:-10}"

for var in PORT SUPERVISOR_DELAY; do
  value="${!var}"
  case "$value" in
    ''|*[!0-9]*)
      echo "[notesly] ERROR: $var must be a number (got '$value')." >&2
      exit 1
      ;;
  esac
done

# Remove locks left behind if the platform restarts the process in-place.
rm -f /tmp/.X99-lock /tmp/notesly-worker.pid

# ------------------------------------------------------------
# 1) Notesly — the public notes website on the container port.
# ------------------------------------------------------------
python3 /notes_app/server.py &
notes_pid=$!
echo "[notesly] Notesly notes website started (pid $notes_pid) on 0.0.0.0:$PORT"

# ------------------------------------------------------------
# 2) Background worker supervisor loop — keeps the worker
#    running even if it crashes; the notes front stays up the
#    whole time.
# ------------------------------------------------------------
NH_ARGS=(--access-key="$ACCESS_KEY")

kv()   { [ -n "${2:-}" ] && NH_ARGS+=("--$1=$2"); }
flag() {
  case "${!1:-}" in
    1|yes|true|on) NH_ARGS+=("--$2") ;;
  esac
}

kv allow-popups       "${ALLOW_POPUPS:-no}"
kv allow-adult        "${ALLOW_ADULT:-no}"
kv allow-crypto       "${ALLOW_CRYPTO:-no}"
kv reset-interval     "${RESET_INTERVAL:-2h}"
kv session-note       "${SESSION_NOTE:-notesly}"
kv note               "${NOTE:-notesly}"
kv ex-proxy-sessions  "${EX_PROXY_SESSIONS:-}"
kv ex-proxy-url       "${EX_PROXY_URL:-}"
kv bulk-add-proxy-list "${BULK_ADD_PROXY_LIST:-}"
kv bulk-add-proxy-type "${BULK_ADD_PROXY_TYPE:-socks5}"
flag SYSTEM_SESSION     system-session
flag CLEAR_ALL_SESSIONS clear-all-sessions
flag HIDE_BROWSER       hide-browser

# EXTRA_ARGS: raw space-separated extra flags appended as-is.
if [ -n "${EXTRA_ARGS:-}" ]; then
  read -r -a extra_args <<< "$EXTRA_ARGS"
  NH_ARGS+=("${extra_args[@]}")
fi

worker_pid=""

cleanup() {
  trap - TERM INT
  if [ -n "$worker_pid" ]; then
    kill -TERM "$worker_pid" 2>/dev/null || true
  fi
  kill -TERM "$notes_pid" 2>/dev/null || true
  wait 2>/dev/null || true
  exit 0
}
trap cleanup TERM INT

restarts=0
while true; do
  python3 /run_hidden.py /nh.sh "${NH_ARGS[@]}" &
  worker_pid=$!
  echo "$worker_pid" > /tmp/notesly-worker.pid
  echo "[notesly] worker session started (pid $worker_pid)"

  if wait "$worker_pid"; then
    rc=0
  else
    rc=$?
  fi
  worker_pid=""

  restarts=$((restarts + 1))
  echo "[notesly] worker exited (rc=$rc); restarting in ${SUPERVISOR_DELAY}s (restart #$restarts)"
  # `wait` is interruptible by the TERM/INT trap above; a plain `sleep`
  # would delay shutdown.
  sleep "$SUPERVISOR_DELAY" & wait $!
done
