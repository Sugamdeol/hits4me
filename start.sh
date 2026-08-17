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
# 9Hits stays OFF by default (NINEHITS_ENABLED=no) so a bare deploy never
# surprises anyone. BUT both viewers can now share a 512 MB free instance
# (e.g. Render free) via three cooperating layers:
#
#   1. LOW_MEMORY Chromium flags auto-applied on < 1 GB boxes
#      (--renderer-process-limit=1, --enable-low-end-device-mode,
#      --memory-model=low, V8 heap caps, caches off, no GPU process, ...) -
#      see NH_MEM_FLAGS / FS_MEM_FLAGS below.
#   2. memguard.py - a memory guardian that watches total RSS against the
#      cgroup limit and restarts the HEAVIEST viewer when the pair would
#      otherwise exceed it, so the platform never OOM-kills the container.
#   3. DUAL_VIEWER_MODE=time-slice / auto - if the box really cannot fit both
#      simultaneously, memguard alternates them (TIME_SLICE seconds each) so
#      only one Chromium is resident at a time (~50% uptime each, but NO OOM).
#
# Set NINEHITS_ENABLED=yes + DUAL_VIEWER_MODE=auto (as render.yaml / koyeb.yaml
# already do) to run BOTH viewers on the free 512 MB plan. On >= 2 GB hosts
# everything just runs concurrently and the guardian never intervenes.
#
# Health endpoint: GET /health on 0.0.0.0:$PORT (health_server.py).
# Any extra positional arguments are forwarded to the nhviewer init pass.
# =============================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-10000}"

# -------------------------------------------------------------------- modes
# Two runtime modes, picked by the first positional argument:
#
#   (no args, the legacy default)
#       start.sh is the container ENTRYPOINT. It starts every supervisor
#       inline (Xvfb, 9Hits, FeelingSurf, memguard) and execs the health
#       server in the foreground. This mode is what every deployment
#       platform sees today; nothing about it has changed.
#
#   ninehits-only
#       start.sh is the command for the 9Hits slot of the new
#       ``supervisor.py`` process manager. In this mode it only runs the
#       9Hits-specific chain (Xvfb, 9Hits init+run passes via run_pty.py,
#       optional VNC) and stays alive as long as the 9Hits supervisor is
#       alive. When the 9Hits supervisor dies, start.sh exits - the Python
#       supervisor detects the exit, waits for the cooldown, and restarts
#       ``start.sh ninehits-only`` from scratch. This is what lets one
#       viewer's crash NOT take down the other one.
#
# Detection is intentionally simple: any first arg equal to "ninehits-only".
# Extra args after the mode word are forwarded to the nhviewer init pass
# (matches the legacy behaviour, so existing ``docker run ... /start.sh
# --foo=bar`` commands keep working).
RUN_MODE="legacy"
case "${1:-}" in
  ninehits-only)
    RUN_MODE="ninehits-only"
    shift
    ;;
esac

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
# glibc arena bloat control + aggressive heap return-to-OS - applies to both
# Chromium-based viewers (and to Xvfb / python / bash). The trim threshold
# makes glibc give freed pages back to the kernel instead of hoarding them.
export MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"
export MALLOC_TRIM_THRESHOLD_="${MALLOC_TRIM_THRESHOLD_:-65536}"
export MALLOC_MMAP_THRESHOLD_="${MALLOC_MMAP_THRESHOLD_:-131072}"

log() { printf '[start] %s\n' "$*"; }

_yes() { case "${1:-}" in 1|yes|true|on) return 0 ;; *) return 1 ;; esac; }

# ---------------------------------------------------------------- memory size
# Prefer the cgroup limit (Render free = 512 MB) over /proc/meminfo, which in
# containers often shows the HOST's RAM and would lie about what we can use.
detect_mem_limit_mb() {
  local v="" path
  for path in /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory/memory.limit_in_bytes; do
    [ -f "$path" ] || continue
    v=$(cat "$path" 2>/dev/null || echo "")
    case "$v" in
      ""|max) v="" ;;
      *[!0-9]*) v="" ;;
    esac
    # Ignore absurd "no limit" values (>= 1 TiB) and fall through to MemTotal.
    if [ -n "$v" ] && [ "$v" -ge 1099511627776 ] 2>/dev/null; then v=""; fi
    [ -n "$v" ] && { echo $((v / 1024 / 1024)); return 0; }
  done
  local kb=0
  [ -f /proc/meminfo ] && kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
  echo $((kb / 1024))
}
MEM_LIMIT_MB=$(detect_mem_limit_mb)
export MEM_LIMIT_MB

