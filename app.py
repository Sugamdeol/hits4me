#!/usr/bin/env python3
"""
Hugging Face Spaces (Gradio Free Tier) entrypoint for the 9Hits Viewer.

Runs on the 100% FREE Gradio SDK (2 vCPU, 16 GB RAM) - no paid Docker Space
required. There is no Docker daemon and no /nh.sh inside a Gradio Space, so
this drives the Docker-free native runner (run_native.py) in-process: it
downloads the viewer, starts Xvfb, supervises the viewer under a PTY and
exposes a live status dashboard.

System packages (xvfb, libnss3, ...) come from packages.txt, which Spaces
installs automatically at build time.
"""

import os
import sys
import threading
import time
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import gradio as gr
except ImportError:
    gr = None

import run_native  # noqa: E402
import viewer_config  # noqa: E402

# Gradio owns $PORT on Spaces, so the viewer's own /health server is disabled
# here; this app's HTTP interface is the health target instead.
PORT = int(os.environ.get("PORT", "7860") or 7860)
MAX_LOG_LINES = 400

LOG_BUFFER = deque(maxlen=MAX_LOG_LINES)
STATE = {"supervisor": None, "display": None, "error": None, "started_at": time.time()}


class LogCapture:
    """File-like sink: mirrors viewer output to stdout and the ring buffer."""

    def __init__(self, stream):
        self.stream = stream
        self._partial = b""

    def write(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8", "replace")
        try:
            self.stream.write(data)
        except Exception:
            pass
        self._partial += data
        while b"\n" in self._partial:
            line, self._partial = self._partial.split(b"\n", 1)
            text = line.decode("utf-8", "replace").rstrip("\r")
            if text.strip():
                LOG_BUFFER.append(text)
        return len(data)

    def flush(self):
        try:
            self.stream.flush()
        except Exception:
            pass


def log(message):
    LOG_BUFFER.append("[space] %s" % message)
    print("[space] %s" % message, flush=True)


def boot():
    """Download, configure and supervise the viewer (runs on a worker thread)."""
    try:
        env = dict(os.environ)
        env.setdefault("SYSTEM_SESSION", "yes")
        env.setdefault("CLEAR_ALL_SESSIONS", "yes")
        env.setdefault("HIDE_BROWSER", "yes")
        env.setdefault("NOTE", "huggingface")
        env.setdefault("SESSION_NOTE", "hf-system")

        # Spaces give us a writable /home/user; keep everything under it.
        install_dir = os.path.abspath(
            os.path.expanduser(
                env.get("INSTALL_DIR") or os.path.join(os.getcwd(), ".9hits")
            )
        )
        os.makedirs(install_dir, exist_ok=True)
        env["CACHE_PATH"] = env.get("CACHE_PATH") or os.path.join(install_dir, "cache")
        if not viewer_config.env_str("CACHE_LIMIT", env=env):
            env["CACHE_LIMIT"] = run_native.DEFAULT_CACHE_LIMIT

        if not viewer_config.env_str("ACCESS_KEY", env=env):
            log(
                "ACCESS_KEY is not set - add it under Settings -> "
                "Variables and secrets, then restart the Space."
            )

        run_native.check_platform(skip=True)
        run_native.check_dependencies(skip=True)

        log("preparing viewer in %s" % install_dir)
        binary = run_native.ensure_viewer(install_dir)

        run_native.resolve_proxy_list(env)

        display = run_native.VirtualDisplay()
        os.environ["DISPLAY"] = display.start()
        env["DISPLAY"] = os.environ["DISPLAY"]
        STATE["display"] = display

        writer = LogCapture(sys.stdout.buffer)

        config_args = viewer_config.build_config_args(env=env)
        log("applying configuration: %s" % viewer_config.redacted_line(config_args))
        previous = os.environ.copy()
        os.environ.update(env)
        try:
            import run_pty

            run_pty.run(
                [binary] + config_args + ["--exit-on-init"],
                writer=writer,
                forward_signals=False,
            )
        finally:
            os.environ.clear()
            os.environ.update(previous)

        # writer/logger route the supervised viewer's output and the
        # supervisor's own messages into the Gradio dashboard.
        supervisor = run_native.ViewerSupervisor(
            binary,
            viewer_config.build_run_flags(env=env),
            env,
            delay=int(viewer_config.env_str("SUPERVISOR_DELAY", "10", env=env) or 10),
            writer=writer,
            logger=log,
        )
        supervisor.start()
        STATE["supervisor"] = supervisor
        log("viewer supervisor started")
    except SystemExit as exc:
        STATE["error"] = "startup aborted (%s) - check the logs above" % exc
        log(STATE["error"])
    except Exception as exc:  # keep the UI alive so the user can read why
        STATE["error"] = "%s: %s" % (type(exc).__name__, exc)
        log("startup failed: %s" % STATE["error"])


def get_logs():
    if not LOG_BUFFER:
        return "Starting 9Hits Viewer..."
    return "\n".join(list(LOG_BUFFER)[-40:])


def get_status():
    if STATE["error"]:
        return "🔴 Error — %s" % STATE["error"]

    supervisor = STATE["supervisor"]
    if not supervisor:
        return "🟡 Booting — downloading viewer / starting display..."

    status = supervisor.status()
    icon = "🟢 Online" if status["viewer_running"] else "🟡 Restarting"
    return (
        "%s | PID: %s | Restarts: %d | Viewer uptime: %ds | Space uptime: %ds"
        % (
            icon,
            status["viewer_pid"] or "-",
            status["restarts"],
            status["viewer_uptime_seconds"],
            int(time.time() - STATE["started_at"]),
        )
    )


def build_ui():
    if not gr:
        return None

    with gr.Blocks(title="9Hits Viewer Status") as demo:
        gr.Markdown("# 🌐 9Hits Viewer — Hugging Face Space (Free Tier)")
        gr.Markdown(
            "Running the 9Hits headless viewer natively (no Docker required). "
            "Monitor node status and live logs below."
        )

        with gr.Row():
            gr.Textbox(
                label="Instance Status",
                value=get_status,
                interactive=False,
                every=5,
            )

        with gr.Row():
            gr.Textbox(
                label="Live Viewer Output",
                value=get_logs,
                interactive=False,
                lines=18,
                every=3,
            )

        gr.Markdown(
            "💡 **Tip:** Set `ACCESS_KEY` in **Space Settings** → "
            "**Variables and secrets**, then restart the Space."
        )

    return demo


def main():
    threading.Thread(target=boot, name="viewer-boot", daemon=True).start()

    if gr:
        demo = build_ui()
        if demo:
            demo.queue().launch(
                server_name="0.0.0.0",
                server_port=PORT,
                show_error=True,
            )
            return

    # No gradio installed: fall back to the plain /health endpoint.
    import health_server

    def status():
        supervisor = STATE["supervisor"]
        if not supervisor:
            return {"viewer_running": False, "supervisor_running": not STATE["error"]}
        return supervisor.status()

    health_server.serve(PORT, status)


if __name__ == "__main__":
    main()
