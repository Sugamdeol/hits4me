#!/usr/bin/env python3
"""
Notesly — notes website front for the SnapDeploy FeelingSurf container.

The public URL of the SnapDeploy container serves this notes website instead
of anything viewer-related. The FeelingSurf viewer keeps running headlessly in
the background (giving views on the container's IP) with its own health server
on VIEWER_HEALTH_PORT; the notes server reports whether it is alive.

Endpoints
---------
    GET    /                    -> notes website (single-page app)
    GET    /health /healthz /ping -> JSON status (also used by SnapDeploy bots)
    GET    /api/notes           -> all notes (pinned first, then newest)
    POST   /api/notes           -> create note   {title?, content?, tags?}
    PUT    /api/notes/<id>      -> update note   {title?, content?, tags?, pinned?}
    DELETE /api/notes/<id>      -> delete note
    GET    /api/stats           -> note counts + viewer status

Environment
-----------
    PORT                HTTP port for this server            (default 3000)
    NOTES_DATA          JSON file used for persistence        (default ~/.notesly/notes.json)
    VIEWER_HEALTH_PORT  port of the viewer's own health server (default 3100)

Stdlib only — no third-party dependencies.
"""

import datetime
import json
import os
import socket
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(ROOT, "static")

PORT = int(os.environ.get("PORT", "3000") or 3000)
VIEWER_HEALTH_PORT = int(os.environ.get("VIEWER_HEALTH_PORT", "3100") or 3100)
NOTES_DATA = os.path.expanduser(os.environ.get("NOTES_DATA", "~/.notesly/notes.json"))
MAX_BODY = 512 * 1024  # 512 KB request body cap
STARTED_AT = time.time()
VERSION = "1.0.0"

_lock = threading.Lock()
_notes = []  # list of note dicts, insertion order irrelevant (sorted on read)

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".woff2": "font/woff2",
}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _seed_notes():
    """Sample notes so a fresh deployment does not look empty."""
    now = _now_iso()
    return [
        {
            "id": uuid.uuid4().hex,
            "title": "Welcome to Notesly 👋",
            "content": (
                "This is your new notes workspace.\n\n"
                "• Create notes with the New note button (or Ctrl/Cmd + N)\n"
                "• Pin important notes so they stay on top\n"
                "• Tag notes like #ideas or #todo and filter with search\n"
                "• Everything saves automatically as you type\n\n"
                "Notes are stored privately on this instance and synced to your "
                "browser, so your writing is never lost."
            ),
            "tags": ["welcome", "tips"],
            "pinned": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": uuid.uuid4().hex,
            "title": "Quick tips",
            "content": (
                "- Search filters titles, content and tags.\n"
                "- Pinned notes sort to the top of the list.\n"
                "- Click the pin icon in the toolbar to pin the open note.\n"
                "- Use plain text or simple lists — Notesly keeps it clean."
            ),
            "tags": ["tips"],
            "pinned": False,
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": uuid.uuid4().hex,
            "title": "Ideas 📝",
            "content": "A place for the thoughts that come at the oddest hours.\n\n",
            "tags": ["ideas"],
            "pinned": False,
            "created_at": now,
            "updated_at": now,
        },
    ]