# --------------------------------------------------------- 512MB dual-viewer
# Knobs that let BOTH 9Hits and FeelingSurf share one small instance without
# the platform OOM-killing the container:
#
#   DUAL_VIEWER_MODE
#     auto        start both together; escalate to time-slice if RAM proves
#                 too small (2+ over-limit restarts inside 10 min)
#     concurrent  always run both; memguard restarts the heaviest viewer when
#                 total RSS crosses MEMGUARD_HARD_PCT of the limit
#     time-slice  alternate the viewers every TIME_SLICE seconds (one Chromium
#                 resident at a time - guaranteed to fit 512 MB, ~50% uptime)
#     off         legacy: no memguard, no slicing (two viewers may OOM small
#                 plans - you were warned)
#   LOW_MEMORY
#     auto        apply Chromium memory-shrinking flags only on < 1 GB boxes
#     balanced    always apply the balanced flags     off: never
#     extreme     balanced + --single-process for BOTH viewers (~324 MB for
#                 the pair - lets both run CONCURRENTLY in 512 MB). Per-viewer
#                 toggles NH_SP / FS_SP (default yes) + crash auto-fallback:
#                 if a viewer crash-loops 3x at startup, its SP flags are
#                 dropped automatically so it keeps running (slower, safer).
#                 FeelingSurf GL: FS_GL_MODE=swiftshader (upstream default) or
#                 disable-gpu (only if swiftshader misbehaves under SP).
#   TIME_SLICE    seconds per viewer turn in time-slice mode (default 1500=25m)
DUAL_VIEWER_MODE="${DUAL_VIEWER_MODE:-auto}"
TIME_SLICE="${TIME_SLICE:-1500}"
LOW_MEMORY="${LOW_MEMORY:-auto}"
CREATE_SWAP="${CREATE_SWAP:-}"     # e.g. "256M"; auto-tried on small boxes
NH_SP="${NH_SP:-yes}"              # single-process for 9Hits in extreme mode
FS_SP="${FS_SP:-yes}"              # single-process for FeelingSurf in extreme
FS_GL_MODE="${FS_GL_MODE:-swiftshader}"   # swiftshader | disable-gpu
export DUAL_VIEWER_MODE TIME_SLICE LOW_MEMORY CREATE_SWAP NH_SP FS_SP FS_GL_MODE

# Decide whether the low-memory Chromium flag set applies.
LOW_MEM_ON=0
case "$LOW_MEMORY" in
  off) : ;;
  balanced|extreme) LOW_MEM_ON=1 ;;
  auto)
    if [ "${MEM_LIMIT_MB:-0}" -gt 0 ] && [ "$MEM_LIMIT_MB" -lt 1024 ]; then
      LOW_MEM_ON=1
    fi
    ;;
esac
if [ "$LOW_MEM_ON" -eq 1 ]; then
  log "LOW_MEMORY=$LOW_MEMORY: applying Chromium memory flags (detected ~${MEM_LIMIT_MB} MB limit)"
else
  log "LOW_MEMORY=$LOW_MEMORY: memory flags off (detected ~${MEM_LIMIT_MB} MB limit)"
fi

