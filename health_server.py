#!/usr/bin/env python3
"""
Combined HTTP health endpoint for the 9Hits + FeelingSurf container.
Stdlib only, no dependencies.

Serves GET/HEAD on 0.0.0.0:$PORT for:
    /  /gui  /index.html   -> dashboard GUI (HTML, polls /health every 2s)
    /health  /healthz  /ping -> JSON status

Response (200) example:
    {
      "service": "hits4me-combined-viewer",
      "version": "3.1.0",
      "status": "ok",                 # "ok" | "restarting" | "error"
      "viewer_enabled": true,         # is 9Hits toggled on (NINEHITS_ENABLED)?
      "viewer_running": true,         # is the 9Hits run-pass process alive?
      "supervisor_running": true,
      "viewer_pid": 12,
      "viewer_phase": "run",          # "init" (applying config) | "run" | "down"
      "viewer_silent_seconds": 3,     # output heartbeat age (health of the logs)
      "xvfb_running": true,           # the 9Hits virtual display (:99)
      "restarts": 0,                  # how many times the viewer was relaunched
      "feelingsurf_running": true,
      "uptime_seconds": 123,
      "dual_viewer_mode": "auto",          # configured DUAL_VIEWER_MODE
      "effective_mode": "time-slice",      # auto may escalate to time-slice
      "active_viewer": "feelingsurf",      # who owns the RAM right now
      "memory_used_mb": 412.5,             # total unique memory, PSS (memguard)
      "memory_limit_mb": 512,
      "memory_peak_mb": 498.1,
      "ninehits_rss_mb": 0.0,              # per-viewer unique memory, PSS
      "feelingsurf_rss_mb": 331.2,
      "next_flip_in_seconds": 412          # countdown (time-slice mode only)
    }

The last block comes from memguard.py's /tmp/memguard.json and is all `null`
when memguard is not running (DUAL_VIEWER_MODE=off or an older image).

When NINEHITS_ENABLED=no (the default) the 9Hits fields (`viewer_running`,
`viewer_phase`, `viewer_silent_seconds`, `xvfb_running`) are `null` and
`viewer_enabled` is `false`; `status` is still `ok` while FeelingSurf is up.

Semantics:
  * Always 200 while the supervisor is alive, so Render never kill-loops the
    service (the viewer restarts itself on crash anyway).
  * 503 only if the supervisor itself is gone (Render will then restart the
    container, which is the correct recovery).
  * For uptime bots: monitor the keyword `"status": "ok"` to be alerted when
    the viewer is crash-looping while the container is still up.
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

PORT = int(os.environ.get("PORT", "10000") or 10000)
SUPERVISOR_PID = int(os.environ.get("SUPERVISOR_PID", "0") or 0)
XVFB_SUPERVISOR_PID = int(os.environ.get("XVFB_SUPERVISOR_PID", "0") or 0)
VNC_SUPERVISOR_PID = int(os.environ.get("VNC_SUPERVISOR_PID", "0") or 0)
FEELINGSURF_SUPERVISOR_PID = int(
    os.environ.get("FEELINGSURF_SUPERVISOR_PID", "0") or 0
)
MEMGUARD_PID = int(os.environ.get("MEMGUARD_PID", "0") or 0)
DUAL_VIEWER_MODE = os.environ.get("DUAL_VIEWER_MODE", "auto")
LOW_MEMORY = os.environ.get("LOW_MEMORY", "auto")
MEMGUARD_STATE_FILE = "/tmp/memguard.json"
MEMGUARD_MODE_FILE = "/tmp/memguard.mode"
FEELINGSURF_ENABLED = os.environ.get("FEELINGSURF_ENABLED", "yes").lower() not in (
    "0", "no", "false", "off"
)
# 9Hits is OFF by default so the container fits on 512 MB free instances.
NINEHITS_ENABLED = os.environ.get("NINEHITS_ENABLED", "no").lower() not in (
    "0", "no", "false", "off"
)
FEELINGSURF_PORT = int(os.environ.get("FEELINGSURF_PORT", "3000") or 3000)
PID_FILE = os.environ.get("VIEWER_PID_FILE", "/tmp/viewer.pid")
RESTART_FILE = os.environ.get("VIEWER_RESTART_FILE", "/tmp/viewer.restarts")
STATE_FILE = os.environ.get("VIEWER_STATE_FILE", "/tmp/viewer.state")
HEARTBEAT_FILE = os.environ.get("VIEWER_HEARTBEAT_FILE", "/tmp/viewer.lastoutput")
XVFB_PID_FILE = "/tmp/xvfb.pid"
FEELINGSURF_PID_FILE = "/tmp/feelingsurf.pid"
FEELINGSURF_RESTART_FILE = "/tmp/feelingsurf.restarts"
STARTED_AT = time.time()
VERSION = "3.1.0"


def memguard_state():
    """Fresh memguard.py snapshot (memory stats / mode / turns), or {}."""
    try:
        with open(MEMGUARD_STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def effective_mode():
    """Configured vs actual mode: memguard may escalate auto -> time-slice."""
    try:
        with open(MEMGUARD_MODE_FILE, "r", encoding="utf-8") as fh:
            mode = fh.read().strip()
        if mode:
            return mode
    except OSError:
        pass
    return None

HEALTH_PATHS = ("/health", "/healthz", "/ping")
GUI_PATHS = ("/", "/gui", "/index.html")

# Self-contained dashboard (no external assets). It polls GET /health and
# renders viewer status + live memory usage. Static text only - costs ~0 RAM.
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
  .wrap { max-width:960px; margin:0 auto; }
  header { display:flex; align-items:center; gap:12px; margin-bottom:16px; flex-wrap:wrap; }
  .sub { color:#8b949e; font-size:12px; }
  .pill { padding:3px 10px; border-radius:999px; font-size:12px; font-weight:700; }
  .pill.ok { background:#1f6f3f33; color:#3fb950; border:1px solid #23863655; }
  .pill.restarting { background:#9e6a0311; color:#d29922; border:1px solid #9e6a0344; }
  .pill.error { background:#f8514911; color:#f85149; border:1px solid #f8514944; }
  .pill.neutral { background:#30363d33; color:#8b949e; border:1px solid #30363d; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  @media (max-width:760px) { .grid { grid-template-columns:1fr; } }
  .card { background:#161b22; border:1px solid #30363d; border-radius:10px; padding:14px 16px; }
  .card.full { grid-column:1 / -1; }
  .card h2 { font-size:12px; color:#8b949e; text-transform:uppercase; letter-spacing:1px; margin-bottom:10px; }
  .kv { display:flex; justify-content:space-between; padding:3px 0; border-bottom:1px dashed #21262d; }
  .kv:last-child { border-bottom:none; }
  .kv .k { color:#8b949e; }
  .kv .v { color:#e6edf3; font-weight:600; }
  .big { font-size:34px; font-weight:800; }
  .big small { font-size:14px; color:#8b949e; font-weight:400; }
  .bar { height:14px; background:#21262d; border-radius:7px; overflow:hidden; position:relative; margin:6px 0 2px; }
  .bar > i { display:block; height:100%; background:linear-gradient(90deg,#1f6feb,#58a6ff); transition:width .5s; }
  .bar > i.warn { background:linear-gradient(90deg,#d29922,#e3b341); }
  .bar > i.crit { background:linear-gradient(90deg,#f85149,#ff7b72); }
  .bar > .th { position:absolute; top:0; bottom:0; width:2px; background:#f85149; }
  .bar-lbl { display:flex; justify-content:space-between; font-size:11px; color:#8b949e; }
  .row { display:flex; gap:20px; flex-wrap:wrap; }
  .tag { font-size:11px; padding:1px 7px; border-radius:5px; background:#21262d; color:#8b949e; }
  .tag.on { background:#23863633; color:#3fb950; }
  .tag.off { background:#f8514911; color:#f85149; }
  footer { margin-top:16px; color:#484f58; font-size:11px; text-align:center; }
  .err { color:#f85149; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>🌐 hits4me viewer dashboard</h1>
    <span id="statusPill" class="pill neutral">…</span>
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
      <h2>Viewers</h2>
      <div class="kv"><span class="k">9Hits enabled</span><span class="v" id="nhEnabled">–</span></div>
      <div class="kv"><span class="k">9Hits running</span><span class="v" id="nhRunning">–</span></div>
      <div class="kv"><span class="k">phase / pid</span><span class="v" id="nhPhase">–</span></div>
      <div class="kv"><span class="k">silent (s) / restarts</span><span class="v" id="nhSilent">–</span></div>
      <div class="kv"><span class="k">FeelingSurf enabled</span><span class="v" id="fsEnabled">–</span></div>
      <div class="kv"><span class="k">FeelingSurf running</span><span class="v" id="fsRunning">–</span></div>
      <div class="kv"><span class="k">FS pid / restarts</span><span class="v" id="fsPid">–</span></div>
      <div class="kv"><span class="k">low memory flags</span><span class="v" id="lowMem">–</span></div>
    </div>
  </div>

  <footer id="foot">loading…</footer>
</div>

<script>
const $ = id => document.getElementById(id);
const F = n => (n === null || n === undefined) ? '–' : Number(n).toFixed(1);
const fmt = s => (s === null || s === undefined || s === '') ? '–' : String(s);
const state = { pill: 'neutral' };

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

  // status pill
  const s = d.status || 'error';
  const pill = $('statusPill');
  pill.className = 'pill ' + s;
  pill.textContent = s.toUpperCase();
  pill.style.background = s === 'ok' ? '#23863633' : s === 'restarting' ? '#9e6a0311' : '#f8514911';
  pill.style.color = s === 'ok' ? '#3fb950' : s === 'restarting' ? '#d29922' : '#f85149';

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
  setTag($('nhTag'), d.viewer_enabled === true ? (d.viewer_running === true ? 'RUN' : 'down') : (d.viewer_enabled === false ? 'off' : '—'));
  setTag($('fsTag'), d.feelingsurf_enabled === true ? (d.feelingsurf_running === true ? 'RUN' : 'down') : (d.feelingsurf_enabled === false ? 'off' : '—'));

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
  $('nhRunning').textContent = fmt(d.viewer_running);
  $('nhPhase').textContent = (fmt(d.viewer_phase)) + ' / ' + fmt(d.viewer_pid);
  $('nhSilent').textContent = (d.viewer_silent_seconds === null ? '–' : d.viewer_silent_seconds) + ' / ' + fmt(d.restarts);
  $('fsEnabled').textContent = fmt(d.feelingsurf_enabled);
  $('fsRunning').textContent = fmt(d.feelingsurf_running);
  $('fsPid').textContent = fmt(d.feelingsurf_pid) + ' / ' + fmt(d.feelingsurf_restarts);
  $('lowMem').textContent = fmt(d.low_memory);

  $('foot').textContent = 'updated ' + new Date().toLocaleTimeString() + ' · refresh 2s · data: /health';
}

function setTag(el, text) {
  el.textContent = text;
  el.className = 'tag ' + (text === 'RUN' ? 'on' : (text === 'down' ? 'off' : ''));
}

refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


def _read_int(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0


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


def viewer_running():
    return _pid_alive(_read_int(PID_FILE))


def viewer_phase():
    """Current 9Hits lifecycle phase: init | run | down (None if unknown)."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            phase = fh.read().strip()
            return phase or None
    except OSError:
        return None


