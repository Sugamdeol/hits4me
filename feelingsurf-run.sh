#!/usr/bin/env bash
# Run FeelingSurf in the same container as 9Hits.
#
# Upstream base flags (--disable-dev-shm-usage --no-sandbox --use-gl=angle
# --use-angle=swiftshader) are kept unconditional unless FS_GL_MODE=disable-gpu
# is set (upstream replaced --disable-gpu with swiftshader software GL to fix a
# 2.5.2 startup crash; only use --disable-gpu if swiftshader misbehaves under
# single-process mode).
#
# LOW_MEMORY tuning flags come from start.sh via FS_MEM_FLAGS (applied when
# the box has < 1 GB RAM - see start.sh for the flag list). FS_EXTRA_FLAGS is
# a user override appended last (last switch wins for Chromium).
#
# LOW_MEMORY=extreme passes FS_SP=yes: --single-process --in-process-gpu are
# added, which lets BOTH viewers run concurrently inside 512 MB (~324 MB pair).
#
# Display: normally a private Xvfb (:98). When the 9Hits side is enabled and
# FS_SHARE_DISPLAY != no, it reuses the 9Hits Xvfb (:99) instead - one less
# X server (~10-20 MB) on tight instances.
set -u

DISPLAY_NUMBER="${FEELINGSURF_DISPLAY:-:98}"
FS_RESOLUTION="${FS_RESOLUTION:-1920x1080x24}"

export healthcheck=true
export healthcheck_port="${FEELINGSURF_PORT:-3000}"
# The official image accepts the lower-case name. ACCESS_TOKEN is the
# deployment-friendly alias used by this repository.
export access_token="${access_token:-${ACCESS_TOKEN:-}}"

xvfb_pid=0
viewer_pid=0

cleanup() {
  if [ "$viewer_pid" -gt 1 ]; then
    kill -TERM "$viewer_pid" 2>/dev/null || true
  fi
  if [ "$xvfb_pid" -gt 1 ]; then
    kill -TERM "$xvfb_pid" 2>/dev/null || true
  fi
  wait "$viewer_pid" 2>/dev/null || true
  wait "$xvfb_pid" 2>/dev/null || true
}
trap 'cleanup; exit 0' TERM INT EXIT

# --- shared display? (only when 9Hits runs its own Xvfb AND it is actually
# up - check the X socket so a broken/missing :99 falls back to our own) ----
SHARE_DISPLAY=0
NH_DNUM="${NH_DISPLAY:-:99}"
case "${FS_SHARE_DISPLAY:-yes}" in
  0|no|false|off) : ;;
  *)
    case "${NINEHITS_ENABLED:-no}" in
      1|yes|true|on)
        if [ -S "/tmp/.X11-unix/X${NH_DNUM#:}" ]; then
          export DISPLAY="$NH_DNUM"
          SHARE_DISPLAY=1
        fi
        ;;
    esac
    ;;
esac
if [ "$SHARE_DISPLAY" -eq 0 ]; then
  export DISPLAY="$DISPLAY_NUMBER"
  rm -f "/tmp/.X${DISPLAY_NUMBER#:}-lock"
  /usr/bin/Xvfb "$DISPLAY_NUMBER" -screen 0 "$FS_RESOLUTION" -nolisten unix &
  xvfb_pid=$!
  sleep 1
fi

fs_flags=()
fs_extra=()
# ${arr[@]+...} keeps `set -u` happy even when the arrays stay empty.
read -r -a fs_flags <<< "${FS_MEM_FLAGS:-}"
read -r -a fs_extra <<< "${FS_EXTRA_FLAGS:-}"

gl_args=()
case "${FS_GL_MODE:-swiftshader}" in
  disable-gpu)
    gl_args=(--disable-gpu)
    ;;
  *)
    gl_args=(--use-gl=angle --use-angle=swiftshader)
    ;;
esac

sp_args=()
case "${FS_SP:-no}" in
  1|yes|true|on) sp_args=(--single-process --in-process-gpu) ;;
esac

/usr/bin/FeelingSurfViewer \
  --disable-dev-shm-usage \
  --no-sandbox \
  "${gl_args[@]+"${gl_args[@]}"}" \
  "${fs_flags[@]+"${fs_flags[@]}"}" \
  "${sp_args[@]+"${sp_args[@]}"}" \
  "${fs_extra[@]+"${fs_extra[@]}"}" &
viewer_pid=$!
wait "$viewer_pid"
exit_code=$?
trap - EXIT
cleanup
exit "$exit_code"