# Chromium/Electron switches that meaningfully cut RSS for autosurf viewers
# (the "balanced" set - measured with a real Chromium 149, see README):
#   --renderer-process-limit=1     one renderer for all sessions, not one each
#   --enable-low-end-device-mode   Chromium's own low-RAM behaviour (aggressive
#                                  memory purging, smaller caches)
#   --memory-model=low             same idea, newer Chromium
#   --js-flags=--max-old-space-size=64  cap V8 heaps in every renderer
#   --disk-cache-size/--media-cache-size  keep the browser cache off RAM/disk
#   --disable-gpu (9Hits only)     drop the separate GPU process (~40-80 MB);
#                                  FeelingSurf upstream NEEDS swiftshader GL, so
#                                  it keeps --use-gl=angle --use-angle=swiftshader
#   --disable-{extensions,sync,background-networking,component-extensions-...}
#                                  no extensions/background work in the viewer
NH_MEM_FLAGS=()
FS_MEM_FLAGS=()
if [ "$LOW_MEM_ON" -eq 1 ]; then
  NH_MEM_FLAGS=(
    --disable-gpu --disable-dev-shm-usage --disable-extensions
    --disable-background-networking --disable-sync
    --disable-component-extensions-with-background-pages
    --renderer-process-limit=1 --enable-low-end-device-mode --memory-model=low
    --js-flags=--max-old-space-size=64
    --disk-cache-size=1048576 --media-cache-size=1048576
    --disable-features=Translate,BackForwardCache,MediaRouter,OptimizationHints
  )
  # Note: --disable-dev-shm-usage --no-sandbox --use-gl=angle
  # --use-angle=swiftshader are the unconditional upstream base flags and are
  # already in feelingsurf-run.sh (upstream replaced --disable-gpu with
  # swiftshader to fix a 2.5.2 startup crash - never add --disable-gpu here
  # unless FS_GL_MODE=disable-gpu is set explicitly).
  FS_MEM_FLAGS=(
    --disable-extensions --disable-background-networking --disable-sync
    --disable-component-extensions-with-background-pages
    --renderer-process-limit=1 --enable-low-end-device-mode --memory-model=low
    --js-flags=--max-old-space-size=64
    --disk-cache-size=1048576 --media-cache-size=1048576
    --disable-features=Translate,BackForwardCache,MediaRouter,OptimizationHints
  )
fi

# --- LOW_MEMORY=extreme: single-process mode for both viewers ---------------
# Measured with a real Chromium 149 (2 tabs each, stable 60s+):
#   both --single-process --disable-gpu              -> ~324 MB total
#   9Hits single-process + FeelingSurf swiftshader   -> ~458 MB total
# Both fit Render free's 512 MB, so DUAL_VIEWER_MODE=concurrent finally works.
# Chromium upstream labels --single-process as unsupported, so per-viewer
# toggles + the crash auto-fallback below make it safe: a viewer that
# crash-loops 3x at startup is relaunched WITHOUT its SP flags.
NH_EXTREME_FLAGS=()
FS_EXTREME_FLAGS=()
if [ "$LOW_MEMORY" = "extreme" ]; then
  log "LOW_MEMORY=extreme: enabling single-process mode (NH_SP=$NH_SP FS_SP=$FS_SP, FS_GL_MODE=$FS_GL_MODE)"
  if _yes "$NH_SP"; then
    NH_EXTREME_FLAGS+=(--single-process --in-process-gpu)
  fi
  if _yes "$FS_SP"; then
    FS_EXTREME_FLAGS+=(--single-process --in-process-gpu)
  fi
  case "$FS_GL_MODE" in
    disable-gpu) log "FS_GL_MODE=disable-gpu: replacing swiftshader with --disable-gpu for FeelingSurf" ;;
    *) FS_GL_MODE=swiftshader ;;
  esac
fi

# FeelingSurf display resolution: 1280x720 on small boxes saves a few MB of
# Xvfb framebuffer + renderer surface memory (1920x1080 on normal hosts).
FS_RESOLUTION="${FS_RESOLUTION:-}"
if [ -z "$FS_RESOLUTION" ]; then
  if [ "$LOW_MEM_ON" -eq 1 ]; then FS_RESOLUTION=1280x720x24; else FS_RESOLUTION=1920x1080x24; fi
fi
export FS_RESOLUTION
export FS_MEM_FLAGS="${FS_MEM_FLAGS[*]}"
export FS_EXTRA_FLAGS="${FS_EXTRA_FLAGS:-}"