def viewer_silent_seconds():
    """Seconds since the viewer last printed anything (None if no data yet)."""
    try:
        return int(time.time() - os.stat(HEARTBEAT_FILE).st_mtime)
    except OSError:
        return None


def xvfb_running():
    return _pid_alive(_read_int(XVFB_PID_FILE))


def supervisor_running():
    if SUPERVISOR_PID <= 1:
        return True  # pid unknown -> assume fine
    return _pid_alive(SUPERVISOR_PID)


def feelingsurf_running():
    """Require both its managed process and built-in HTTP endpoint."""
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


def feelingsurf_supervisor_running():
    if not FEELINGSURF_ENABLED:
        return None
    return _pid_alive(FEELINGSURF_SUPERVISOR_PID)


def _reap(_signum=None, _frame=None):
    """Reap orphaned children (harmless if there are none)."""
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
            if pid == 0:
                break
        except (ChildProcessError, OSError):
            break


def _shutdown(_signum=None, _frame=None):
    """Forward SIGTERM/SIGINT to the supervisor and the current viewer so the
    container shuts down cleanly when Render stops or redeploys it.

    The supervisor goes first so it can't race a viewer relaunch while we
    terminate; run_pty.py then forwards the signal to the viewer itself."""
    for pid in (
        SUPERVISOR_PID,
        _read_int(PID_FILE),
        XVFB_SUPERVISOR_PID,
        _read_int(XVFB_PID_FILE),
        VNC_SUPERVISOR_PID,
        FEELINGSURF_SUPERVISOR_PID,
        _read_int(FEELINGSURF_PID_FILE),
        MEMGUARD_PID,
    ):
        if pid > 1:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    sys.exit(0)


