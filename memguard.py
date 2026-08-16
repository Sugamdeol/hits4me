#!/usr/bin/env python3
"""
memguard.py - memory guardian + time-slice scheduler for the dual-viewer
(9Hits v6 + FeelingSurf) container on small free tiers (Render 512 MB, etc.).

Why this exists
---------------
Two Chromium-based viewers cannot both stay resident inside a 512 MB cgroup:
9Hits v6 (CEF) idles at ~250-400 MB and FeelingSurf (Electron) at ~250-350 MB,
plus Xvfb / supervisors / health server. When total RSS crosses the cgroup
limit, the platform OOM-kills the WHOLE container ("Ran out of memory (used
over 512MB)" on Render), taking both viewers down in a loop.

This daemon keeps the container inside the limit instead of letting the
platform kill it:

  * It samples total + per-viewer memory from /proc every few seconds and
    writes /tmp/memguard.json for the /health endpoint (memory stats, mode,
    turns). All numbers are PSS (proportional set size) - the UNIQUE memory
    each process contributes. Plain RSS double-counts the file-backed pages
    two Chromium instances share (same binary/libs), which would show a
    ~300 MB phantom overshoot and trigger needless restarts.

  * DUAL_VIEWER_MODE=concurrent (or auto while memory fits):
      Both viewers run together with LOW_MEMORY Chromium flags. If total RSS
      exceeds MEMGUARD_HARD_PCT of the limit, the HEAVIEST viewer is restarted
      (SIGTERM, then SIGKILL after a grace period) so the platform never has
      to OOM-kill the container. A cooldown prevents kill-thrashing.

  * DUAL_VIEWER_MODE=time-slice:
      Only one viewer is active at a time. Every TIME_SLICE seconds the active
      viewer is gracefully stopped and the other one starts (supervisors gate
      launches on /tmp/active_viewer). Memory use drops to ~half, so 512 MB is
      always enough - at the cost of ~50% uptime per viewer.

  * DUAL_VIEWER_MODE=auto (default):
      Start in concurrent mode; if the box really cannot fit both (repeated
      over-limit interventions inside a window), escalate automatically to
      time-slice and keep the lighter viewer running.

Safety properties
-----------------
  * Never signals supervisors, Xvfb, the health server or itself - only the
    two viewer process trees (matched by their real executable, never by
    command-line text, so python/bash wrappers can't be mistaken for viewers).
  * /tmp/memguard.alive is touched every interval; if it goes stale, the
    start.sh supervisors stop gating and run freely (fail-open, no deadlock).
  * A viewer killed here is restarted by its own supervisor (start.sh), so a
    "kill" is a clean restart, never a lost viewer.

Env vars (all optional)
-----------------------
  DUAL_VIEWER_MODE      auto | concurrent | time-slice | off   (default auto)
  TIME_SLICE            seconds per viewer turn in time-slice  (default 1500)
  ACTIVE_VIEWER         first active viewer in time-slice      (default ninehits)
  MEMGUARD_LIMIT_MB     hard memory limit; 0 = auto-detect      (default 0)
  MEMGUARD_INTERVAL     sampling interval seconds               (default 5)
  MEMGUARD_SOFT_PCT     unused today, reserved for future       (default 80)
  MEMGUARD_HARD_PCT     kill when total RSS exceeds this %%     (default 90)
  MEMGUARD_COOLDOWN     min seconds between restarts            (default 90)
  MEMGUARD_KILL_GRACE   SIGTERM -> SIGKILL grace seconds        (default 15)
  MEMGUARD_ESCALATE_HITS    over-limit restarts inside window   (default 2)
  MEMGUARD_ESCALATE_WINDOW  seconds for the hits window         (default 600)

Stdlib only - no dependencies.
"""

import json
import os
import signal
import sys
import time

# --------------------------------------------------------------------------- #
# Helpers (must precede the config block below, which calls them)
# --------------------------------------------------------------------------- #

def _env(name, default=""):
    return os.environ.get(name, default)