# Crash-streak tracker: used by both supervisors to auto-drop single-process
# flags after 3 quick restarts (SP is the risky knob; a viewer that crashes
# under SP should fall back to the balanced set instead of loop-forever).
crash_streak() {
  local file="/tmp/$1.crash" now last start
  now=$(date +%s 2>/dev/null || echo 0)
  last=$(cat "$file" 2>/dev/null || echo 0)
  start=$(cat "/tmp/$1.start" 2>/dev/null || echo 0)
  if [ "$start" -gt 0 ] && [ $((now - start)) -lt 90 ]; then
    echo $((last + 1)) > "$file"
  else
    echo 1 > "$file"
  fi
  echo "$(date +%s 2>/dev/null || echo 0)" > "/tmp/$1.start"
  cat "$file"
}

# Best-effort swap for hosts that allow it (Docker --privileged, real VMs).
# On Render/free Docker swapon lacks CAP_SYS_ADMIN and this quietly no-ops.
maybe_create_swap() {
  local size="${1:-256M}" f="/tmp/9hits_swap"
  command -v swapon >/dev/null 2>&1 || { log "NOTE: swapon not available - skipping swap"; return 0; }
  if ! fallocate -l "$size" "$f" 2>/dev/null; then
    local mb="${size%[a-zA-Z]*}"
    dd if=/dev/zero of="$f" bs=1M count="${mb:-256}" 2>/dev/null || return 0
  fi
  chmod 600 "$f"
  if mkswap "$f" >/dev/null 2>&1 && swapon "$f" 2>/dev/null; then
    log "swap enabled: $size at $f (helps Chromium survive peaks)"
  else
    rm -f "$f"
    log "NOTE: swap ($size) requested but swapon is not permitted on this host"
  fi
}
if [ -n "$CREATE_SWAP" ]; then
  maybe_create_swap "$CREATE_SWAP"
elif [ "${MEM_LIMIT_MB:-0}" -gt 0 ] && [ "$MEM_LIMIT_MB" -lt 1024 ]; then
  log "small instance (~${MEM_LIMIT_MB} MB): trying a 256M swap (best-effort)"
  maybe_create_swap "256M"
fi

# Time-slice turn gate: in time-slice mode a viewer only launches when
# memguard.py has marked it as the active one (/tmp/active_viewer). Fail-open:
# if memguard is missing or its heartbeat (/tmp/memguard.alive) is stale, the
# viewer runs freely so a dead guardian can never deadlock the container.
wait_for_turn() {
  local name="$1" tries=0 active="" age=0
  case "$DUAL_VIEWER_MODE" in
    concurrent|off) return 0 ;;
  esac
  while :; do
    if [ ! -f /tmp/active_viewer ] || [ ! -f /tmp/memguard.alive ]; then
      tries=$((tries + 1))
      if [ "$tries" -ge 3 ]; then
        log "memguard turn file not present - running $name anyway (fail-open)"
        return 0
      fi
      sleep 3 & wait $!
      continue
    fi
    if [ -f /tmp/memguard.alive ]; then
      age=$(($(date +%s 2>/dev/null || echo 0) - $(stat -c %Y /tmp/memguard.alive 2>/dev/null || echo 0)))
      if [ "$age" -gt "${MEMGUARD_DEAD_AFTER:-90}" ]; then
        log "memguard heartbeat stale (${age}s) - running $name anyway (fail-open)"
        return 0
      fi
    fi
    active=$(cat /tmp/active_viewer 2>/dev/null || echo both)
    case "$active" in
      both|"$name") return 0 ;;
    esac
    sleep 5 & wait $!
  done
}

# Script-level knob used by BOTH viewers (must be set before the 9Hits gate).
# RESTART_DELAY was an nh.sh knob; here it is an alias for the supervisor delay.
SUPERVISOR_DELAY="${SUPERVISOR_DELAY:-${RESTART_DELAY:-10}}"

# --------------------------------- 9Hits on/off toggle (default: OFF) --------
NINEHITS_ENABLED="${NINEHITS_ENABLED:-no}"
if _yes "$NINEHITS_ENABLED"; then
  NINEHITS_ENABLED=yes
  log "9Hits enabled (NINEHITS_ENABLED=yes)"
else
  NINEHITS_ENABLED=no
fi
export NINEHITS_ENABLED

