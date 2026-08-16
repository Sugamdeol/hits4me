#!/usr/bin/env bash
# =============================================================================
# Entrypoint for the 9Hits Viewer v6 + FeelingSurf Viewer + /health container.
#
# 9Hits side (we drive the viewer directly - we do NOT call the opaque
# upstream /nh.sh anymore):
#
#   * The viewer (nhviewer) is extracted from the image's baked-in tarball at
#     BUILD time (see Dockerfile) into /opt/9hits. No multi-minute bzip2
#     extraction or download happens at container start (that stall was the
#     "Extracting 9hitsv6-linux64 ..." deadlock seen on weak free-tier CPUs).
#   * Xvfb provides a virtual display (:99 by default) - the v6 viewer needs
#     an X display even with --hide-browser=yes. Supervised: restarted if it
#     ever dies. (FeelingSurf uses its own :98.)
#   * Launch flow mirrors the official 9hits installer (9hitste/install):
#       1. init pass: `nhviewer <your config flags> --exit-on-init` applies
#          access key / sessions / limits, then exits (bounded by a timeout);
#       2. run pass:  `nhviewer --auto-start --in-loop --render-to-terminal
#          [--reset-interval=...]` runs supervised under a pseudo-TTY
#          (run_pty.py) with a watchdog that restarts a wedged (silent AND
#          zero-CPU) viewer.
#   * Set DEFAULT_DL to download a different viewer build at container start.
#
# Health endpoint: GET /health on 0.0.0.0:$PORT (health_server.py).
# Any extra positional arguments are forwarded to the nhviewer init pass.
# =============================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-10000}"

NH_DIR="${NH_DIR:-/opt/9hits}"
NH_BIN="${NH_BIN:-$NH_DIR/nhviewer}"
NH_DISPLAY="${NH_DISPLAY:-:99}"
NH_RESOLUTION="${NH_RESOLUTION:-auto}"
INIT_TIMEOUT="${INIT_TIMEOUT:-300}"
NH_WATCHDOG="${NH_WATCHDOG:-yes}"
NH_WATCHDOG_STUCK="${NH_WATCHDOG_STUCK:-600}"
NH_RENDER_TO_TERMINAL="${NH_RENDER_TO_TERMINAL:-yes}"

export PORT
export DISPLAY="$NH_DISPLAY"
export HOME="${HOME:-/root}"

log() { printf '[start] %s\n' "$*"; }

_yes() { case "${1:-}" in 1|yes|true|on) return 0 ;; *) return 1 ;; esac; }

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
# These are consumed by the nhviewer INIT pass (nhviewer <args> --exit-on-init).
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
kv hide-columns       "${HIDE_COLUMNS:-}"
flag SYSTEM_SESSION    system-session
flag CLEAR_ALL_SESSIONS clear-all-sessions

# Cap the browser disk cache by default (the viewer's own default is unlimited),
# exactly like the official installer does (200 MB unless CACHE_LIMIT is set).
NH_ARGS+=("--cache-limit=${CACHE_LIMIT:-209715200}")

# EXTRA_ARGS: raw space-separated extra viewer flags appended as-is.
if [ -n "${EXTRA_ARGS:-}" ]; then
  read -r -a extra_args <<< "$EXTRA_ARGS"
  NH_ARGS+=("${extra_args[@]}")
fi

# Extra positional args (Render start command / docker run args).
NH_ARGS+=("$@")

# -------------------------------------------- script-level (consumed locally)
# RESTART_DELAY was an nh.sh knob; here it is an alias for the supervisor delay.
SUPERVISOR_DELAY="${SUPERVISOR_DELAY:-${RESTART_DELAY:-10}}"
# INSTALL_DIR: the viewer is baked into the image at $NH_DIR at build time.
if [ -n "${INSTALL_DIR:-}" ] && [ "$INSTALL_DIR" != "$NH_DIR" ]; then
  log "NOTE: --install-dir is ignored; the viewer is baked into the image at $NH_DIR (override with NH_DIR at your own risk)."