def _env_int(name, default):
    try:
        return int(str(_env(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default


def _log(msg):
    print("[memguard] %s" % msg, flush=True)


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

INTERVAL = _env_int("MEMGUARD_INTERVAL", 5)
LIMIT_MB = _env_int("MEMGUARD_LIMIT_MB", 0)          # 0 = auto-detect
# Default threshold: 97% of a small budget so a tuned pair (extreme mode ~320-460
# MB) can run CONCURRENTLY without memguard ever intervening; the platform OOM
# kill at 100% stays unreachable because the guardian acts at 97%.
HARD_PCT = float(_env("MEMGUARD_HARD_PCT", "97"))    # % of limit that triggers
COOLDOWN = _env_int("MEMGUARD_COOLDOWN", 90)         # min s between restarts
KILL_GRACE = _env_int("MEMGUARD_KILL_GRACE", 15)     # TERM -> KILL
ESCALATE_HITS = _env_int("MEMGUARD_ESCALATE_HITS", 3)
ESCALATE_WINDOW = _env_int("MEMGUARD_ESCALATE_WINDOW", 600)
MODE = _env("DUAL_VIEWER_MODE", "auto").lower()      # auto|concurrent|time-slice|off
TIME_SLICE = _env_int("TIME_SLICE", 1500)
ACTIVE_FIRST = _env("ACTIVE_VIEWER", "ninehits").lower()
if ACTIVE_FIRST not in ("ninehits", "feelingsurf"):
    ACTIVE_FIRST = "ninehits"

STATE_FILE = "/tmp/memguard.json"
MODE_FILE = "/tmp/memguard.mode"
TURN_FILE = "/tmp/active_viewer"
ALIVE_FILE = "/tmp/memguard.alive"

PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


# --------------------------------------------------------------------------- #
# Memory accounting (reads /proc; works inside the container's PID namespace)
# --------------------------------------------------------------------------- #

def _detect_limit_mb():
    """Best-effort cgroup v2/v1 memory limit, then MemTotal. 0 = unknown."""
    if LIMIT_MB > 0:
        return LIMIT_MB
    candidates = (
        "/sys/fs/cgroup/memory.max",                     # cgroup v2
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",   # cgroup v1
    )
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
        except OSError:
            continue
        if not raw or raw == "max":
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        # Sanity: ignore absurdly large "no limit" values (>= 1 TiB).
        if 0 < value < 1 << 40:
            return value // (1024 * 1024)
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _all_pids():
    try:
        return [int(e) for e in os.listdir("/proc") if e.isdigit()]
    except OSError:
        return []


def _rss_pages(pid):
    try:
        with open("/proc/%d/statm" % pid, "r", encoding="utf-8") as fh:
            return int(fh.read().split()[1])  # resident set size, in pages
    except (OSError, ValueError, IndexError):
        return 0


def _pss_mb(pid):
    """Proportional set size (MB): a process's UNIQUE memory contribution.

    RSS over-counts when two Chromium instances share file-backed pages
    (same binary, same libs): summing RSS across the pair double-counts the
    shared pages (~300 MB of phantom memory in tests). PSS divides each
    shared page among its users, so summing PSS across the container's
    processes approximates what the cgroup actually charges - the metric
    that decides whether we are really inside the 512 MB budget.
    """
    try:
        with open("/proc/%d/smaps_rollup" % pid, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("Pss:"):
                    return int(line.split()[1]) / 1024.0  # kB -> MB
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def _cmdline(pid):
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as fh:
            return [t.decode("utf-8", "replace") for t in fh.read().split(b"\0") if t]
    except OSError:
        return []


def _comm(pid):
    try:
        with open("/proc/%d/comm" % pid, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _exe(pid):
    try:
        return os.readlink("/proc/%d/exe" % pid)
    except OSError:
        return ""


# Our own plumbing must NEVER be signalled: python/bash wrappers, the display
# servers, the supervisors. Matching by real executable keeps memguard blind
# to any process whose command line merely mentions a viewer name.
_SAFE_EXE_BASES = {
    "python3", "python", "bash", "sh", "dash", "runuser", "su",
    "Xvfb", "x11vnc", "wget", "timeout", "sleep", "cat", "dd",
}


def _classify(pid):
    """'ninehits' | 'feelingsurf' | None - by real executable + process name.

    9Hits v6: main binary nhviewer + engine 'may' (+ Chromium child procs
    which re-exec the same nhviewer binary). FeelingSurf: FeelingSurfViewer
    (Electron). Matching on /proc/<pid>/exe means run_pty.py / start.sh /
    memguard.py themselves (python/bash) can never be mistaken for a viewer.
    """
    exe = _exe(pid)
    base = os.path.basename(exe)
    if base in _SAFE_EXE_BASES:
        return None
    if base == "may" or base.startswith("nhviewer"):
        return "ninehits"
    if base.startswith("FeelingSurfViewer"):
        return "feelingsurf"
    # Fallback for unreadable exe (rare): comm-based, never the safe names.
    if not exe:
        comm = _comm(pid)
        if comm == "may" or comm.startswith("nhviewer"):
            return "ninehits"
        if comm.startswith("FeelingSurf"):
            return "feelingsurf"
    return None


def total_rss_mb():
    """Total unique memory of the whole container (PSS sum over all procs)."""
    return sum(_pss_mb(p) for p in _all_pids())


def viewer_rss_mb():
    ninehits = feelingsurf = 0.0
    for pid in _all_pids():
        kind = _classify(pid)
        if kind is None:
            continue
        pss = _pss_mb(pid)
        if kind == "ninehits":
            ninehits += pss
        else:
            feelingsurf += pss
    return ninehits, feelingsurf


def _viewer_pids(kind):
    return [p for p in _all_pids() if _classify(p) == kind and _alive(p)]


# --------------------------------------------------------------------------- #
# Restart (kill) a viewer: TERM, wait gracefully, then KILL stragglers.
# The supervisor in start.sh restarts the killed viewer - this is a restart,
# not a permanent stop.
# --------------------------------------------------------------------------- #

def _alive(pid):
    """True while the process is really running. Zombies (state Z/X) count as
    dead - their /proc entry lingers until the parent reaps them, which would
    otherwise make us think a killed viewer is still up."""
    try:
        with open("/proc/%d/stat" % pid, "rb") as fh:
            data = fh.read()
        rparen = data.rfind(b")")  # comm may contain spaces/parens
        if rparen < 0:
            return False
        state = chr(data[rparen + 2])
        return state not in ("Z", "X")
    except OSError:
        return False


def restart_viewer(kind, grace=KILL_GRACE):
    pids = _viewer_pids(kind)
    if not pids:
        return 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.time() + max(1, grace)
    remaining = [p for p in pids if _alive(p)]
    while remaining and time.time() < deadline:
        time.sleep(0.5)
        remaining = [p for p in remaining if _alive(p)]
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return len(pids)


# --------------------------------------------------------------------------- #
# Turn file helpers (time-slice mode)
# --------------------------------------------------------------------------- #

def _write(path, text):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError:
        pass


def _set_turn(viewer):
    """'ninehits' | 'feelingsurf' | 'both' - supervisors gate on this file."""
    _write(TURN_FILE, viewer)


def _other(viewer):
    return "feelingsurf" if viewer == "ninehits" else "ninehits"


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #

def _handle_term(_signum=None, _frame=None):
    _log("SIGTERM received - exiting (supervisors fail open)")
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, _handle_term)
    signal.signal(signal.SIGINT, _handle_term)

    if MODE == "off":
        _log("DUAL_VIEWER_MODE=off - memory guardian disabled")
        _set_turn("both")
        return 0

    limit = _detect_limit_mb()
    effective = "time-slice" if MODE == "time-slice" else "concurrent"
    active = ACTIVE_FIRST
    peak = 0.0
    interventions = 0
    last_target = None
    last_kill = 0.0
    last_cooldown_log = 0.0
    kill_times = []
    last_flip = time.time()

    _log(
        "starting (mode=%s, effective=%s, limit=%d MB, interval=%ds, time_slice=%ds)"
        % (MODE, effective, limit, INTERVAL, TIME_SLICE)
    )
    if effective == "time-slice":
        _set_turn(active)
    else:
        _set_turn("both")
    _write(MODE_FILE, effective)

    while True:
        now = time.time()
        _write(ALIVE_FILE, str(int(now)))  # heartbeat -> supervisors fail open

        total = total_rss_mb()
        nh_rss, fs_rss = viewer_rss_mb()
        peak = max(peak, total)

        hard_mb = (limit * HARD_PCT / 100.0) if limit > 0 else 0.0

        if effective == "time-slice":
            if now - last_flip >= TIME_SLICE:
                last_flip = now
                outgoing = active
                active = _other(active)
                _set_turn(active)
                killed = restart_viewer(outgoing)
                _log(
                    "time-slice flip -> active=%s (stopped %s, %d proc)"
                    % (active, outgoing, killed)
                )
        elif hard_mb > 0 and total > hard_mb:
            # Over budget: restart the heaviest viewer (or the only resident
            # one) so the platform never OOM-kills the container.
            if nh_rss >= fs_rss:
                target = "ninehits"
            else:
                target = "feelingsurf"
            if now - last_kill < COOLDOWN:
                if now - last_cooldown_log > 30:
                    last_cooldown_log = now
                    _log(
                        "over budget (%.0f/%.0f MB) but in cooldown - skipping restart"
                        % (total, hard_mb)
                    )
            else:
                last_kill = now
                killed = restart_viewer(target)
                if killed == 0:
                    # Over budget but no viewer process found - the memory hog
                    # is not one of our viewers; restarting cannot help, so do
                    # not count it as an intervention (avoids spurious
                    # escalation).
                    _log(
                        "over budget (%.0f/%.0f MB) but no %s processes to "
                        "restart - memory pressure is outside the viewers"
                        % (total, hard_mb, target)
                    )
                else:
                    interventions += 1
                    last_target = target
                    kill_times = [t for t in kill_times if now - t < ESCALATE_WINDOW]
                    kill_times.append(now)
                    _log(
                        "over budget (%.0f/%.0f MB) - restarted %s (%d proc), "
                        "intervention #%d"
                        % (total, hard_mb, target, killed, interventions)
                    )
                    if MODE == "auto" and len(kill_times) >= ESCALATE_HITS:
                        effective = "time-slice"
                        # Keep the lighter viewer running; stop the other one.
                        active = _other(target)
                        _set_turn(active)
                        _write(MODE_FILE, effective)
                        _log(
                            "auto-escalated to time-slice: %d interventions in "
                            "%ds window - box cannot fit both viewers, "
                            "alternating them (active=%s)"
                            % (len(kill_times), ESCALATE_WINDOW, active)
                        )
                        last_flip = now
        else:
            # Under budget (or unknown limit): make sure concurrent state.
            if MODE == "time-slice":
                _set_turn(active)
            else:
                _set_turn("both")

        try:
            with open(STATE_FILE, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "updated_at": int(now),
                        "configured_mode": MODE,
                        "effective_mode": effective,
                        "active_viewer": active,
                        "memory_used_mb": round(total, 1),
                        "memory_limit_mb": limit,
                        "memory_peak_mb": round(peak, 1),
                        "hard_threshold_mb": round(hard_mb, 1),
                        "ninehits_rss_mb": round(nh_rss, 1),
                        "feelingsurf_rss_mb": round(fs_rss, 1),
                        "interventions": interventions,
                        "last_target": last_target,
                        "time_slice_seconds": TIME_SLICE,
                        "interval_seconds": INTERVAL,
                        "last_flip_epoch": int(last_flip),
                        "next_flip_in_seconds": (
                            max(0, int(TIME_SLICE - (now - last_flip)))
                            if effective == "time-slice" else None
                        ),
                    },
                    fh,
                )
        except OSError:
            pass

        try:
            time.sleep(INTERVAL)
        except KeyboardInterrupt:
            break

    return 0


if __name__ == "__main__":
    sys.exit(main())
