#!/usr/bin/env bash
# =============================================================================
# Entrypoint for the 9Hits Viewer v6 + /health endpoint container.
#
#   * Builds the /nh.sh argument list from environment variables
#     (ACCESS_KEY, SYSTEM_SESSION, ALLOW_CRYPTO, ... - see README.md).
#   * Supervises the viewer: it runs under a pseudo-TTY (Render and
#     `docker run -d` don't allocate a TTY, and the viewer exits without
#     one) and is relaunched automatically if it exits.
#   * Runs health_server.py in the foreground: GET /health on 0.0.0.0:$PORT.
#
# Any extra positional arguments (e.g. a start command set on Render or
# passed to `docker run`) are appended to /nh.sh as additional flags.
# =============================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-10000}"
NH_SCRIPT="${NH_SCRIPT:-/nh.sh}"
export PORT

log() { printf '[start] %s\n' "$*"; }

# ---------------------------------------------------------------- arguments
NH_ARGS=()
kv()   { [ -n "${2:-}" ] && NH_ARGS+=("--$1=$2"); }
flag() {
  case "${!1:-}" in
    1|yes|true|on) NH_ARGS+=("--$2") ;;
  esac
}

kv access-key         "${ACCESS_KEY:-}"
kv allow-popups       "${ALLOW_POPUPS:-}"
kv allow-adult        "${ALLOW_ADULT:-}"
kv allow-crypto       "${ALLOW_CRYPTO:-}"
kv hide-browser       "${HIDE_BROWSER:-}"
kv ex-proxy-sessions  "${EX_PROXY_SESSIONS:-}"
kv ex-proxy-url       "${EX_PROXY_URL:-}"
kv bulk-add-proxy-list "${BULK_ADD_PROXY_LIST:-}"
kv bulk-add-proxy-type "${BULK_ADD_PROXY_TYPE:-}"
kv session-note       "${SESSION_NOTE:-}"
kv note               "${NOTE:-}"
kv cache-path         "${CACHE_PATH:-}"
kv cache-limit        "${CACHE_LIMIT:-}"
kv hide-columns       "${HIDE_COLUMNS:-}"
kv install-dir        "${INSTALL_DIR:-}"
kv default-dl         "${DEFAULT_DL:-}"
kv restart-delay      "${RESTART_DELAY:-}"
kv reset-interval     "${RESET_INTERVAL:-}"
kv vnc-pw             "${VNC_PW:-}"
kv vnc-port           "${VNC_PORT:-}"
flag SYSTEM_SESSION    system-session
flag CLEAR_ALL_SESSIONS clear-all-sessions
flag RE_INSTALL        re-install
flag VNC               vnc
flag NO_VNC_PW         no-vnc-pw

# EXTRA_ARGS: raw space-separated extra flags appended as-is.
if [ -n "${EXTRA_ARGS:-}" ]; then
  read -r -a extra_args <<< "$EXTRA_ARGS"
  NH_ARGS+=("${extra_args[@]}")
fi

# Extra positional args (Render start command / docker run args).
NH_ARGS+=("$@")

if [ -z "${ACCESS_KEY:-}" ]; then
  log "WARNING: ACCESS_KEY is not set - the viewer will report 'User not found!'"
fi

# ------------------------------------------------------- redacted arg echo
display_args() {
  local out=() a
  for a in "${NH_ARGS[@]}"; do
    case "$a" in
      --access-key=*)        out+=("--access-key=****") ;;
      --bulk-add-proxy-list=*) out+=("--bulk-add-proxy-list=****") ;;
      --vnc-pw=*)            out+=("--vnc-pw=****") ;;
      *)                     out+=("$a") ;;
    esac
  done
  echo "${out[*]}"
}

# ----------------------------------------------------------------- supervisor
# Keep the viewer alive: relaunch it whenever it exits (crash, --reset-interval,
# manual stop, ...). The health server reports the current state via
# /tmp/viewer.pid and /tmp/viewer.restarts.
supervisor() {
  trap 'log "supervisor stopped"; exit 0' TERM INT
  while :; do
    log "launching: $NH_SCRIPT $(display_args)"
    python3 "$SCRIPT_DIR/run_pty.py" "$NH_SCRIPT" "${NH_ARGS[@]}" &
    local vpid=$!
    echo "$vpid" > /tmp/viewer.pid
    wait "$vpid"
    local code=$?
    rm -f /tmp/viewer.pid
    local restarts=0
    [ -f /tmp/viewer.restarts ] && restarts=$(cat /tmp/viewer.restarts 2>/dev/null || echo 0)
    echo $((restarts + 1)) > /tmp/viewer.restarts
    log "viewer exited (code $code) - restarting in ${SUPERVISOR_DELAY:-10}s"
    # `wait` is interruptible by the TERM/INT trap above; a plain `sleep`
    # would delay shutdown.
    sleep "${SUPERVISOR_DELAY:-10}" & wait $!
  done
}

supervisor &
export SUPERVISOR_PID=$!

log "9hits viewer supervisor pid=$SUPERVISOR_PID"
log "health endpoint on 0.0.0.0:$PORT (GET /health)"
exec python3 "$SCRIPT_DIR/health_server.py"