fi
# RE_INSTALL only made sense when the viewer was extracted at runtime.
if _yes "${RE_INSTALL:-}" && [ -z "${DEFAULT_DL:-}" ]; then
  log "NOTE: RE_INSTALL is a no-op now (the viewer is extracted at build time). Use DEFAULT_DL=<url> to swap builds."
fi

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

_yes "${SYSTEM_SESSION:-}" && est_sessions=$((est_sessions + 1))

mem_total_mb=0
if [ -f /proc/meminfo ]; then
  mem_total_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
  mem_total_mb=$((mem_total_kb / 1024))
fi

if [ "$mem_total_mb" -gt 0 ] && [ "$mem_total_mb" -lt 1024 ] && [ "$est_sessions" -ge 4 ]; then
  log "WARNING: detected ${mem_total_mb} MB RAM with ~${est_sessions} estimated sessions. Instances with < 1024 MB RAM (e.g. Render free tier 512 MB) may experience OOM kills with 4+ sessions. Consider lowering the session count (5-6 is recommended maximum) or using a bigger instance."
fi
if [ "$mem_total_mb" -gt 0 ] && [ "$mem_total_mb" -lt 2048 ]; then
  log "NOTE: 9Hits v6 officially recommends >= 2 GB RAM per instance; this box has ~${mem_total_mb} MB. If sessions crash-loop, disable FeelingSurf (FEELINGSURF_ENABLED=no), run fewer sessions, or use a bigger instance."
fi

# ------------------------------------------------------- redacted arg echo
display_args() {
  local out=() a
  for a in "${NH_ARGS[@]}"; do
    case "$a" in
      --access-key=*)          out+=("--access-key=****") ;;
      --bulk-add-proxy-list=*) out+=("--bulk-add-proxy-list=****") ;;
      *)                       out+=("$a") ;;
    esac
  done
  echo "${out[*]}"
}

# --------------------------------------------- best-effort /dev/shm enlargen
# The v6 viewer's Chromium loves a big /dev/shm (upstream recommends running
# with --shm-size=2g). Render & friends don't expose that knob, so try to
# remount it ourselves; no-op (with a log line) where not permitted.
fix_shm() {
  local cur
  cur=$(df -m /dev/shm 2>/dev/null | awk 'NR==2 {print $2}')
  if [ -n "${cur:-}" ] && [ "$cur" -lt 512 ]; then
    if mount -o remount,size=1g /dev/shm 2>/dev/null; then
      log "enlarged /dev/shm to 1g"
    else
      log "NOTE: /dev/shm is only ${cur:-?} MB and cannot be resized here (no CAP_SYS_ADMIN). Where supported, run with --shm-size=2g (already set in docker-compose.yml)."
    fi
  fi
}

# ------------------------------------------------------------- Xvfb display
pick_resolution() {
  if [ "$NH_RESOLUTION" != "auto" ]; then
    echo "$NH_RESOLUTION"; return
  fi
  local cores
  cores=$(nproc 2>/dev/null || echo 1)
  if [ "$cores" -ge 4 ] && [ "$mem_total_mb" -ge 4000 ]; then
    echo "2560x1440x24"
  else
    echo "1920x1080x24"
  fi
}

xvfb_supervisor() {
  trap 'log "Xvfb supervisor stopped"; exit 0' TERM INT
  if ! command -v Xvfb >/dev/null 2>&1; then
    # Runtime without our Docker image: wait quietly instead of crash-looping.
    log "Xvfb not installed on this runtime - 9Hits display supervisor disabled"
    while :; do sleep 300 & wait $!; done
  fi
  local dnum="${NH_DISPLAY#:}"; dnum="${dnum%%.*}"
  local res; res=$(pick_resolution)
  while :; do
    rm -f "/tmp/.X${dnum}-lock" "/tmp/.X11-unix/X${dnum}" 2>/dev/null || true
    log "starting Xvfb $NH_DISPLAY ($res) for 9Hits"
    Xvfb "$NH_DISPLAY" -screen 0 "$res" -nolisten tcp &
    local xpid=$!
    echo "$xpid" > /tmp/xvfb.pid
    wait "$xpid"
    local code=$?
    rm -f /tmp/xvfb.pid
    log "Xvfb exited (code $code) - restarting in 3s"
    sleep 3 & wait $!
  done
}

