#!/usr/bin/env bash
# =============================================================================
# Entrypoint for the 9Hits Viewer v6 + /health endpoint CONTAINER.
#
#   * Builds the /nh.sh argument list from environment variables
#     (ACCESS_KEY, SYSTEM_SESSION, ALLOW_CRYPTO, ... - see README.md), using
#     the shared viewer_config.py so the Docker and non-Docker paths agree.
#   * Supervises the viewer: it runs under a pseudo-TTY (Render and
#     `docker run -d` don't allocate a TTY, and the viewer exits without
#     one) and is relaunched automatically if it exits.
#   * Runs health_server.py in the foreground: GET /health on 0.0.0.0:$PORT.
#
# Any extra positional arguments (e.g. a start command set on Render or
# passed to `docker run`) are appended to /nh.sh as additional flags.
#
# NOT USING DOCKER? Use run_native.py instead - it does all of the above
# without a container, a daemon, root or systemd:
#     ACCESS_KEY=xxx python3 run_native.py --system-session
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
    export BULK_ADD_PROXY_LIST BULK_ADD_PROXY_TYPE
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
# viewer_config.py is the single source of truth for the env -> flag mapping
# (shared with run_native.py). It emits one NUL-delimited argument per entry so
# values containing spaces survive intact.
NH_ARGS=()
while IFS= read -r -d '' arg; do
  NH_ARGS+=("$arg")
done < <(python3 -c '
import sys, os
sys.path.insert(0, sys.argv[1])
import viewer_config
for a in viewer_config.build_config_args(include_nh_flags=True):
    sys.stdout.write(a + "\0")
' "$SCRIPT_DIR")

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
est_sessions=$(python3 -c '
import sys
sys.path.insert(0, sys.argv[1])
import viewer_config
print(viewer_config.estimate_sessions())
' "$SCRIPT_DIR")

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
      --access-key=*)          out+=("--access-key=****") ;;
      --bulk-add-proxy-list=*) out+=("--bulk-add-proxy-list=****") ;;
      --vnc-pw=*)              out+=("--vnc-pw=****") ;;
      *)                       out+=("$a") ;;
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