def _quiet(func):
    """Swallow client-disconnect errors (BrokenPipeError & friends).

    Render's health checker and uptime bots routinely hang up before reading
    the response body; without this every such abort dumped a long
    BrokenPipeError traceback into the logs.
    """

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


class Handler(BaseHTTPRequestHandler):
    server_version = "hits4me-health/" + VERSION
    protocol_version = "HTTP/1.1"

    def _path(self):
        return self.path.split("?", 1)[0]

    @_quiet
    def do_GET(self):
        if self._path() in GUI_PATHS:
            self._send(
                200,
                {"Cache-Control": "no-store"},
                GUI_HTML.encode("utf-8"),
                "text/html; charset=utf-8",
            )
        elif self._path() in HEALTH_PATHS:
            self._health()
        else:
            body = b"not found\n"
            self._send(404, {}, body, "text/plain")

    @_quiet
    def do_HEAD(self):
        if self._path() in GUI_PATHS or self._path() in HEALTH_PATHS:
            self.send_response(200)
        else:
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _health(self):
        # When 9Hits is disabled the viewer-specific health signals are
        # irrelevant -> report them as null so monitors don't flag a missing
        # viewer; `viewer_enabled: false` is the source of truth instead.
        viewer = viewer_running() if NINEHITS_ENABLED else None
        supervisor = supervisor_running()
        feelingsurf = feelingsurf_running()
        feelingsurf_supervisor = feelingsurf_supervisor_running()
        supervisors_ok = supervisor and feelingsurf_supervisor is not False
        # `is not False` treats a disabled viewer (None) as healthy, so the
        # combined status stays "ok" whenever the enabled viewers are up.
        viewers_ok = (viewer is not False) and (feelingsurf is not False)
        if viewers_ok and supervisors_ok:
            status = "ok"
        elif supervisors_ok:
            status = "restarting"
        else:
            status = "error"
        mg = memguard_state()
        body = json.dumps(
            {
                "service": "hits4me-combined-viewer",
                "version": VERSION,
                "status": status,
                "viewer_enabled": NINEHITS_ENABLED,
                "viewer_running": viewer,
                "supervisor_running": supervisor,
                "viewer_pid": _read_int(PID_FILE),
                "viewer_phase": viewer_phase() if NINEHITS_ENABLED else None,
                "viewer_silent_seconds": viewer_silent_seconds() if NINEHITS_ENABLED else None,
                "xvfb_running": xvfb_running() if NINEHITS_ENABLED else None,
                "restarts": _read_int(RESTART_FILE),
                "feelingsurf_enabled": FEELINGSURF_ENABLED,
                "feelingsurf_running": feelingsurf,
                "feelingsurf_supervisor_running": feelingsurf_supervisor,
                "feelingsurf_pid": _read_int(FEELINGSURF_PID_FILE),
                "feelingsurf_restarts": _read_int(FEELINGSURF_RESTART_FILE),
                # 512MB dual-viewer management (memguard.py). All fields are
                # None when memguard is not running.
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
        code = 200 if supervisors_ok else 503
        self._send(code, {"Cache-Control": "no-store"}, body.encode("utf-8"), "application/json")

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
            pass  # client hung up before reading; nothing to do about it

    def log_message(self, _fmt, *_args):
        pass  # keep logs tidy; the viewer dashboard is already in the logs


def main():
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGCHLD, _reap)
    server = Server(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True  # don't let a slow client delay shutdown
    print("[health] listening on 0.0.0.0:%d (GET /health)" % PORT, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