def _load():
    global _notes
    try:
        with open(NOTES_DATA, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            _notes = [n for n in data if isinstance(n, dict)]
            return
    except (OSError, ValueError):
        pass
    _notes = _seed_notes()
    _save()


def _save():
    """Atomically write the note store (safe against crashes)."""
    global NOTES_DATA
    directory = os.path.dirname(os.path.abspath(NOTES_DATA)) or "."
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        # Home dir may be read-only on exotic platforms; fall back to /tmp.
        NOTES_DATA = os.path.join(tempfile.gettempdir(), "notesly", "notes.json")
        directory = os.path.dirname(NOTES_DATA)
        os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".notes-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(_notes, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, NOTES_DATA)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _sorted_notes():
    def key(n):
        updated = n.get("updated_at") or ""
        # With reverse=True below: pinned (1) first, then updated_at desc.
        return (1 if n.get("pinned") else 0, updated)

    return sorted(_notes, key=key, reverse=True)


def _clean_payload(payload):
    """Validate/normalise an incoming note payload; drop unknown keys."""
    title = payload.get("title")
    content = payload.get("content")
    tags = payload.get("tags")
    pinned = payload.get("pinned")

    if title is not None and not isinstance(title, str):
        raise ValueError("title must be a string")
    if content is not None and not isinstance(content, str):
        raise ValueError("content must be a string")
    if tags is not None:
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise ValueError("tags must be a list of strings")
        tags = [t.strip() for t in tags if t.strip()]
    if pinned is not None and not isinstance(pinned, bool):
        raise ValueError("pinned must be a boolean")

    note = {}
    if title is not None:
        note["title"] = title.strip() or "Untitled note"
    if content is not None:
        note["content"] = content
    if tags is not None:
        note["tags"] = tags
    if pinned is not None:
        note["pinned"] = pinned
    return note


# ---------------------------------------------------------------------------
# Viewer status
# ---------------------------------------------------------------------------

def viewer_running():
    """True when the FeelingSurf viewer's health server answers on its port."""
    try:
        with socket.create_connection(("127.0.0.1", VIEWER_HEALTH_PORT), timeout=1.0):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class NotesHandler(BaseHTTPRequestHandler):
    server_version = "Notesly/" + VERSION
    protocol_version = "HTTP/1.1"

    # -- helpers -----------------------------------------------------------

    def _send(self, status, body, content_type="application/json; charset=utf-8",
              extra_headers=None, head=False):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _send_json(self, obj, status=200):
        self._send(status, json.dumps(obj, ensure_ascii=False))

    def _send_error_json(self, status, message):
        self._send_json({"error": message}, status)

    def _send_file(self, rel_path, head=False):
        static_root = os.path.realpath(STATIC_DIR)
        full = os.path.realpath(os.path.join(static_root, rel_path))
        inside = full == static_root or full.startswith(static_root + os.sep)
        if not inside or not os.path.isfile(full):
            self._send_error_json(404, "Not found")
            return
        ext = os.path.splitext(full)[1].lower()
        ctype = CONTENT_TYPES.get(ext, "application/octet-stream")
        try:
            with open(full, "rb") as fh:
                data = fh.read()
        except OSError:
            self._send_error_json(500, "Could not read file")
            return
        cache = "public, max-age=300" if ext in (".css", ".js") else "no-store"
        self._send(200, data, ctype, {"Cache-Control": cache}, head=head)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("payload too large")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("invalid JSON body")
        if not isinstance(payload, dict):
            raise ValueError("body must be a JSON object")
        return payload

    def _health_payload(self):
        return {
            "service": "notesly",
            "app": "Notesly notes website",
            "version": VERSION,
            "status": "ok",
            "viewer_running": viewer_running(),
            "note_count": len(_notes),
            "uptime_seconds": int(time.time() - STARTED_AT),
        }

    # -- routing -----------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path).rstrip("/") or "/"
        self._handle_get(path, head=False)

    def do_HEAD(self):
        # Needed for wget --spider (used by the Docker HEALTHCHECK) and
        # uptime monitors that only fetch headers.
        parsed = urlparse(self.path)
        path = unquote(parsed.path).rstrip("/") or "/"
        self._handle_get(path, head=True)

    def _handle_get(self, path, head=False):
        if path in ("/health", "/healthz", "/ping"):
            body = json.dumps(self._health_payload(), ensure_ascii=False)
            self._send(200, body, head=head)
            return

        if path == "/api/notes":
            with _lock:
                notes = [dict(n) for n in _sorted_notes()]
            body = json.dumps({"notes": notes}, ensure_ascii=False)
            self._send(200, body, head=head)
            return

        if path == "/api/stats":
            with _lock:
                pinned = sum(1 for n in _notes if n.get("pinned"))
            body = json.dumps({
                "notes": len(_notes),
                "pinned": pinned,
                "viewer_running": viewer_running(),
                "uptime_seconds": int(time.time() - STARTED_AT),
            }, ensure_ascii=False)
            self._send(200, body, head=head)
            return

        if path.startswith("/api/"):
            self._send_error_json(404, "Unknown API endpoint")
            return

        # Static assets and SPA fallback.
        if path.startswith("/static/"):
            self._send_file(path[len("/static/"):], head=head)
            return
        self._send_file("index.html", head=head)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path).rstrip("/") or "/"

        if path != "/api/notes":
            self._send_error_json(404, "Unknown API endpoint")
            return
        try:
            payload = self._read_body()
            clean = _clean_payload(payload)
        except ValueError as exc:
            self._send_error_json(400, str(exc))
            return

        now = _now_iso()
        note = {
            "id": uuid.uuid4().hex,
            "title": clean.get("title", "Untitled note"),
            "content": clean.get("content", ""),
            "tags": clean.get("tags", []),
            "pinned": clean.get("pinned", False),
            "created_at": now,
            "updated_at": now,
        }
        with _lock:
            _notes.append(note)
            _save()
        self._send_json(note, 201)

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path).rstrip("/") or "/"
        prefix = "/api/notes/"
        if not path.startswith(prefix):
            self._send_error_json(404, "Unknown API endpoint")
            return
        note_id = path[len(prefix):]
        try:
            payload = self._read_body()
            clean = _clean_payload(payload)
        except ValueError as exc:
            self._send_error_json(400, str(exc))
            return

        with _lock:
            note = next((n for n in _notes if n.get("id") == note_id), None)
            if note is None:
                self._send_error_json(404, "Note not found")
                return
            note.update(clean)
            note["updated_at"] = _now_iso()
            _save()
        self._send_json(dict(note))

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path).rstrip("/") or "/"
        prefix = "/api/notes/"
        if not path.startswith(prefix):
            self._send_error_json(404, "Unknown API endpoint")
            return
        note_id = path[len(prefix):]
        with _lock:
            before = len(_notes)
            _notes[:] = [n for n in _notes if n.get("id") != note_id]
            if len(_notes) == before:
                self._send_error_json(404, "Note not found")
                return
            _save()
        self._send_json({"ok": True, "id": note_id})

    # -- boring bits -------------------------------------------------------

    def log_message(self, fmt, *args):
        print("[notesly] %s - %s" % (self.address_string(), fmt % args), flush=True)


def main():
    _load()
    print("[notesly] notes data file: %s" % NOTES_DATA, flush=True)
    print("[notesly] viewer health probe: 127.0.0.1:%d" % VIEWER_HEALTH_PORT, flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), NotesHandler)
    print("[notesly] Notesly notes website listening on 0.0.0.0:%d" % PORT, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