if _yes "$NINEHITS_ENABLED"; then
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

  # LOW_MEMORY Chromium flags (only when this is a small box). Applied BEFORE
  # EXTRA_ARGS so user-supplied flags can override them (last one wins).
  NH_ARGS+=("${NH_MEM_FLAGS[@]}")

  # EXTRA_ARGS: raw space-separated extra viewer flags appended as-is.
  if [ -n "${EXTRA_ARGS:-}" ]; then
    read -r -a extra_args <<< "$EXTRA_ARGS"
    NH_ARGS+=("${extra_args[@]}")
  fi

  # Extra positional args (Render start command / docker run args).
  NH_ARGS+=("$@")

  # -------------------------------------------- script-level (consumed locally)
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
    # On tiny instances shrink the framebuffer too (a few MB + renderer cost).
    if [ "$LOW_MEM_ON" -eq 1 ]; then
      echo "1280x720x24"; return
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

    local stuck=0
    _yes "$NH_WATCHDOG" && stuck="$NH_WATCHDOG_STUCK"
    NH_SP_ON="$NH_SP"   # recomputed per launch (auto-fallback may disable it)

    while :; do
      # In time-slice mode wait until memguard.py gives us the active turn.
      wait_for_turn ninehits
      wait_display || true

      local run_args=(--auto-start --in-loop)
      _yes "$NH_RENDER_TO_TERMINAL" && run_args+=(--render-to-terminal)
      [ -n "${RESET_INTERVAL:-}" ] && run_args+=("--reset-interval=${RESET_INTERVAL}")
      # The run pass is the LONG-LIVED instance, so the LOW_MEMORY Chromium
      # flags must be here too (the init pass instance exits immediately).
      run_args+=("${NH_MEM_FLAGS[@]}")
      # Single-process (extreme mode): apply to the run pass only, with the
      # crash auto-fallback (3 quick crashes -> drop SP permanently).
      if [ "$LOW_MEMORY" = "extreme" ] && _yes "$NH_SP_ON"; then
        if [ -f /tmp/viewer.restarts ]; then
          local streak
          streak=$(crash_streak viewer)
          if [ "$streak" -ge 3 ]; then
            NH_SP_ON=no
            log "9Hits crashed $streak times at startup - disabling single-process for 9Hits (balanced flags only)"
          fi
        fi
        # Fast-crash detector: the previous run pass died within 10s with a
        # signal/abort exit code - the classic --single-process failure on
        # 512 MB boxes (Chromium exits 133 = SIGTRAP). Waiting for the 3-cycle
        # streak above would take ~22 min because each cycle includes a full
        # init pass, so drop SP on THIS launch instead.
        if [ -f /tmp/viewer.fastcrash ]; then
          rm -f /tmp/viewer.fastcrash
          log "9Hits run pass died within 10s under --single-process - dropping single-process for this launch (balanced flags only)"
          NH_SP_ON=no
        fi
        if _yes "$NH_SP_ON"; then
          run_args+=("${NH_EXTREME_FLAGS[@]}")
        fi
      fi
      # NH_RUN_EXTRA_ARGS: run-pass-only raw flags (init pass uses EXTRA_ARGS).
      if [ -n "${NH_RUN_EXTRA_ARGS:-}" ]; then
        read -r -a _run_extra <<< "$NH_RUN_EXTRA_ARGS"
        run_args+=("${_run_extra[@]}")
      fi

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

      # The init pass can take minutes; re-check the turn so a long init never
      # leaks the run pass into the other viewer's time-slice.
      wait_for_turn ninehits

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
      local launch_epoch
      launch_epoch=$(date +%s 2>/dev/null || echo 0)
      wait "$vpid"
      local code=$?
      rm -f /tmp/viewer.pid
      echo "down" > /tmp/viewer.state
      # Fast-crash sentinel: a run pass that dies in under 10 seconds with a
      # non-clean, non-shutdown exit code almost always means the Chromium
      # flags themselves are unusable here (--single-process -> code 133 /
      # SIGTRAP on Render's 512 MB plan). The next iteration reads this file
      # and relaunches WITHOUT --single-process straight away.
      local now_epoch=0 uptime_s=0
      now_epoch=$(date +%s 2>/dev/null || echo 0)
      [ "$launch_epoch" -gt 0 ] && uptime_s=$((now_epoch - launch_epoch))
      if [ "$uptime_s" -lt 10 ] \
         && [ "$code" -ne 0 ] && [ "$code" -ne 143 ] && [ "$code" -ne 137 ] \
         && [ "$code" -ne 15 ] && [ "$code" -ne 9 ]; then
        touch /tmp/viewer.fastcrash 2>/dev/null || true
        log "9Hits run pass exited after ${uptime_s}s with code $code - flagging fast crash (single-process will be dropped on the next launch)"
      fi
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
else
  # 9Hits is OFF by default so the container fits in 512 MB (two Chromium
  # viewers OOM the free plan). Skip the entire 9Hits stack: the nh / Xvfb /
  # VNC supervisors, fix_shm, the proxy-list fetch, and the ACCESS_KEY /
  # pool / RAM warnings. Advertise the disabled state to the health server.
  log "9Hits disabled (NINEHITS_ENABLED=no) - running FeelingSurf-only + /health; set NINEHITS_ENABLED=yes to run both viewers (LOW_MEMORY + memguard keep them inside small plans)"
  export SUPERVISOR_PID=0 XVFB_SUPERVISOR_PID=0 VNC_SUPERVISOR_PID=0
