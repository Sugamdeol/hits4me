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

# ------------------------------------------------ fetch proxy list if requested
if [ -n "${BULK_ADD_PROXY_LIST_URL:-}" ] && [ -z "${BULK_ADD_PROXY_LIST:-}" ]; then
  fetch_err_file=$(mktemp 2>/dev/null || echo "/tmp/fetch_proxy_err.$$")
  if fetch_out=$(python3 "$SCRIPT_DIR/fetch_proxy_list.py" "$BULK_ADD_PROXY_LIST_URL" 2>"$fetch_err_file"); then
    BULK_ADD_PROXY_LIST="$fetch_out"
    BULK_ADD_PROXY_TYPE="${BULK_ADD_PROXY_TYPE:-socks5}"
    proxy_count=$(awk -F'|' '{print NF}' <<< "$BULK_ADD_PROXY_LIST")
    log "fetched $proxy_count proxies from BULK_ADD_PROXY_LIST_URL"
  else
    err_msg=$(cat "$fetch_err_file" 2>/dev/null || true)
    [ -z "$err_msg" ] && err_msg="$fetch_out"
    [ -z "$err_msg" ] && err_msg="unknown error"
    log "WARNING: failed to fetch proxy list from BULK_ADD_PROXY_LIST_URL ($err_msg)"
  fi
  rm -f "$fetch_err_file"
fi

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

# ---------------------------------------------------------- pool-closed check
if [[ "${EX_PROXY_SESSIONS:-0}" =~ ^[1-9][0-9]*$ ]] && [ -z "${EX_PROXY_URL:-}" ] && [ -z "${BULK_ADD_PROXY_LIST:-}" ]; then
  log "WARNING: EX_PROXY_SESSIONS is set (${EX_PROXY_SESSIONS}) but EX_PROXY_URL and BULK_ADD_PROXY_LIST are empty. The 9Hits public pool is CLOSED! Every pool session will fail with 'Pool error: The public pool is closed!'. Configure your own pool at https://dash.9hits.com/pool (EX_PROXY_URL) or use BULK_ADD_PROXY_LIST / BULK_ADD_PROXY_LIST_URL."
fi

# ----------------------------------------------------------- RAM sanity check
est_sessions=0
if [[ "${EX_PROXY_SESSIONS:-0}" =~ ^[1-9][0-9]*$ ]]; then
  est_sessions=$((est_sessions + EX_PROXY_SESSIONS))
fi

if [ -n "${BULK_ADD_PROXY_LIST:-}" ]; then
  bulk_proxies=$(awk -F'|' '{print NF}' <<< "$BULK_ADD_PROXY_LIST")
  est_sessions=$((est_sessions + bulk_proxies))
fi

case "${SYSTEM_SESSION:-}" in
  1|yes|true|on) est_sessions=$((est_sessions + 1)) ;;
esac

mem_total_mb=0
if [ -f /proc/meminfo ]; then
  mem_total_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
  mem_total_mb=$((mem_total_kb / 1024))
fi

if [ "$mem_total_mb" -gt 0 ] && [ "$mem_total_mb" -lt 1024 ] && [ "$est_sessions" -ge 4 ]; then
  log "WARNING: detected ${mem_total_mb} MB RAM with ~${est_sessions} estimated sessions. Instances with < 1024 MB RAM (e.g. Render free tier 512 MB) may experience OOM kills with 4+ sessions. Consider lowering the session count (5-6 is recommended maximum) or using a bigger instance."
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

# FeelingSurf runs in this same container/deployment. Keep it under a separate
# supervisor so either viewer can restart without taking down the other one.
feelingsurf_supervisor() {
  trap 'log "FeelingSurf supervisor stopped"; exit 0' TERM INT
  while :; do
    log "launching FeelingSurf viewer"
    runuser -u fsviewer -- "$SCRIPT_DIR/feelingsurf-run.sh" &
    local fpid=$!
    echo "$fpid" > /tmp/feelingsurf.pid
    wait "$fpid"
    local code=$?
    rm -f /tmp/feelingsurf.pid
    local restarts=0
    [ -f /tmp/feelingsurf.restarts ] && restarts=$(cat /tmp/feelingsurf.restarts 2>/dev/null || echo 0)
    echo $((restarts + 1)) > /tmp/feelingsurf.restarts
    log "FeelingSurf exited (code $code) - restarting in ${SUPERVISOR_DELAY:-10}s"
    sleep "${SUPERVISOR_DELAY:-10}" & wait $!
  done
}

case "${FEELINGSURF_ENABLED:-yes}" in
  0|no|false|off)
    export FEELINGSURF_SUPERVISOR_PID=0
    log "FeelingSurf disabled by FEELINGSURF_ENABLED=${FEELINGSURF_ENABLED}"
    ;;
  *)
    if [ -z "${access_token:-${ACCESS_TOKEN:-}}" ]; then
      log "WARNING: ACCESS_TOKEN is not set - FeelingSurf cannot authenticate"
    fi
    feelingsurf_supervisor &
    export FEELINGSURF_SUPERVISOR_PID=$!
    ;;
esac

log "9Hits supervisor pid=$SUPERVISOR_PID"
[ "${FEELINGSURF_SUPERVISOR_PID:-0}" -gt 1 ] && log "FeelingSurf supervisor pid=$FEELINGSURF_SUPERVISOR_PID"
log "combined health endpoint on 0.0.0.0:$PORT (GET /health)"
exec python3 "$SCRIPT_DIR/health_server.py"
