#!/usr/bin/env python3
"""
Combined HTTP health endpoint for the 9Hits + FeelingSurf container.
Stdlib only, no dependencies.

Routes:
    GET/HEAD /  /gui  /index.html  -> dashboard GUI (HTML, polls /health every 2s)
    GET/HEAD /health  /healthz /ping -> JSON status (per-viewer + memory)
    POST/GET  /control/<name>/<action> -> Start/Stop/Restart a managed slot
                                        (writes to /tmp/supervisor_ctl)
    GET       /logs/<name>            -> last ~4 KB of a slot's log file
    GET       /slots                  -> raw supervisor snapshot (debug)

Example /health response (both viewers up):
    {
      "service": "hits4me-combined-viewer",
      "version": "3.3.0",
      "status": "ok",
      "ninehits": {
        "status": "running", "running": true, "enabled": true,
        "pid": 42, "uptime": "20m34s", "uptime_seconds": 1234,
        "restarts": 0, "last_exit_code": null, "last_error": null,
        "memory_mb": 110.2, "child_count": 7,
        "max_memory_mb": 400, "max_children": 0,
        "check_interval_seconds": 30, "log_file": "/logs/9hits.log"
      },
      "feelingsurf": {
        "status": "running", "running": true, "enabled": true,
        "pid": 17, "uptime": "9m0s", "uptime_seconds": 540,
        "restarts": 1, "last_exit_code": null, "last_error": null,
        "memory_mb": 198.6, "child_count": 9,
        "max_memory_mb": 400, "max_children": 0,
        "check_interval_seconds": 30, "log_file": "/logs/feelingsurf.log"
      },
      "supervisor_running": true,
      "memguard_status": "running", "memguard_pid": 12,
      "health_status": "running", "health_pid": 1,
      "dual_viewer_mode": "auto",
      "effective_mode": "concurrent",
      "active_viewer": "both",
      "memory_used_mb": 312.4,
      "memory_limit_mb": 512,
      "memory_peak_mb": 351.1,
      "ninehits_rss_mb": 110.2,
      "feelingsurf_rss_mb": 198.6,
      "memguard_interventions": 0,
      "uptime_seconds": 1234,
      "low_memory": "extreme"
    }

Top-level ``status`` is computed from the per-slot statuses:
  * "ok"         - every enabled viewer slot is running
  * "degraded"   - at least one viewer is up, another is down; the
                   supervisor is in control and will recover it
  * "restarting" - all enabled viewers are down but the supervisor is up
                   (very early in the boot, or all of them crashed
                   simultaneously)
  * "error"      - the supervisor itself is gone (no slot can recover
                   on its own); HTTP 503

Per-viewer ``status`` values (in the nested object):
  * "running"  - the managed process is alive
  * "stopped"  - the slot is enabled but the process is not running
                 (will be restarted by the supervisor on cooldown, or
                 it was stopped by the operator)
  * "crashed"  - the process just died abnormally (last exit != 0/TERM)
  * "parked"   - the slot is cooling off after too many rapid crashes;
                 the supervisor will retry once SUPERVISOR_PARK_SECS
                 elapse
  * "starting" - the supervisor is launching it right now
  * "stopping" - the supervisor is stopping it right now
  * "disabled" - the slot is off by configuration

The endpoint always answers 200 when the health server itself is alive so
uptime bots do not get spurious 5xx spikes during a normal slot restart.
It returns 503 only when the supervisor is gone - that is the case where
the container truly needs a full restart.
"""

import http.client
import json
import os
import signal
import sys
import time

try:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as Server
except ImportError:  # Python < 3.7
    from http.server import BaseHTTPRequestHandler, HTTPServer as Server

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

PORT = int(os.environ.get("PORT", "10000") or 10000)
FEELINGSURF_PORT = int(os.environ.get("FEELINGSURF_PORT", "3000") or 3000)
DUAL_VIEWER_MODE = os.environ.get("DUAL_VIEWER_MODE", "auto")
LOW_MEMORY = os.environ.get("LOW_MEMORY", "auto")

FEELINGSURF_ENABLED = os.environ.get("FEELINGSURF_ENABLED", "yes").lower() not in (
    "0", "no", "false", "off"
)
NINEHITS_ENABLED = os.environ.get("NINEHITS_ENABLED", "no").lower() not in (
    "0", "no", "false", "off"
)

# Legacy single-process file paths - kept for backwards compatibility so the
# /health JSON still reports the same fields older uptime bots parse. When the
# Python supervisor is in charge, the values come from the supervisor state
# file (and the legacy files are not used).
PID_FILE = os.environ.get("VIEWER_PID_FILE", "/tmp/viewer.pid")
RESTART_FILE = os.environ.get("VIEWER_RESTART_FILE", "/tmp/viewer.restarts")
STATE_FILE = os.environ.get("VIEWER_STATE_FILE", "/tmp/viewer.state")
HEARTBEAT_FILE = os.environ.get("VIEWER_HEARTBEAT_FILE", "/tmp/viewer.lastoutput")
XVFB_PID_FILE = "/tmp/xvfb.pid"
FEELINGSURF_PID_FILE = "/tmp/feelingsurf.pid"
FEELINGSURF_RESTART_FILE = "/tmp/feelingsurf.restarts"

# Supervisor-managed state (used when the container runs under
# ``supervisor.py``). When the supervisor is absent (e.g. legacy bash-only
# mode or HF Gradio native mode) the server falls back to reading the legacy
# files above and reporting a read-only RSS snapshot.
SUPERVISOR_STATE_FILE = "/tmp/supervisor_state.json"
SUPERVISOR_ALIVE_FILE = "/tmp/supervisor.alive"
SUPERVISOR_CTL_DIR = "/tmp/supervisor_ctl"
SUPERVISOR_LOG_DIR = os.environ.get("LOG_DIR", "/logs")

MEMGUARD_STATE_FILE = "/tmp/memguard.json"
MEMGUARD_MODE_FILE = "/tmp/memguard.mode"
MEMGUARD_PID = int(os.environ.get("MEMGUARD_PID", "0") or 0)