fi

# FeelingSurf runs in this same container/deployment. Keep it under a separate
# supervisor so either viewer can restart without taking down the other one.
feelingsurf_supervisor() {
  trap 'log "FeelingSurf supervisor stopped"; exit 0' TERM INT
  FS_SP_ON="$FS_SP"   # auto-fallback may disable it
  while :; do
    # In time-slice mode wait until memguard.py gives us the active turn.
    wait_for_turn feelingsurf
    # Extreme mode: single-process, with crash auto-fallback (3 quick crashes
    # -> relaunch on the balanced flag set instead of loop-forever).
    if [ "$LOW_MEMORY" = "extreme" ] && _yes "$FS_SP_ON"; then
      if [ -f /tmp/feelingsurf.restarts ]; then
        local streak
        streak=$(crash_streak feelingsurf)
        if [ "$streak" -ge 3 ]; then
          FS_SP_ON=no
          log "FeelingSurf crashed $streak times at startup - disabling single-process for FeelingSurf (balanced flags only)"
        fi
      fi
    fi
    export FS_SP="$FS_SP_ON"
    log "launching FeelingSurf viewer (FS_SP=$FS_SP_ON FS_GL_MODE=$FS_GL_MODE)"
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

if [ "$RUN_MODE" = "ninehits-only" ]; then
  export FEELINGSURF_SUPERVISOR_PID=0
  log "ninehits-only mode: FeelingSurf slot is owned by supervisor.py - not starting a second instance here"
else
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
fi

# If both viewers ended up disabled (explicitly or because the binaries are
# absent), make it obvious that only /health will run.
if ! _yes "$NINEHITS_ENABLED" && [ "${FEELINGSURF_SUPERVISOR_PID:-0}" -eq 0 ]; then
  log "WARNING: both viewers are disabled (NINEHITS_ENABLED=no and FEELINGSURF_ENABLED=no/absent) - only /health will run"
fi

if _yes "$NINEHITS_ENABLED"; then
  log "9Hits supervisor pid=$SUPERVISOR_PID (Xvfb $NH_DISPLAY pid=$XVFB_SUPERVISOR_PID)"
  [ "$VNC_SUPERVISOR_PID" -gt 1 ] && log "VNC supervisor pid=$VNC_SUPERVISOR_PID (port ${VNC_PORT:-5901})"
fi
[ "${FEELINGSURF_SUPERVISOR_PID:-0}" -gt 1 ] && log "FeelingSurf supervisor pid=$FEELINGSURF_SUPERVISOR_PID"

# Memory guardian + time-slice scheduler. It watches total RSS against the
# cgroup limit and (a) restarts the heaviest viewer before the platform OOMs
# the container, or (b) in time-slice mode alternates the two viewers. The
# supervisors' wait_for_turn() gates fail open if this ever dies.
if [ "$RUN_MODE" = "ninehits-only" ]; then
  export MEMGUARD_PID=0
  log "ninehits-only mode: memguard is owned by supervisor.py - not starting a second guardian here"
elif [ "$DUAL_VIEWER_MODE" != "off" ] && [ -f "$SCRIPT_DIR/memguard.py" ]; then
  python3 "$SCRIPT_DIR/memguard.py" &
  export MEMGUARD_PID=$!
  log "memguard started (DUAL_VIEWER_MODE=$DUAL_VIEWER_MODE, TIME_SLICE=${TIME_SLICE}s, pid=$MEMGUARD_PID)"
else
  export MEMGUARD_PID=0
  log "memguard disabled (DUAL_VIEWER_MODE=$DUAL_VIEWER_MODE) - no OOM protection; both viewers may not fit small plans"
fi

if [ "$RUN_MODE" != "ninehits-only" ]; then
  log "combined health endpoint on 0.0.0.0:$PORT (GET /health)"
fi

# ---------------------------------------------------------------- ninehits-only
# When the Python ``supervisor.py`` is in charge it runs this script as
# ``/start.sh ninehits-only`` and the only thing that should be alive in
# this PID namespace afterwards is the 9Hits process tree (Xvfb + 9Hits
# supervisor + optional VNC). The script must:
#
#   * Exit cleanly when the 9Hits supervisor exits, so the Python
#     supervisor can re-launch us (the per-slot crash recovery).
#   * Forward SIGTERM to the 9Hits supervisor + Xvfb + VNC, so the
#     container can shut down on ``docker stop``.
#   * NOT start the FeelingSurf supervisor, the memguard, or the health
#     server - those are independent slots owned by the Python supervisor.
if [ "$RUN_MODE" = "ninehits-only" ]; then
  if [ "${SUPERVISOR_PID:-0}" -le 1 ] || [ "${XVFB_SUPERVISOR_PID:-0}" -le 1 ]; then
    log "ninehits-only mode: 9Hits is not enabled (SUPERVISOR_PID=$SUPERVISOR_PID) - exiting"
    exit 0
  fi

  log "ninehits-only mode: waiting on 9Hits supervisor pid=$SUPERVISOR_PID (Xvfb $XVFB_SUPERVISOR_PID)"

  forward_term() {
    log "ninehits-only: forwarding signal to 9Hits supervisor and Xvfb"
    [ "${SUPERVISOR_PID:-0}" -gt 1 ] && kill -TERM "$SUPERVISOR_PID" 2>/dev/null || true
    [ "${XVFB_SUPERVISOR_PID:-0}" -gt 1 ] && kill -TERM "$XVFB_SUPERVISOR_PID" 2>/dev/null || true
    [ "${VNC_SUPERVISOR_PID:-0}" -gt 1 ] && kill -TERM "$VNC_SUPERVISOR_PID" 2>/dev/null || true
  }
  trap 'forward_term' TERM INT HUP

  # Wait for the 9Hits supervisor. `wait` is interruptible by the trap
  # above, so a SIGTERM from the Python supervisor reaches us and we
  # forward it to the 9Hits child. When the 9Hits child finally exits,
  # wait returns its exit code - that becomes our exit code, so the
  # Python supervisor sees a real non-zero code on abnormal exits and
  # bumps the restart counter.
  wait "$SUPERVISOR_PID"
  nh_exit=$?

  # 9Hits supervisor died; tear down its Xvfb (and optional VNC) so the
  # process tree unwinds cleanly. The Python supervisor will spawn a
  # fresh /start.sh ninehits-only soon.
  log "ninehits-only: 9Hits supervisor exited (code $nh_exit) - tearing down Xvfb/VNC"
  [ "${XVFB_SUPERVISOR_PID:-0}" -gt 1 ] && kill -TERM "$XVFB_SUPERVISOR_PID" 2>/dev/null || true
  [ "${VNC_SUPERVISOR_PID:-0}" -gt 1 ] && kill -TERM "$VNC_SUPERVISOR_PID" 2>/dev/null || true
  # Brief grace for the children to die on their own.
  sleep 2 & wait $!
  exit "$nh_exit"
fi

exec python3 "$SCRIPT_DIR/health_server.py"
