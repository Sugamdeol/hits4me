#!/usr/bin/env python3
"""
Combined HTTP health endpoint for the 9Hits + FeelingSurf container.
Stdlib only, no dependencies.

Serves GET/HEAD on 0.0.0.0:$PORT for:
    /  /health  /healthz  /ping

Response (200) example:
    {
      "service": "9hits-viewer",
      "version": "1.0.0",
      "status": "ok",            # "ok" | "restarting" | "error"
      "viewer_running": true,    # is the viewer process alive right now?
      "supervisor_running": true,
      "viewer_pid": 12,
      "restarts": 0,             # how many times the viewer was relaunched
      "uptime_seconds": 123
    }

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
FEELINGSURF_SUPERVISOR_PID = int(
    os.environ.get("FEELINGSURF_SUPERVISOR_PID", "0") or 0
)
FEELINGSURF_ENABLED = os.environ.get("FEELINGSURF_ENABLED", "yes").lower() not in (
    "0", "no", "false", "off"
)
FEELINGSURF_PORT = int(os.environ.get("FEELINGSURF_PORT", "3000") or 3000)
PID_FILE = os.environ.get("VIEWER_PID_FILE", "/tmp/viewer.pid")
RESTART_FILE = os.environ.get("VIEWER_RESTART_FILE", "/tmp/viewer.restarts")
FEELINGSURF_PID_FILE = "/tmp/feelingsurf.pid"
FEELINGSURF_RESTART_FILE = "/tmp/feelingsurf.restarts"
STARTED_AT = time.time()
VERSION = "2.0.0"

HEALTH_PATHS = ("/", "/health", "/healthz", "/ping")


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
        FEELINGSURF_SUPERVISOR_PID,
        _read_int(FEELINGSURF_PID_FILE),
    ):
        if pid > 1:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    sys.exit(0)


class Handler(BaseHTTPRequestHandler):
    server_version = "hits4me-health/" + VERSION
    protocol_version = "HTTP/1.1"

    def _path(self):
        return self.path.split("?", 1)[0]

    def do_GET(self):
        if self._path() in HEALTH_PATHS:
            self._health()
        else:
            body = b"not found\n"
            self._send(404, {}, body, "text/plain")

    def do_HEAD(self):
        if self._path() in HEALTH_PATHS:
            self.send_response(200)
        else:
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _health(self):
        viewer = viewer_running()
        supervisor = supervisor_running()
        feelingsurf = feelingsurf_running()
        feelingsurf_supervisor = feelingsurf_supervisor_running()
        supervisors_ok = supervisor and feelingsurf_supervisor is not False
        viewers_ok = viewer and feelingsurf is not False
        if viewers_ok and supervisors_ok:
            status = "ok"
        elif supervisors_ok:
            status = "restarting"
        else:
            status = "error"
        body = json.dumps(
            {
                "service": "hits4me-combined-viewer",
                "version": VERSION,
                "status": status,
                "viewer_running": viewer,
                "supervisor_running": supervisor,
                "viewer_pid": _read_int(PID_FILE),
                "restarts": _read_int(RESTART_FILE),
                "feelingsurf_enabled": FEELINGSURF_ENABLED,
                "feelingsurf_running": feelingsurf,
                "feelingsurf_supervisor_running": feelingsurf_supervisor,
                "feelingsurf_pid": _read_int(FEELINGSURF_PID_FILE),
                "feelingsurf_restarts": _read_int(FEELINGSURF_RESTART_FILE),
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
        self.wfile.write(body)

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