wait_display() {
  local i
  for i in $(seq 1 60); do
    if command -v xdpyinfo >/dev/null 2>&1; then
      xdpyinfo -display "$NH_DISPLAY" >/dev/null 2>&1 && return 0
    else
      [ -S "/tmp/.X11-unix/X${NH_DISPLAY#:}" ] && return 0
    fi
    sleep 0.5
  done
  log "WARNING: X display $NH_DISPLAY not ready after 30s (continuing anyway)"
  return 1
}

# --------------------------------------------------------------------- VNC
vnc_supervisor() {
  trap 'log "VNC supervisor stopped"; exit 0' TERM INT
  local port="${VNC_PORT:-5901}" auth_args=()
  if [ -n "${VNC_PW:-}" ]; then
    mkdir -p "$HOME/.x11vnc"
    x11vnc -storepasswd "$VNC_PW" "$HOME/.x11vnc/passwd" >/dev/null 2>&1
    chmod 600 "$HOME/.x11vnc/passwd"
    auth_args=(-rfbauth "$HOME/.x11vnc/passwd")
  elif _yes "${NO_VNC_PW:-}"; then
    auth_args=(-nopw)
    log "WARNING: VNC has NO password (--no-vnc-pw). Anyone can watch/control the viewer display."
  else
    VNC_PW=$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom 2>/dev/null | head -c 8)
    mkdir -p "$HOME/.x11vnc"
    x11vnc -storepasswd "$VNC_PW" "$HOME/.x11vnc/passwd" >/dev/null 2>&1
    chmod 600 "$HOME/.x11vnc/passwd"
    auth_args=(-rfbauth "$HOME/.x11vnc/passwd")
    log "VNC auto-generated password: $VNC_PW  (set your own with VNC_PW=...)"
  fi
  while :; do
    log "starting x11vnc on port $port (mirroring $NH_DISPLAY)"
    x11vnc -display "$NH_DISPLAY" -rfbport "$port" -forever -shared -noxdamage -quiet "${auth_args[@]}" &
    local vpid=$!
    wait "$vpid"
    log "x11vnc exited (code $?) - restarting in 3s"
    sleep 3 & wait $!
  done
}

# ------------------------------------ optional runtime viewer re-download
# Old nh.sh --default-dl semantics: fetch a different viewer build at start
# (e.g. to test a newer build without rebuilding the image).
maybe_update_viewer() {
  [ -n "${DEFAULT_DL:-}" ] || return 0
  local target="/tmp/nhviewer-download" src="" sub=""
  log "DEFAULT_DL set - downloading viewer from $DEFAULT_DL"
  if wget -q --tries=3 --timeout=60 -O "$target" "$DEFAULT_DL"; then
    rm -rf /tmp/nh.new
    mkdir -p /tmp/nh.new
    if tar -xf "$target" -C /tmp/nh.new 2>/dev/null; then
      # locate nhviewer: either at the root or inside one wrapper directory
      if [ -f /tmp/nh.new/nhviewer ]; then
        src=/tmp/nh.new
      else
        sub=$(find /tmp/nh.new -mindepth 1 -maxdepth 1 -type d | head -n 1)
        if [ -n "$sub" ] && [ -f "$sub/nhviewer" ]; then
          src="$sub"
        fi
      fi
    fi
  fi
  rm -f "$target"
  if [ -z "$src" ]; then
    log "WARNING: DEFAULT_DL download/extract failed - keeping the baked-in viewer"
    rm -rf /tmp/nh.new
    return 1
  fi
  rm -rf "${NH_DIR}.old"
  if [ -d "$NH_DIR" ]; then
    mv "$NH_DIR" "${NH_DIR}.old"
  fi
  if [ "$src" != /tmp/nh.new ]; then
    mv "$src" "$NH_DIR"
    rm -rf /tmp/nh.new
  else
    mv /tmp/nh.new "$NH_DIR"
  fi
  chmod -R a+rwX "$NH_DIR"
  chmod +x "$NH_BIN" 2>/dev/null || true
  rm -rf "${NH_DIR}.old"
  log "viewer replaced from DEFAULT_DL"
}

