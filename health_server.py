#!/usr/bin/env python3
"""
Tiny HTTP health endpoint for the 9Hits viewer (Render + uptime bots).
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

Two ways to use it:
  * as a script (Docker entrypoint): reads /tmp/viewer.pid + /tmp/viewer.restarts
  * as a module (run_native.py): ``serve(port, status_provider)`` /
    ``serve_in_thread(port, status_provider)`` with a custom status callable.
"""

import json
import os
import signal
import sys
import threading
import time

try:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as Server
except ImportError:  # Python < 3.7
    from http.server import BaseHTTPRequestHandler, HTTPServer as Server

PORT = int(os.environ.get("PORT", "10000") or 10000)
SUPERVISOR_PID = int(os.environ.get("SUPERVISOR_PID", "0") or 0)
PID_FILE = os.environ.get("VIEWER_PID_FILE", "/tmp/viewer.pid")
RESTART_FILE = os.environ.get("VIEWER_RESTART_FILE", "/tmp/viewer.restarts")
STARTED_AT = time.time()
VERSION = "1.0.0"

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


def default_status():
    """Status dict derived from the pid/restart files written by start.sh."""
    viewer = viewer_running()
    supervisor = supervisor_running()
    return {
        "viewer_running": viewer,
        "supervisor_running": supervisor,
        "viewer_pid": _read_int(PID_FILE),
        "restarts": _read_int(RESTART_FILE),
        "uptime_seconds": int(time.time() - STARTED_AT),
    }


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
    for pid in (SUPERVISOR_PID, _read_int(PID_FILE)):
        if pid > 1:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    sys.exit(0)


def make_handler(status_provider):
    """Build a BaseHTTPRequestHandler subclass bound to ``status_provider``."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "hits4me-health/" + VERSION
        protocol_version = "HTTP/1.1"

        def _path(self):
            return self.path.split("?", 1)[0]

        def do_GET(self):
            if self._path() in HEALTH_PATHS:
                self._health()
            else:
                self._send(404, {}, b"not found\n", "text/plain")

        def do_HEAD(self):
            if self._path() in HEALTH_PATHS:
                self.send_response(200)
            else:
                self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _health(self):
            try:
                info = dict(status_provider() or {})
            except Exception as exc:  # never let a bad probe 500 the endpoint
                info = {
                    "viewer_running": False,
                    "supervisor_running": False,
                    "error": str(exc)[:200],
                }

            viewer = bool(info.get("viewer_running"))
            supervisor = bool(info.get("supervisor_running"))
            if viewer and supervisor:
                status = "ok"
            elif supervisor:
                status = "restarting"
            else:
                status = "error"

            payload = {
                "service": "9hits-viewer",
                "version": VERSION,
                "status": status,
            }
            payload.update(info)
            body = json.dumps(payload, sort_keys=True, default=str)
            code = 200 if supervisor else 503
            self._send(
                code,
                {"Cache-Control": "no-store"},
                body.encode("utf-8"),
                "application/json",
            )

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

    return Handler


def make_server(port=None, status_provider=None, host="0.0.0.0"):
    port = PORT if port is None else int(port)
    provider = status_provider or default_status
    server = Server((host, port), make_handler(provider))
    server.daemon_threads = True  # don't let a slow client delay shutdown
    return server


def serve_in_thread(port=None, status_provider=None, host="0.0.0.0"):
    """Start the health endpoint on a daemon thread; returns the server."""
    server = make_server(port, status_provider, host)
    thread = threading.Thread(
        target=server.serve_forever, name="health-server", daemon=True
    )
    thread.start()
    return server


def serve(port=None, status_provider=None, host="0.0.0.0"):
    """Blocking variant used by the Docker entrypoint."""
    server = make_server(port, status_provider, host)
    print(
        "[health] listening on %s:%d (GET /health)" % (host, server.server_address[1]),
        flush=True,
    )
    server.serve_forever()


# Backwards compatible alias: the old module-level Handler class.
Handler = make_handler(default_status)


def main():
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGCHLD, _reap)
    serve(PORT)


if __name__ == "__main__":
    main()
