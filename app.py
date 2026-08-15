#!/usr/bin/env python3
"""
Hugging Face Spaces (Gradio Free Tier) Entrypoint for 9Hits Viewer.

Allows running on Hugging Face Spaces using the 100% FREE Gradio SDK
(2 vCPU, 16 GB RAM) without requiring a paid Docker Space.
"""

import os
import signal
import subprocess
import sys
import threading
import time

try:
    import gradio as gr
except ImportError:
    gr = None

PORT = int(os.environ.get("PORT", "7860") or 7860)
ACCESS_KEY = os.environ.get("ACCESS_KEY", "")
SYSTEM_SESSION = os.environ.get("SYSTEM_SESSION", "yes")

LOG_BUFFER = []
MAX_LOG_LINES = 100


def run_supervisor():
    """Start start.sh in the background with PORT set."""
    env = os.environ.copy()
    env["PORT"] = str(PORT)
    env["SYSTEM_SESSION"] = SYSTEM_SESSION
    env["CLEAR_ALL_SESSIONS"] = os.environ.get("CLEAR_ALL_SESSIONS", "yes")
    env["HIDE_BROWSER"] = "yes"

    script_path = os.path.join(os.path.dirname(__file__), "start.sh")
    proc = subprocess.Popen(
        ["/bin/bash", script_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )

    for line in iter(proc.stdout.readline, ""):
        if line:
            clean_line = line.rstrip()
            LOG_BUFFER.append(clean_line)
            if len(LOG_BUFFER) > MAX_LOG_LINES:
                LOG_BUFFER.pop(0)
            print(clean_line, flush=True)

    proc.wait()


def get_logs():
    return "\n".join(LOG_BUFFER[-40:]) if LOG_BUFFER else "Starting 9Hits Viewer..."


def get_status():
    viewer_alive = os.path.exists("/tmp/viewer.pid")
    restarts = 0
    if os.path.exists("/tmp/viewer.restarts"):
        try:
            with open("/tmp/viewer.restarts") as f:
                restarts = int(f.read().strip() or 0)
        except Exception:
            pass
    return f"Status: {'🟢 Online' if viewer_alive else '🟡 Booting / Restarting'} | Restarts: {restarts}"


def build_ui():
    if not gr:
        return None

    with gr.Blocks(title="9Hits Viewer Status") as demo:
        gr.Markdown("# 🌐 9Hits Viewer — Hugging Face Space (Free Tier)")
        gr.Markdown(
            "Running 9Hits headless viewer. Monitor your node status and live logs below."
        )

        with gr.Row():
            status_box = gr.Textbox(
                label="Instance Status",
                value=get_status,
                interactive=False,
                every=5,
            )

        with gr.Row():
            log_box = gr.Textbox(
                label="Live Viewer Output",
                value=get_logs,
                interactive=False,
                lines=18,
                every=3,
            )

        gr.Markdown(
            "💡 **Tip:** Set `ACCESS_KEY` in **Space Settings** → **Variables and secrets**."
        )

    return demo


def main():
    # Start supervisor thread
    t = threading.Thread(target=run_supervisor, daemon=True)
    t.start()

    if gr:
        demo = build_ui()
        if demo:
            demo.queue().launch(
                server_name="0.0.0.0",
                server_port=PORT,
                show_error=True,
            )
            return

    # Fallback if gradio is not installed: wait forever
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