STARTED_AT = time.time()
VERSION = "3.3.0"

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _read_int(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _pid_alive(pid):
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _supervisor_alive():
    """The supervisor touches /tmp/supervisor.alive every loop tick; an
    existing file is a strong signal it is up."""
    return os.path.isfile(SUPERVISOR_ALIVE_FILE)


def _supervisor_state():
    """Read /tmp/supervisor_state.json or {} when missing/invalid.

    Returns {} when the supervisor is not alive, so callers do not pick
    up stale state from a previous container run. A stale alive file
    (older than 30s) is also treated as not-alive, which guards against
    a frozen supervisor.
    """
    if not _supervisor_alive():
        return {}
    try:
        mtime = os.stat(SUPERVISOR_ALIVE_FILE).st_mtime
        if (time.time() - mtime) > 30:
            return {}
    except OSError:
        return {}
    try:
        with open(SUPERVISOR_STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _slot_snapshot(name):
    """Return the supervisor slot dict for ``name`` or None."""
    state = _supervisor_state()
    slots = state.get("slots") or {}
    if name in slots and isinstance(slots[name], dict):
        return slots[name]
    return None


# --------------------------------------------------------------------------- #
# Memory accounting (used when the supervisor is not present)
# --------------------------------------------------------------------------- #

def _proc_parent_map():
    children = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return children
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open("/proc/%d/stat" % pid, "rb") as fh:
                raw = fh.read()
            right = raw.rfind(b")")
            fields = raw[right + 2 :].split()
            parent = int(fields[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(parent, []).append(pid)
    return children


def _process_tree(root_pid):
    children = _proc_parent_map()
    result = set()
    pending = [root_pid] if root_pid > 1 else []
    while pending:
        pid = pending.pop()
        if pid in result:
            continue
        result.add(pid)
        pending.extend(children.get(pid, ()))
    return result


def _rss_mb(pid):
    try:
        with open("/proc/%d/status", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def native_memory_state():
    """Read-only RSS snapshot used when the supervisor is absent."""
    sup_pid = int(os.environ.get("SUPERVISOR_PID", "0") or 0)
    root_pid = sup_pid if _pid_alive(sup_pid) else os.getpid()
    pids = _process_tree(root_pid)
    used = round(sum(_rss_mb(p) for p in pids), 1) if pids else None
    limit = None
    for path in (
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    ):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
            if raw and raw != "max":
                value = int(raw)
                if 0 < value < 1 << 40:
                    limit = value // (1024 * 1024)
                    break
        except (OSError, ValueError):
            pass
    if limit is None:
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        limit = int(line.split()[1]) // 1024
                        break
        except (OSError, ValueError, IndexError):
            pass
    return {
        "memory_used_mb": used,
        "memory_limit_mb": limit,
        "memory_peak_mb": used,
        "hard_threshold_mb": None,
        "ninehits_rss_mb": None,
        "feelingsurf_rss_mb": None,
        "active_viewer": "both",
        "time_slice_seconds": None,
        "next_flip_in_seconds": None,
        "interventions": 0,
        "last_target": None,
    }


def memguard_state():
    try:
        with open(MEMGUARD_STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def runtime_memory_state():
    """Use supervisor data (which mirrors memguard) when available.

    Memguard state files in /tmp are only consulted when memguard is
    actually running right now (per the supervisor snapshot). Otherwise
    we fall back to the read-only RSS snapshot to avoid reporting stale
    numbers from a previous run / disabled mode.
    """
    slot_mem = _slot_snapshot("memguard")
    if slot_mem and slot_mem.get("enabled") and slot_mem.get("status") == "running":
        data = memguard_state()
        if data:
            return data
    return native_memory_state()


def effective_mode():
    # Only report a memguard-derived effective mode if memguard is actually
    # running right now. Without this guard a leftover /tmp/memguard.mode
    # file from a previous run can mask the real configured DUAL_VIEWER_MODE.
    slot_mem = _slot_snapshot("memguard")
    if slot_mem and slot_mem.get("enabled") and slot_mem.get("status") == "running":
        try:
            with open(MEMGUARD_MODE_FILE, "r", encoding="utf-8") as fh:
                mode = fh.read().strip()
            if mode:
                return mode
        except OSError:
            pass
    if DUAL_VIEWER_MODE == "off":
        return "off"
    return None


# --------------------------------------------------------------------------- #
# Per-slot status
# --------------------------------------------------------------------------- #

def _nh_viewer_running_legacy():
    """Legacy bash-mode detection (no supervisor)."""
    if not NINEHITS_ENABLED:
        return None
    return _pid_alive(_read_int(PID_FILE))


def _nh_viewer_phase_legacy():
    if not NINEHITS_ENABLED:
        return None
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            phase = fh.read().strip()
            return phase or None
    except OSError:
        return None


def _nh_silent_seconds_legacy():
    if not NINEHITS_ENABLED:
        return None
    try:
        return int(time.time() - os.stat(HEARTBEAT_FILE).st_mtime)
    except OSError:
        return None


def _xvfb_running_legacy():
    if not NINEHITS_ENABLED:
        return None
    return _pid_alive(_read_int(XVFB_PID_FILE))


def _feelingsurf_running_legacy():
    if not FEELINGSURF_ENABLED:
        return None
    if not _pid_alive(_read_int(FEELINGSURF_PID_FILE)):
        return False
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", FEELINGSURF_PORT, timeout=0.75
        )
        connection.request("HEAD", "/")
        response = connection.getresponse()
        response.read()
        connection.close()
        return response.status < 500
    except (OSError, http.client.HTTPException):
        return False


# --------------------------------------------------------------------------- #
# Log serving (for the dashboard "show last log" button)
# --------------------------------------------------------------------------- #

LOG_TAIL_BYTES = 4 * 1024
LOG_PATH_FOR_SLOT = {
    "ninehits": os.path.join(SUPERVISOR_LOG_DIR, "9hits.log"),
    "feelingsurf": os.path.join(SUPERVISOR_LOG_DIR, "feelingsurf.log"),
    "memguard": os.path.join(SUPERVISOR_LOG_DIR, "memguard.log"),
    "health": os.path.join(SUPERVISOR_LOG_DIR, "health.log"),
    "supervisor": os.path.join(SUPERVISOR_LOG_DIR, "supervisor.log"),
}


def _tail_log(path):
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    with open(path, "rb") as fh:
        if size > LOG_TAIL_BYTES:
            fh.seek(size - LOG_TAIL_BYTES)
            data = fh.read()
        else:
            data = fh.read()
    return data.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Decorator: silence BrokenPipeError / ECONNRESET from the HTTP layer
# --------------------------------------------------------------------------- #

def _quiet(func):
    """Swallow client-disconnect errors so an aborting uptime bot does not
    dump a traceback into the supervisor's log."""
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except OSError as exc:
            if getattr(exc, "errno", None) not in (32, 104):  # EPIPE, ECONNRESET
                raise
        return None
    return wrapper


# --------------------------------------------------------------------------- #
# HTML dashboard (inline; ~0 MB of RAM, polls /health every 2s)
# --------------------------------------------------------------------------- #

GUI_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>hits4me - viewer dashboard</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background:#0d1117; color:#e6edf3; font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; padding:20px; }
  h1 { font-size:18px; letter-spacing:.5px; }
  .wrap { max-width:1000px; margin:0 auto; }
  header { display:flex; align-items:center; gap:12px; margin-bottom:16px; flex-wrap:wrap; }
  .sub { color:#8b949e; font-size:12px; }
  .pill { padding:3px 10px; border-radius:999px; font-size:12px; font-weight:700; }
  .pill.ok { background:#1f6f3f33; color:#3fb950; border:1px solid #23863655; }
  .pill.restarting { background:#9e6a0311; color:#d29922; border:1px solid #9e6a0344; }
  .pill.error { background:#f8514911; color:#f85149; border:1px solid #f8514944; }
  .pill.disabled { background:#30363d33; color:#8b949e; border:1px solid #30363d; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  @media (max-width:760px) { .grid { grid-template-columns:1fr; } }
  .card { background:#161b22; border:1px solid #30363d; border-radius:10px; padding:14px 16px; }
  .card.full { grid-column:1 / -1; }
  .card h2 { font-size:12px; color:#8b949e; text-transform:uppercase; letter-spacing:1px; margin-bottom:10px; }
  .kv { display:flex; justify-content:space-between; padding:3px 0; border-bottom:1px dashed #21262d; gap:8px; }
  .kv:last-child { border-bottom:none; }
  .kv .k { color:#8b949e; }
  .kv .v { color:#e6edf3; font-weight:600; text-align:right; word-break:break-all; }
  .big { font-size:34px; font-weight:800; }
  .big small { font-size:14px; color:#8b949e; font-weight:400; }
  .bar { height:14px; background:#21262d; border-radius:7px; overflow:hidden; position:relative; margin:6px 0 2px; }
  .bar > i { display:block; height:100%; background:linear-gradient(90deg,#1f6feb,#58a6ff); transition:width .5s; }
  .bar > i.warn { background:linear-gradient(90deg,#d29922,#e3b341); }
  .bar > i.crit { background:linear-gradient(90deg,#f85149,#ff7b72); }
  .bar > .th { position:absolute; top:0; bottom:0; width:2px; background:#f85149; }
  .bar-lbl { display:flex; justify-content:space-between; font-size:11px; color:#8b949e; }
  .row { display:flex; gap:20px; flex-wrap:wrap; }
  .tag { font-size:11px; padding:1px 7px; border-radius:5px; background:#21263d; color:#8b949e; }
  .tag.on { background:#23863633; color:#3fb950; }
  .tag.off { background:#f8514911; color:#f85149; }
  .tag.warn { background:#9e6a0311; color:#d29922; }
  .tag.disabled { background:#30363d; color:#8b949e; }
  .btn { display:inline-block; padding:5px 12px; border:1px solid #30363d; border-radius:6px;
         background:#21262d; color:#c9d1d9; font:600 12px inherit; cursor:pointer; margin-right:6px; }
  .btn:hover { background:#30363d; }
  .btn.start { background:#1f6f3f22; border-color:#23863655; color:#3fb950; }
  .btn.start:hover { background:#1f6f3f44; }
  .btn.stop { background:#f8514911; border-color:#f8514944; color:#f85149; }
  .btn.stop:hover { background:#f8514933; }
  .btn.restart { background:#1f6feb11; border-color:#1f6feb55; color:#58a6ff; }
  .btn.restart:hover { background:#1f6feb33; }
  .btn:disabled { opacity:.4; cursor:not-allowed; }
  .logbox { background:#0d1117; border:1px solid #30363d; border-radius:6px; padding:8px 10px;
            white-space:pre-wrap; max-height:200px; overflow:auto; font-size:11px; color:#c9d1d9; }
  footer { margin-top:16px; color:#484f58; font-size:11px; text-align:center; }
  details { margin-top:6px; }
  summary { cursor:pointer; color:#8b949e; font-size:11px; }
  .err { color:#f85149; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>🌐 hits4me viewer dashboard</h1>
    <span id="statusPill" class="pill disabled">…</span>
    <span class="sub" id="serviceInfo"></span>
  </header>

  <div class="grid">
    <div class="card full">
      <h2>Memory (container RSS)</h2>
      <div class="row" style="justify-content:space-between;align-items:baseline;">
        <span class="big"><span id="memUsed">–</span> <small>/ <span id="memLimit">–</span> MB</small></span>
        <span class="sub">threshold <span id="memThresh">–</span> MB · peak <span id="memPeak">–</span> MB</span>
      </div>
      <div class="bar" id="memBar"><i id="memFill" style="width:0%"></i><span class="th" id="memThreshMark"></span></div>
      <div class="bar-lbl"><span>0</span><span id="memBarPct">–</span><span id="memLimitMax">–</span></div>
      <div style="margin-top:10px;">
        <div class="bar-lbl"><span>9Hits v6 <span id="nhTag" class="tag">–</span></span><span id="nhMem">– MB</span></div>
        <div class="bar" style="height:9px"><i id="nhFill" style="width:0%"></i></div>
      </div>
      <div style="margin-top:8px;">
        <div class="bar-lbl"><span>FeelingSurf <span id="fsTag" class="tag">–</span></span><span id="fsMem">– MB</span></div>
        <div class="bar" style="height:9px"><i id="fsFill" style="width:0%"></i></div>
      </div>
    </div>

    <div class="card">
      <h2>Dual-viewer mode</h2>
      <div class="kv"><span class="k">configured</span><span class="v" id="cfgMode">–</span></div>
      <div class="kv"><span class="k">effective</span><span class="v" id="effMode">–</span></div>
      <div class="kv"><span class="k">active viewer</span><span class="v" id="activeViewer">–</span></div>
      <div class="kv"><span class="k">time slice</span><span class="v" id="timeSlice">–</span></div>
      <div class="kv"><span class="k">next flip in</span><span class="v" id="nextFlip">–</span></div>
      <div class="kv"><span class="k">memguard restarts</span><span class="v" id="mgInt">–</span></div>
      <div class="kv"><span class="k">last target</span><span class="v" id="mgTarget">–</span></div>
    </div>

    <div class="card">
      <h2>Viewers (24x7 status)</h2>
      <div class="kv"><span class="k">9Hits enabled</span><span class="v" id="nhEnabled">–</span></div>
      <div class="kv"><span class="k">9Hits status</span><span class="v" id="nhStatus">–</span></div>
      <div class="kv"><span class="k">9Hits running</span><span class="v" id="nhRunningFlag">–</span></div>
      <div class="kv"><span class="k">9Hits pid / uptime / restarts</span><span class="v" id="nhPid">–</span></div>
      <div class="kv"><span class="k">9Hits memory (RSS) / procs</span><span class="v" id="nhMemProcs">–</span></div>
      <div class="kv"><span class="k">9Hits last exit / error</span><span class="v" id="nhErr">–</span></div>
      <div class="kv"><span class="k">FeelingSurf enabled</span><span class="v" id="fsEnabled">–</span></div>
      <div class="kv"><span class="k">FeelingSurf status</span><span class="v" id="fsStatus">–</span></div>
      <div class="kv"><span class="k">FeelingSurf running</span><span class="v" id="fsRunningFlag">–</span></div>
      <div class="kv"><span class="k">FS pid / uptime / restarts</span><span class="v" id="fsPid">–</span></div>
      <div class="kv"><span class="k">FS memory (RSS) / procs</span><span class="v" id="fsMemProcs">–</span></div>
      <div class="kv"><span class="k">FS last exit / error</span><span class="v" id="fsErr">–</span></div>
      <div class="kv"><span class="k">low memory flags</span><span class="v" id="lowMem">–</span></div>
    </div>

    <div class="card full">
      <h2>Per-slot controls</h2>
      <div class="row" id="slotButtons" style="gap:8px;flex-wrap:wrap;"></div>
      <div class="row" style="margin-top:12px;gap:18px;flex-wrap:wrap;">
        <div style="flex:1;min-width:380px;">
          <div class="sub" style="margin-bottom:4px;">9Hits log tail (logs/9hits.log)</div>
          <pre class="logbox" id="nhLog">…</pre>
        </div>
        <div style="flex:1;min-width:380px;">
          <div class="sub" style="margin-bottom:4px;">FeelingSurf log tail (logs/feelingsurf.log)</div>
          <pre class="logbox" id="fsLog">…</pre>
        </div>
      </div>
    </div>
  </div>

  <footer id="foot">loading…</footer>
</div>

<script>
const $ = id => document.getElementById(id);
const F = n => (n === null || n === undefined) ? '–' : Number(n).toFixed(1);
const fmt = s => (s === null || s === undefined) ? '–' : String(s);

async function refresh() {
  let d;
  try {
    const r = await fetch('/health', { cache: 'no-store' });
    d = await r.json();
  } catch (e) {
    $('foot').textContent = 'cannot reach /health (' + e + ') - retrying…';
    return;
  }
  const used = d.memory_used_mb, limit = d.memory_limit_mb;
  const thresh = d.hard_threshold_mb;
  const pct = (used && limit) ? Math.min(100, used / limit * 100) : 0;
  const thPct = (thresh && limit) ? Math.min(100, thresh / limit * 100) : 100;

  const s = d.status || 'error';
  const pill = $('statusPill');
  pill.className = 'pill ' + (s === 'ok' ? 'ok' : s === 'restarting' ? 'restarting' : s === 'disabled' ? 'disabled' : 'error');
  pill.textContent = s.toUpperCase();

  $('serviceInfo').textContent = d.service + ' v' + d.version + ' · uptime ' + (d.uptime_seconds !== null ? Math.floor(d.uptime_seconds / 60) + 'm' : '–');

  // memory
  $('memUsed').textContent = F(used);
  $('memLimit').textContent = fmt(limit);
  $('memThresh').textContent = F(thresh);
  $('memPeak').textContent = F(d.memory_peak_mb);
  $('memFill').style.width = pct + '%';
  $('memFill').className = pct > 90 ? 'crit' : pct > 75 ? 'warn' : '';
  $('memThreshMark').style.left = thPct + '%';
  $('memBarPct').textContent = (used && limit) ? Math.round(pct) + '% of budget' : '–';
  $('memLimitMax').textContent = fmt(limit) + ' MB';

  const nh = d.ninehits_rss_mb, fs = d.feelingsurf_rss_mb;
  const nhPct = nh && limit ? Math.min(100, nh / limit * 100) : 0;
  const fsPct = fs && limit ? Math.min(100, fs / limit * 100) : 0;
  $('nhFill').style.width = nhPct + '%';
  $('fsFill').style.width = fsPct + '%';
  $('nhMem').textContent = F(nh) + ' MB';
  $('fsMem').textContent = F(fs) + ' MB';
  setTag($('nhTag'), d.ninehits_running === true ? 'RUN' : d.ninehits_running === false ? 'down' : d.ninehits_running === 'disabled' ? 'off' : '—');
  setTag($('fsTag'), d.feelingsurf_running === true ? 'RUN' : d.feelingsurf_running === false ? 'down' : d.feelingsurf_running === 'disabled' ? 'off' : '—');

  // mode
  $('cfgMode').textContent = fmt(d.dual_viewer_mode);
  $('effMode').textContent = fmt(d.effective_mode);
  $('activeViewer').textContent = fmt(d.active_viewer);
  $('timeSlice').textContent = d.time_slice_seconds ? d.time_slice_seconds + ' s' : '–';
  const nf = d.next_flip_in_seconds;
  $('nextFlip').textContent = nf === null || nf === undefined ? '–' : nf + ' s';
  $('mgInt').textContent = fmt(d.memguard_interventions);
  $('mgTarget').textContent = fmt(d.memguard_last_target);

  // viewers
  $('nhEnabled').textContent = fmt(d.viewer_enabled);
  $('nhStatus').textContent = fmt(d.ninehits_status);
  $('nhRunningFlag').textContent = (d.ninehits && d.ninehits.running === true) ? 'YES (pid ' + fmt(d.ninehits.pid) + ')' :
                                    (d.ninehits_running === 'disabled' ? 'disabled' : 'no');
  $('nhPid').textContent = fmt(d.ninehits_pid) + ' / ' + (d.ninehits ? d.ninehits.uptime : '-') + ' / ' + fmt(d.ninehits_restart_count);
  $('nhMemProcs').textContent = (d.ninehits && d.ninehits.memory_mb != null ? d.ninehits.memory_mb.toFixed(1) + ' MB' : '–') +
                                 ' / ' + (d.ninehits && d.ninehits.child_count != null ? d.ninehits.child_count + ' procs' : '–') +
                                 (d.ninehits && d.ninehits.max_memory_mb > 0 ? ' (cap ' + d.ninehits.max_memory_mb + ' MB)' : '');
  $('nhErr').textContent = fmt(d.ninehits_last_exit_code) + (d.ninehits_last_error ? ' (' + d.ninehits_last_error + ')' : ' (none)');
  $('fsEnabled').textContent = fmt(d.feelingsurf_enabled);
  $('fsStatus').textContent = fmt(d.feelingsurf_status);
  $('fsRunningFlag').textContent = (d.feelingsurf && d.feelingsurf.running === true) ? 'YES (pid ' + fmt(d.feelingsurf.pid) + ')' :
                                    (d.feelingsurf_running === 'disabled' ? 'disabled' : 'no');
  $('fsPid').textContent = fmt(d.feelingsurf_pid) + ' / ' + (d.feelingsurf ? d.feelingsurf.uptime : '-') + ' / ' + fmt(d.feelingsurf_restart_count);
  $('fsMemProcs').textContent = (d.feelingsurf && d.feelingsurf.memory_mb != null ? d.feelingsurf.memory_mb.toFixed(1) + ' MB' : '–') +
                                 ' / ' + (d.feelingsurf && d.feelingsurf.child_count != null ? d.feelingsurf.child_count + ' procs' : '–') +
                                 (d.feelingsurf && d.feelingsurf.max_memory_mb > 0 ? ' (cap ' + d.feelingsurf.max_memory_mb + ' MB)' : '');
  $('fsErr').textContent = fmt(d.feelingsurf_last_exit_code) + (d.feelingsurf_last_error ? ' (' + d.feelingsurf_last_error + ')' : ' (none)');
  $('lowMem').textContent = fmt(d.low_memory);

  // log tails
  $('nhLog').textContent = d.ninehits_log_tail || '(empty)';
  $('fsLog').textContent = d.feelingsurf_log_tail || '(empty)';

  // per-slot controls
  renderSlotButtons(d.slots || {});

  $('foot').textContent = 'updated ' + new Date().toLocaleTimeString() + ' · refresh 2s · data: /health';
}

function setTag(el, text) {
  el.textContent = text;
  el.className = 'tag ' + (text === 'RUN' ? 'on' : text === 'down' ? 'off' : text === 'off' ? 'disabled' : '');
}

function renderSlotButtons(slots) {
  const root = $('slotButtons');
  root.innerHTML = '';
  const order = ['ninehits', 'feelingsurf', 'memguard', 'health'];
  for (const name of order) {
    const s = slots[name];
    if (!s) continue;
    const card = document.createElement('div');
    card.style.cssText = 'background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:10px 12px;min-width:230px;';
    const tag = s.status || '?';
    const enabled = s.enabled !== false;
    card.innerHTML = '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">'
      + '<strong>' + name + '</strong>'
      + '<span class="tag ' + (tag === 'running' ? 'on' : tag === 'crashed' ? 'off' : tag === 'disabled' ? 'disabled' : 'warn') + '">' + tag + '</span>'
      + '</div>'
      + '<div class="sub" style="margin-bottom:6px;">pid ' + fmt(s.pid) + ' · uptime ' + fmt(s.uptime_seconds) + 's · restarts ' + fmt(s.restart_count) + '</div>'
      + '<div>'
      + '<button class="btn start" data-act="start" data-name="' + name + '"' + (enabled ? '' : ' disabled') + '>Start</button>'
      + '<button class="btn stop" data-act="stop" data-name="' + name + '"' + (enabled ? '' : ' disabled') + '>Stop</button>'
      + '<button class="btn restart" data-act="restart" data-name="' + name + '"' + (enabled ? '' : ' disabled') + '>Restart</button>'
      + '</div>';
    root.appendChild(card);
  }
  root.querySelectorAll('button[data-act]').forEach(btn => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      const name = btn.getAttribute('data-name');
      const act = btn.getAttribute('data-act');
      try {
        const r = await fetch('/control/' + encodeURIComponent(name) + '/' + encodeURIComponent(act), { method: 'POST' });
        const j = await r.json().catch(() => ({}));
        btn.title = j.message || (r.ok ? 'queued' : 'failed');
      } catch (e) {
        btn.title = 'request failed: ' + e;
      }
      setTimeout(() => { btn.disabled = false; }, 800);
    });
  });
}

refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


HEALTH_PATHS = ("/health", "/healthz", "/ping")
GUI_PATHS = ("/", "/gui", "/index.html")


# --------------------------------------------------------------------------- #
# Control endpoint (Start/Stop/Restart for a managed slot)
# --------------------------------------------------------------------------- #

def _request_slot_control(name, action):
    """Drop /tmp/supervisor_ctl/<name>.<action> so supervisor.py picks it up.

    We intentionally do NOT touch processes directly from the health server.
    The supervisor owns every long-lived child; talking to them from here
    would race with the supervisor's own reaper and could leave a half-killed
    process tree behind. File droppings are the clean IPC the supervisor
    already implements.
    """
    if action not in ("start", "stop", "restart"):
        return False, "unknown action: %r" % action
    if not _supervisor_alive():
        return False, "supervisor is not running - no slot to control"
    # Pre-flight: refuse requests to a disabled slot (the supervisor would
    # silently no-op, which is confusing for the operator clicking the button).
    sup_state = _supervisor_state()
    slot = (sup_state.get("slots") or {}).get(name)
    if slot is not None and slot.get("enabled") is False and action in ("start", "restart"):
        return False, "%s is disabled by configuration - set the corresponding NINEHITS_ENABLED / FEELINGSURF_ENABLED env var to enable it" % name
    if name not in ("ninehits", "feelingsurf", "memguard", "health"):
        return False, "unknown slot: %r" % name
    try:
        os.makedirs(SUPERVISOR_CTL_DIR, exist_ok=True)
        path = os.path.join(SUPERVISOR_CTL_DIR, "%s.%s" % (name, action))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(int(time.time())))
        return True, "queued %s for %s" % (action, name)
    except OSError as exc:
        return False, "failed to queue control: %s" % exc


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #

def _slot_dict_to_public(name, slot):
    """Translate a supervisor slot dict into the public /health field names.

    Sanity-checks the supervisor-reported PID: a stale state file from a
    previous container run could list a PID that no longer exists. We treat
    that as "no pid" rather than a false running signal, so the dashboard
    does not briefly show a slot as "running" right after the supervisor
    restarts.
    """
    if not slot:
        return {
            "pid": None,
            "uptime_seconds": 0,
            "restart_count": 0,
            "status": "disabled" if not (
                (name == "ninehits" and NINEHITS_ENABLED) or
                (name == "feelingsurf" and FEELINGSURF_ENABLED)
            ) else "stopped",
            "last_exit_code": None,
            "last_error": None,
            "log_tail": "",
        }
    reported_pid = slot.get("pid")
    safe_pid = reported_pid if reported_pid and _pid_alive(int(reported_pid)) else None
    # If the supervisor claims "running" but the pid is not actually alive,
    # the supervisor just hasn't reaped it yet (it ticks every 0.5s). For the
    # dashboard we still trust the supervisor's status string in that case -
    # it is the source of truth and will be corrected within milliseconds.
    return {
        "pid": safe_pid,
        "uptime_seconds": slot.get("uptime_seconds") or 0,
        "restart_count": slot.get("restart_count") or 0,
        "crash_streak": slot.get("crash_streak") or 0,
        "status": slot.get("status"),
        "last_user_request": slot.get("last_user_request"),
        "last_exit_code": slot.get("last_exit_code"),
        "last_error": slot.get("last_error"),
        "log_tail": slot.get("log_tail") or "",
    }


def _slot_to_nested(name, slot):
    """Build the per-viewer nested object the user-facing /health shape
    documents. Independent of the legacy flat fields (which are kept
    for backward compatibility with old uptime bots).

    Shape:
        {
          "status": "running" | "stopped" | "crashed" | "parked" |
                    "starting" | "stopping" | "disabled" | "error",
          "running": true | false,
          "enabled": true | false,
          "pid": 42 | null,
          "uptime": "1d2h3m",          # human readable
          "uptime_seconds": 12345,
          "restarts": 0,
          "last_exit_code": null,
          "last_error": null,
          "memory_mb": 312.1,           # slot's process tree RSS
          "child_count": 7,             # slot's process tree size
          "max_memory_mb": 400,         # 0 = no cap
          "max_children": 50,           # 0 = no cap
          "check_interval_seconds": 30,
          "log_file": "/logs/9hits.log",
        }
    """
    if not slot:
        enabled = (
            (name == "ninehits" and NINEHITS_ENABLED)
            or (name == "feelingsurf" and FEELINGSURF_ENABLED)
        )
        return {
            "status": "disabled" if not enabled else "stopped",
            "running": False,
            "enabled": enabled,
            "pid": None,
            "uptime": "0s",
            "uptime_seconds": 0,
            "restarts": 0,
            "last_exit_code": None,
            "last_error": None,
            "memory_mb": None,
            "child_count": None,
            "max_memory_mb": 0,
            "max_children": 0,
            "check_interval_seconds": 0,
            "log_file": None,
        }
    # Treat the supervisor's string status as authoritative for the
    # nested shape; cross-check with a live pid probe only for the
    # boolean `running` field.
    status = slot.get("status") or "stopped"
    pid = slot.get("pid")
    if pid and not _pid_alive(int(pid)):
        pid = None
    running = (status == "running")
    uptime_s = int(slot.get("uptime_seconds") or 0)
    return {
        "status": status,
        "running": running,
        "enabled": bool(slot.get("enabled", True)),
        "pid": pid,
        "uptime": slot.get("uptime") or _fmt_duration(uptime_s),
        "uptime_seconds": uptime_s,
        "restarts": int(slot.get("restart_count") or 0),
        "last_exit_code": slot.get("last_exit_code"),
        "last_error": slot.get("last_error"),
        "memory_mb": slot.get("memory_mb"),
        "child_count": slot.get("child_count"),
        "max_memory_mb": int(slot.get("max_memory_mb") or 0),
        "max_children": int(slot.get("max_children") or 0),
        "check_interval_seconds": int(slot.get("check_interval_seconds") or 0),
        "log_file": slot.get("log_file"),
    }


def _fmt_duration(seconds):
    seconds = max(0, int(seconds or 0))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return "%dd%dh%dm" % (days, hours, minutes)
    if hours:
        return "%dh%dm%ds" % (hours, minutes, secs)
    if minutes:
        return "%dm%ds" % (minutes, secs)
    return "%ds" % secs


def _nh_running(slot):
    if not NINEHITS_ENABLED:
        return None
    if slot:
        st = slot.get("status")
        if st == "running":
            return True
        if st in ("stopped", "crashed", "starting", "stopping"):
            return False
    # Legacy bash-only fallback
    return _nh_viewer_running_legacy()


def _feelingsurf_running(slot):
    if not FEELINGSURF_ENABLED:
        return None
    if slot:
        st = slot.get("status")
        if st == "running":
            return True
        if st in ("stopped", "crashed", "starting", "stopping"):
            return False
    return _feelingsurf_running_legacy()


class Handler(BaseHTTPRequestHandler):
    server_version = "hits4me-health/" + VERSION
    protocol_version = "HTTP/1.1"

    def _path(self):
        return self.path.split("?", 1)[0]

    @_quiet
    def do_GET(self):
        path = self._path()
        if path in GUI_PATHS:
            self._send(
                200, {"Cache-Control": "no-store"},
                GUI_HTML.encode("utf-8"), "text/html; charset=utf-8",
            )
        elif path in HEALTH_PATHS:
            self._health()
        elif path == "/slots":
            self._slots()
        elif path.startswith("/logs/"):
            self._log_tail(path[len("/logs/"):])
        elif path.startswith("/control/"):
            # GET form of the control endpoint - handy when curl is hard to POST.
            parts = path[len("/control/"):].split("/", 1)
            if len(parts) == 2:
                self._control(parts[0], parts[1])
            else:
                self._send(404, {}, b"bad control path\n", "text/plain")
        else:
            self._send(404, {}, b"not found\n", "text/plain")

    @_quiet
    def do_POST(self):
        path = self._path()
        if path.startswith("/control/"):
            parts = path[len("/control/"):].split("/", 1)
            if len(parts) == 2:
                self._control(parts[0], parts[1])
            else:
                self._send(404, {}, b"bad control path\n", "text/plain")
        else:
            self._send(404, {}, b"not found\n", "text/plain")

    @_quiet
    def do_HEAD(self):
        path = self._path()
        if path in GUI_PATHS or path in HEALTH_PATHS or path == "/slots" or path.startswith("/control/") or path.startswith("/logs/"):
            self.send_response(200)
        else:
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _slots(self):
        data = _supervisor_state() or {"slots": {}}
        body = json.dumps(data, indent=1, sort_keys=True).encode("utf-8")
        self._send(200, {"Cache-Control": "no-store"}, body, "application/json")

    def _log_tail(self, name):
        path = LOG_PATH_FOR_SLOT.get(name)
        if not path:
            self._send(404, {}, b"unknown log name\n", "text/plain")
            return
        text = _tail_log(path)
        self._send(200, {"Cache-Control": "no-store"},
                   text.encode("utf-8", "replace"), "text/plain; charset=utf-8")

    def _control(self, name, action):
        ok, msg = _request_slot_control(name, action)
        body = json.dumps({"ok": ok, "name": name, "action": action, "message": msg},
                          sort_keys=True).encode("utf-8")
        self._send(200 if ok else 400, {"Cache-Control": "no-store"},
                   body, "application/json")

    def _health(self):
        # Read supervisor state once.
        sup_state = _supervisor_state()
        slots = (sup_state.get("slots") if isinstance(sup_state, dict) else {}) or {}
        nh_slot = slots.get("ninehits")
        fs_slot = slots.get("feelingsurf")
        mg_slot = slots.get("memguard")
        hc_slot = slots.get("health")

        # Per-slot running booleans (true / false / None = N/A).
        nh_running = _nh_running(nh_slot)
        fs_running = _feelingsurf_running(fs_slot)
        mg_status = (mg_slot or {}).get("status") or "stopped"
        hc_status = (hc_slot or {}).get("status") or "running"
        supervisor_alive = _supervisor_alive()

        # Per-slot public view.
        nh_pub = _slot_dict_to_public("ninehits", nh_slot)
        fs_pub = _slot_dict_to_public("feelingsurf", fs_slot)

        # Top-level status. The new shape supports "degraded" - one
        # viewer up, the other down - which is more useful for an
        # uptime monitor than the binary "ok"/"restarting" pair, but
        # we keep "ok" when everything is up and "error" when the
        # supervisor is gone, so the previous semantics are preserved.
        enabled_running = []
        enabled_configured = []
        if NINEHITS_ENABLED:
            enabled_configured.append(True)
            enabled_running.append(nh_running is True)
        if FEELINGSURF_ENABLED:
            enabled_configured.append(True)
            enabled_running.append(fs_running is True)
        if not enabled_configured:
            # No viewers at all -> still ok, container is up.
            status = "ok"
        elif all(enabled_running):
            status = "ok"
        elif supervisor_alive and enabled_running:
            # At least one viewer is up but at least one is down. The
            # supervisor will recover it automatically - this is the
            # "degraded" state.
            status = "degraded"
        elif supervisor_alive:
            # Both viewers enabled, neither up yet (very early in the
            # boot). Same idea as degraded but for the cold-start case.
            status = "restarting"
        else:
            status = "error"

        # Nested per-viewer shape (the documented user-facing one).
        nh_nested = _slot_to_nested("ninehits", nh_slot)
        fs_nested = _slot_to_nested("feelingsurf", fs_slot)

        mg = runtime_memory_state()
        body = json.dumps(
            {
                "service": "hits4me-combined-viewer",
                "version": VERSION,
                "status": status,
                # Per-viewer nested objects (the documented shape).
                "ninehits": nh_nested,
                "feelingsurf": fs_nested,
                # Top-level per-viewer booleans (kept for back-compat:
                # true | false | "disabled" | null).
                "ninehits_running": (
                    "disabled" if not NINEHITS_ENABLED
                    else (True if nh_running is True else False)
                ),
                "feelingsurf_running": (
                    "disabled" if not FEELINGSURF_ENABLED
                    else (True if fs_running is True else False)
                ),
                # Back-compat: the older "feelingsurf" / "ninehits"
                # top-level booleans. We redefine them as the per-viewer
                # nested object too (so naive `if d.ninehits` checks
                # still work because the nested dict is truthy), but a
                # monitor that just wants a boolean should use
                # ``ninehits_running`` or the documented shape.
                "supervisor_running": supervisor_alive,
                "supervisor_version": (sup_state or {}).get("version") if sup_state else None,
                # 9Hits (legacy flat fields - older uptime bots).
                "viewer_enabled": NINEHITS_ENABLED,
                "viewer_running": nh_running,
                "viewer_pid": nh_pub["pid"],
                "viewer_phase": (
                    "run" if nh_running is True
                    else "init" if (nh_slot or {}).get("status") == "starting"
                    else "down"
                ) if NINEHITS_ENABLED else None,
                "viewer_silent_seconds": _nh_silent_seconds_legacy() if not nh_slot and NINEHITS_ENABLED else None,
                "xvfb_running": _xvfb_running_legacy() if not nh_slot and NINEHITS_ENABLED else None,
                "restarts": nh_pub["restart_count"],
                "ninehits_pid": nh_pub["pid"],
                "ninehits_uptime_seconds": nh_pub["uptime_seconds"],
                "ninehits_restart_count": nh_pub["restart_count"],
                "ninehits_crash_streak": nh_pub["crash_streak"],
                "ninehits_status": nh_pub["status"],
                "ninehits_last_exit_code": nh_pub["last_exit_code"],
                "ninehits_last_error": nh_pub["last_error"],
                "ninehits_log_tail": nh_pub["log_tail"],
                # FeelingSurf (legacy flat fields).
                "feelingsurf_enabled": FEELINGSURF_ENABLED,
                "feelingsurf_pid": fs_pub["pid"],
                "feelingsurf_uptime_seconds": fs_pub["uptime_seconds"],
                "feelingsurf_restart_count": fs_pub["restart_count"],
                "feelingsurf_crash_streak": fs_pub["crash_streak"],
                "feelingsurf_status": fs_pub["status"],
                "feelingsurf_last_exit_code": fs_pub["last_exit_code"],
                "feelingsurf_last_error": fs_pub["last_error"],
                "feelingsurf_log_tail": fs_pub["log_tail"],
                # memguard / health
                "memguard_status": mg_status,
                "memguard_pid": (mg_slot or {}).get("pid"),
                "memguard_uptime_seconds": (mg_slot or {}).get("uptime_seconds") or 0,
                "memguard_restart_count": (mg_slot or {}).get("restart_count") or 0,
                "health_status": hc_status,
                "health_pid": (hc_slot or {}).get("pid") or os.getpid(),
                # Per-slot full snapshot (used by the dashboard buttons).
                "slots": slots,
                # Memory / mode (from memguard or the read-only fallback).
                "dual_viewer_mode": DUAL_VIEWER_MODE,
                "effective_mode": effective_mode(),
                "active_viewer": mg.get("active_viewer"),
                "time_slice_seconds": mg.get("time_slice_seconds"),
                "memory_used_mb": mg.get("memory_used_mb"),
                "memory_limit_mb": mg.get("memory_limit_mb"),
                "memory_peak_mb": mg.get("memory_peak_mb"),
                "hard_threshold_mb": mg.get("hard_threshold_mb"),
                "ninehits_rss_mb": mg.get("ninehits_rss_mb"),
                "feelingsurf_rss_mb": mg.get("feelingsurf_rss_mb"),
                "memguard_interventions": mg.get("interventions"),
                "memguard_last_target": mg.get("last_target"),
                "next_flip_in_seconds": mg.get("next_flip_in_seconds"),
                "low_memory": LOW_MEMORY,
                "uptime_seconds": int(time.time() - STARTED_AT),
            },
            sort_keys=True,
        )
        # 200 even when degraded (the supervisor is still in control and
        # will recover). 503 only when the supervisor itself is gone.
        code = 200 if status != "error" else 503
        self._send(code, {"Cache-Control": "no-store"},
                   body.encode("utf-8"), "application/json")

    def _send(self, code, headers, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def log_message(self, _fmt, *_args):
        pass  # keep logs tidy; viewer dashboard is already in the logs


def _reap(_signum=None, _frame=None):
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
            if pid == 0:
                break
        except (ChildProcessError, OSError):
            break


def _shutdown(_signum=None, _frame=None):
    """Best-effort shutdown - the Python supervisor handles the real
    SIGTERM propagation. We just stop the HTTP server and exit so the
    supervisor can restart us cleanly."""
    print("[health] received signal - shutting down", flush=True)
    sys.exit(0)


def main():
    for sig_num in (signal.SIGTERM, signal.SIGINT, signal.SIGQUIT):
        try:
            signal.signal(sig_num, _shutdown)
        except (ValueError, OSError):
            pass
    try:
        signal.signal(signal.SIGCHLD, _reap)
    except (ValueError, OSError):
        pass
    server = Server(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print("[health] listening on 0.0.0.0:%d (GET /health, /slots, /logs/<n>, POST /control/<n>/<action>)" % PORT, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