# ----------------------------------------------------------------- viewer
# Init pass: apply cmdline settings/sessions to the persisted config and exit.
# Bounded by INIT_TIMEOUT so a hung API call can never wedge the container.
run_init_pass() {
  log "init pass: nhviewer $(display_args) --exit-on-init"
  (
    cd "$NH_DIR" 2>/dev/null || exit 1
    timeout --kill-after=15s "${INIT_TIMEOUT}s" \
      "$NH_BIN" "${NH_ARGS[@]}" --exit-on-init
  )
}

nh_supervisor() {
  trap 'log "9Hits supervisor stopped"; exit 0' TERM INT

  maybe_update_viewer

  if [ ! -x "$NH_BIN" ]; then
    # e.g. Hugging Face Gradio Spaces: the app runs in HF's own Python
    # runtime and never builds our Docker image, so the viewer binary
    # simply does not exist. Wait quietly instead of hot restart-looping.
    log "ERROR: $NH_BIN is missing - 9Hits cannot start on this runtime (use the Docker image). Checking again every 5 min."
    while [ ! -x "$NH_BIN" ]; do
      echo "down" > /tmp/viewer.state
      sleep 300 & wait $!
    done
  fi

  wait_display || true

  local run_args=(--auto-start --in-loop)
  _yes "$NH_RENDER_TO_TERMINAL" && run_args+=(--render-to-terminal)
  [ -n "${RESET_INTERVAL:-}" ] && run_args+=("--reset-interval=${RESET_INTERVAL}")
  local stuck=0
  _yes "$NH_WATCHDOG" && stuck="$NH_WATCHDOG_STUCK"

  while :; do
    wait_display || true

    echo "init" > /tmp/viewer.state
    local attempt rc init_ok=0
    for attempt in 1 2 3; do
      run_init_pass
      rc=$?
      if [ "$rc" -eq 0 ]; then
        init_ok=1
        break
      fi
      log "WARNING: 9Hits init pass failed/timed out (attempt $attempt/3, code $rc) - retrying in ${SUPERVISOR_DELAY}s"
      sleep "${SUPERVISOR_DELAY}" & wait $!
      wait_display || true
    done
    [ "$init_ok" -ne 1 ] && log "WARNING: init pass failed 3x - launching run pass anyway (will re-init on next restart)"

    echo "run" > /tmp/viewer.state
    touch /tmp/viewer.lastoutput 2>/dev/null || true
    log "launching 9Hits viewer: nhviewer ${run_args[*]}  (config: $(display_args))"
    (
      cd "$NH_DIR" 2>/dev/null || exit 1
      exec python3 "$SCRIPT_DIR/run_pty.py" \
        --heartbeat-file /tmp/viewer.lastoutput \
        --watchdog-stuck "$stuck" \
        -- "$NH_BIN" "${run_args[@]}"
    ) &
    local vpid=$!
    echo "$vpid" > /tmp/viewer.pid
    wait "$vpid"
    local code=$?
    rm -f /tmp/viewer.pid
    echo "down" > /tmp/viewer.state
    local restarts=0
    [ -f /tmp/viewer.restarts ] && restarts=$(cat /tmp/viewer.restarts 2>/dev/null || echo 0)
    echo $((restarts + 1)) > /tmp/viewer.restarts
    log "9Hits viewer exited (code $code) - restarting in ${SUPERVISOR_DELAY}s"
    # `wait` is interruptible by the TERM/INT trap above; a plain `sleep`
    # would delay shutdown.
    sleep "${SUPERVISOR_DELAY}" & wait $!
  done
}

