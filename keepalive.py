#!/usr/bin/env python3
"""
keepalive.py - lightweight HTTP keep-alive pinger for the separate
FeelingSurf deployment.

This module runs inside the existing 9Hits deployment (supervisor.py PID 1)
as a background daemon thread. Every ``FEELINGSURF_PING_INTERVAL`` seconds it
sends a tiny HTTP GET to the independent FeelingSurf service's public URL so
that free-tier hosts keep the FeelingSurf deployment awake.

Design goals (see the deployment README):
  * It must NEVER block, crash, or restart the 9Hits application. Every
    connection/timeout/HTTP error is caught and logged safely; the loop just
    waits for the next interval and tries again.
  * No tight loop / no high-frequency traffic: only one request per interval,
    and the thread sleeps between pings.
  * The pinger only runs when it can resolve a target URL. Resolution is
    automatic (see ``feelingsurf_ping_url``): ``FEELINGSURF_URL`` →
    ``FEELINGSURF_INTERNAL_URL`` → platform auto-detection. If none yields a
    URL, ``start_keepalive_thread`` is a no-op.
  * The target path is ``<URL>/health`` by default; if that returns 404 the
    pinger falls back to the bare root ``<URL>/`` so it works whether or not
    the FeelingSurf service exposes ``/health``.

Environment variables
=====================
  FEELINGSURF_URL             Explicit public URL of the standalone
                              FeelingSurf deployment, e.g.
                              https://your-feelingsurf-service.example.com
                              (highest priority; docker-compose sets it to the
                              internal service DNS automatically).
  FEELINGSURF_INTERNAL_URL    In-cluster/internal DNS name of the FeelingSurf
                              deployment when the two share a network
                              (e.g. http://feelingsurf:10000 on compose).
  FEELINGSURF_PING_INTERVAL   Seconds between pings. Default 300 (5 minutes).
  FEELINGSURF_PING_TIMEOUT    Per-request timeout in seconds. Default 10.
"""

import logging
import os
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

LOG_PREFIX = "[KeepAlive]"

DEFAULT_INTERVAL = 300   # 5 minutes
DEFAULT_TIMEOUT = 10     # seconds

_logger = logging.getLogger("keepalive")


def _log(msg: str) -> None:
    """Print a [KeepAlive] line that shows up in the 9Hits supervisor log."""
    try:
        print("%s %s" % (LOG_PREFIX, msg), flush=True)
    except Exception:  # never let logging break anything
        pass
    try:
        _logger.info(msg)
    except Exception:
        pass


def _env_float(name: str, default: float) -> float:
    try:
        value = float(str(os.environ.get(name, "") or "").strip() or default)
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def _auto_detect_platform_url() -> str:
    """Best-effort discovery of the FeelingSurf deployment's URL from
    platform-injected metadata, so the keep-alive pinger works without
    manually setting ``FEELINGSURF_URL``.

    Returns ``""`` when nothing can be derived. Detection is keyed on the
    deployment's *own* service identity so we never accidentally point at the
    wrong deployment:

      * Render: ``RENDER_EXTERNAL_URL`` is this service's public URL. We use
        it only when this service IS the FeelingSurf service.
      * Fly.io: public URL follows ``https://<FLY_APP_NAME>.fly.dev``. We use
        it only when the app name indicates the FeelingSurf deployment.

    For the common case (the pinger runs inside the separate **9Hits**
    deployment and targets the sibling FeelingSurf service), set
    ``FEELINGSURF_URL`` explicitly (docker-compose does this automatically via
    the internal service DNS ``http://feelingsurf:10000``).
    """
    # --- Render ---------------------------------------------------------
    if os.environ.get("RENDER"):
        own = (os.environ.get("RENDER_EXTERNAL_URL") or "").strip()
        svc = (os.environ.get("RENDER_SERVICE_NAME") or "").strip().lower()
        if own and svc in ("feelingsurf", "feelingsurf-viewer", "fs"):
            return own
        return ""
    # --- Fly.io ----------------------------------------------------------
    if os.environ.get("FLY_APP_NAME"):
        app = os.environ["FLY_APP_NAME"].strip().lower()
        if "feelingsurf" in app:
            return "https://%s.fly.dev" % app
    return ""


def feelingsurf_ping_url() -> str:
    """Resolve the primary health ping URL for the FeelingSurf deployment.

    Priority:
      1. ``FEELINGSURF_URL`` (explicit override).
      2. ``FEELINGSURF_INTERNAL_URL`` (an in-cluster/internal DNS name when
         the two deployments share a network, e.g. docker-compose's
         ``http://feelingsurf:10000``).
      3. Platform auto-detection from the environment.

    If the resolved URL already carries a path it is used verbatim; otherwise
    ``/health`` is appended so an uptime bot-style endpoint is hit.
    """
    base = (os.environ.get("FEELINGSURF_URL", "") or "").strip()
    if not base:
        base = (os.environ.get("FEELINGSURF_INTERNAL_URL", "") or "").strip()
    if not base:
        base = _auto_detect_platform_url()
    base = base.rstrip("/")
    if not base:
        return ""
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https"):
        # Treat a bare host as https (the current deployments force HTTPS).
        base = "https://" + base
        parts = urlsplit(base)
    if parts.path in ("", "/"):
        return urlunsplit((parts.scheme, parts.netloc, "/health", "", ""))
    return base


def _request(url: str, timeout: float):
    """Issue one GET and return the HTTP status code (int).

    Raises on connection/timeout errors so the caller can log them.
    """
    req = urllib.request.Request(url, method="GET", headers={
        "User-Agent": "hits4me-keepalive/1.0",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.getcode()


def ping_feelingsurf(url: str, timeout: float = DEFAULT_TIMEOUT) -> bool:
    """Ping FeelingSurf once.

    Tries ``<url>/health`` first, then falls back to the bare root on a 404
    (a service without a ``/health`` endpoint). Returns True on success.
    Never raises: errors are logged and False is returned.
    """
    if not url:
        return False

    base = url.rstrip("/")
    candidates = []
    parts = urlsplit(base)
    if parts.path in ("", "/"):
        candidates.append(urlunsplit((parts.scheme, parts.netloc, "/health", "", "")))
    else:
        candidates.append(base)
    # Root fallback so a service that only answers on "/" still works.
    candidates.append(urlunsplit((parts.scheme, parts.netloc, "/", "", "")))

    for idx, target in enumerate(candidates):
        try:
            status = _request(target, timeout)
            _log("FeelingSurf responded with %d" % status)
            return True
        except urllib.error.HTTPError as exc:
            code = exc.code
            # If /health is missing (404) and we have a root fallback left,
            # try the root once before giving up.
            if code == 404 and idx < len(candidates) - 1:
                continue
            _log("FeelingSurf ping failed: HTTP %d on %s" % (code, target))
            return False
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _log("FeelingSurf ping failed: %s on %s" % (exc, target))
            return False
        except Exception as exc:  # safety net - never propagate
            _log("FeelingSurf ping failed: %s on %s" % (exc, target))
            return False
    return False


def run_keepalive_loop(stop_event: threading.Event = None,
                       interval: float = DEFAULT_INTERVAL,
                       timeout: float = DEFAULT_TIMEOUT,
                       url: str = "") -> None:
    """Run the ping loop until ``stop_event`` is set.

    ``url`` overrides ``FEELINGSURF_URL`` when provided (used by tests and
    by callers that already resolved the target). The first ping fires
    immediately, then the thread sleeps ``interval`` seconds between pings.
    """
    if not url:
        url = feelingsurf_ping_url()
    if stop_event is None:
        stop_event = threading.Event()

    while not stop_event.is_set():
        try:
            ping_feelingsurf(url, timeout=timeout)
        except Exception as exc:  # never propagate - keep the loop alive
            _log("FeelingSurf ping failed: %s" % exc)
        try:
            stop_event.wait(interval)
        except Exception:
            break


def start_keepalive_thread() -> threading.Thread:
    """Start the background keep-alive pinger (daemon thread).

    Returns the started thread (or None when ``FEELINGSURF_URL`` is unset).
    A daemon thread means it can never hold up or crash the 9Hits container:
    if the supervisor exits, the thread dies with it.
    """
    url = feelingsurf_ping_url()
    if not url:
        _log("FEELINGSURF_URL not set - keep-alive pinger disabled")
        return None

    interval = _env_float("FEELINGSURF_PING_INTERVAL", DEFAULT_INTERVAL)
    timeout = _env_float("FEELINGSURF_PING_TIMEOUT", DEFAULT_TIMEOUT)

    stop_event = threading.Event()
    thread = threading.Thread(
        target=run_keepalive_loop,
        kwargs={"stop_event": stop_event, "interval": interval,
                "timeout": timeout, "url": url},
        name="feelingsurf-keepalive",
        daemon=True,
    )
    thread.start()
    _log("pinger started: target=%s interval=%.0fs timeout=%.0fs"
         % (url, interval, timeout))
    return thread


if __name__ == "__main__":
    # `python3 keepalive.py` runs a one-shot ping of FEELINGSURF_URL - handy
    # for a quick manual sanity check without waiting for the interval.
    import sys
    url = feelingsurf_ping_url()
    if not url:
        print("%s FEELINGSURF_URL is not set - nothing to ping" % LOG_PREFIX)
        sys.exit(0)
    ok = ping_feelingsurf(url, timeout=_env_float("FEELINGSURF_PING_TIMEOUT", DEFAULT_TIMEOUT))
    sys.exit(0 if ok else 1)