fix_shm

xvfb_supervisor &
export XVFB_SUPERVISOR_PID=$!

nh_supervisor &
export SUPERVISOR_PID=$!

VNC_SUPERVISOR_PID=0
if _yes "${VNC:-}" || [ -n "${VNC_PW:-}" ] || _yes "${NO_VNC_PW:-}"; then
  vnc_supervisor &
  VNC_SUPERVISOR_PID=$!
fi
export VNC_SUPERVISOR_PID

# FeelingSurf runs in this same container/deployment. Keep it under a separate
# supervisor so either viewer can restart without taking down the other one.
feelingsurf_supervisor() {
  trap 'log "FeelingSurf supervisor stopped"; exit 0' TERM INT
  while :; do
    log "launching FeelingSurf viewer"
    if [ "$(id -u)" = "0" ] && id fsviewer >/dev/null 2>&1; then
      runuser -u fsviewer -- "$SCRIPT_DIR/feelingsurf-run.sh" &
    else
      # Non-root runtime (e.g. Hugging Face Gradio Spaces): run directly.
      "$SCRIPT_DIR/feelingsurf-run.sh" &
    fi
    local fpid=$!
    echo "$fpid" > /tmp/feelingsurf.pid
    wait "$fpid"
    local code=$?
    rm -f /tmp/feelingsurf.pid
    local restarts=0
    [ -f /tmp/feelingsurf.restarts ] && restarts=$(cat /tmp/feelingsurf.restarts 2>/dev/null || echo 0)
    echo $((restarts + 1)) > /tmp/feelingsurf.restarts
    log "FeelingSurf exited (code $code) - restarting in ${SUPERVISOR_DELAY}s"
    sleep "${SUPERVISOR_DELAY}" & wait $!
  done
}

case "${FEELINGSURF_ENABLED:-yes}" in
  0|no|false|off)
    export FEELINGSURF_SUPERVISOR_PID=0
    log "FeelingSurf disabled by FEELINGSURF_ENABLED=${FEELINGSURF_ENABLED}"
    ;;
  *)
    if [ ! -x /usr/bin/FeelingSurfViewer ]; then
      # Runtime without our Docker image (e.g. HF Gradio Spaces): skip quietly.
      export FEELINGSURF_ENABLED=no FEELINGSURF_SUPERVISOR_PID=0
      log "FeelingSurf binary not installed on this runtime - disabling FeelingSurf"
    else
      if [ -z "${access_token:-${ACCESS_TOKEN:-}}" ]; then
        log "WARNING: ACCESS_TOKEN is not set - FeelingSurf cannot authenticate"
      fi
      feelingsurf_supervisor &
      export FEELINGSURF_SUPERVISOR_PID=$!
    fi
    ;;
esac

log "9Hits supervisor pid=$SUPERVISOR_PID (Xvfb $NH_DISPLAY pid=$XVFB_SUPERVISOR_PID)"
[ "${FEELINGSURF_SUPERVISOR_PID:-0}" -gt 1 ] && log "FeelingSurf supervisor pid=$FEELINGSURF_SUPERVISOR_PID"
[ "$VNC_SUPERVISOR_PID" -gt 1 ] && log "VNC supervisor pid=$VNC_SUPERVISOR_PID (port ${VNC_PORT:-5901})"
log "combined health endpoint on 0.0.0.0:$PORT (GET /health)"
exec python3 "$SCRIPT_DIR/health_server.py"
