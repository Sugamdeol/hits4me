#!/usr/bin/env python3
"""
Tests for the Docker-free native runner.

Stdlib only (unittest), no network and no real 9Hits binary: the viewer
tarball is served from a local HTTP server and the viewer/Xvfb are replaced
by fake executables on PATH, so the real download, extraction, init pass,
supervision and /health code paths all run for real.

    python3 tests/test_run_native.py
"""

import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import HTTPServer, SimpleHTTPRequestHandler

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import health_server  # noqa: E402
import run_native  # noqa: E402
import run_pty  # noqa: E402
import viewer_config  # noqa: E402

# A stand-in for nhviewer: exits immediately for the init pass, otherwise
# stays alive until it is signalled, and records every invocation.
FAKE_VIEWER = r"""#!/bin/bash
echo "fake-nhviewer args: $*" >> "$FAKE_VIEWER_LOG"
for arg in "$@"; do
  if [ "$arg" = "--exit-on-init" ]; then
    echo "init pass complete"
    exit 0
  fi
done
echo "viewer running on DISPLAY=$DISPLAY"
trap 'echo "viewer terminating"; exit 0' TERM INT
while true; do sleep 0.2; done
"""

# A stand-in for Xvfb: creates the socket file run_native waits for.
FAKE_XVFB = r"""#!/bin/bash
display="$1"
num="${display#:}"
mkdir -p /tmp/.X11-unix
touch "/tmp/.X11-unix/X${num}"
trap 'rm -f "/tmp/.X11-unix/X${num}"; exit 0' TERM INT
while true; do sleep 0.2; done
"""


def write_executable(path, content):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(path, 0o755)
    return path


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_viewer_tarball(path, binary_content=FAKE_VIEWER, top="9hitsv6-linux64"):
    """Build a tar.bz2 laid out like the official release (one top dir)."""
    with tarfile.open(path, "w:bz2") as archive:
        data = binary_content.encode()
        info = tarfile.TarInfo("%s/nhviewer" % top)
        info.size = len(data)
        info.mode = 0o755
        archive.addfile(info, io.BytesIO(data))

        readme = b"fake viewer payload\n"
        info = tarfile.TarInfo("%s/README" % top)
        info.size = len(readme)
        archive.addfile(info, io.BytesIO(readme))
    return path


class TarballServer:
    """Serves a directory over HTTP for the download test."""

    def __init__(self, directory):
        self.directory = directory
        handler = lambda *a, **kw: SimpleHTTPRequestHandler(  # noqa: E731
            *a, directory=directory, **kw
        )
        self.httpd = HTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def url(self, name):
        return "http://127.0.0.1:%d/%s" % (self.port, name)

    def __exit__(self, *_exc):
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------------------------------------------------------------------------
class TestViewerConfig(unittest.TestCase):
    def test_value_and_bool_flags(self):
        env = {
            "ACCESS_KEY": "abc123",
            "SYSTEM_SESSION": "yes",
            "CLEAR_ALL_SESSIONS": "no",
            "NOTE": "my-vps",
            "HIDE_BROWSER": "yes",
        }
        args = viewer_config.build_config_args(env=env)
        self.assertIn("--access-key=abc123", args)
        self.assertIn("--note=my-vps", args)
        self.assertIn("--hide-browser=yes", args)
        self.assertIn("--system-session", args)
        self.assertNotIn("--clear-all-sessions", args)

    def test_empty_values_are_skipped(self):
        args = viewer_config.build_config_args(env={"ACCESS_KEY": "", "NOTE": "   "})
        self.assertEqual(args, [])

    def test_extra_args_are_shell_split(self):
        env = {"EXTRA_ARGS": "--hide-columns=quality,points --foo=bar"}
        args = viewer_config.build_config_args(env=env)
        self.assertEqual(args, ["--hide-columns=quality,points", "--foo=bar"])

    def test_run_flags_include_reset_interval(self):
        flags = viewer_config.build_run_flags(env={"RESET_INTERVAL": "2h"})
        self.assertIn("--auto-start", flags)
        self.assertIn("--in-loop", flags)
        self.assertIn("--reset-interval=2h", flags)
        self.assertNotIn(
            "--reset-interval",
            " ".join(viewer_config.build_run_flags(env={})),
        )

    def test_secrets_are_redacted(self):
        args = ["--access-key=supersecret", "--bulk-add-proxy-list=1.2.3.4;u;p", "--note=x"]
        redacted = viewer_config.redact(args)
        self.assertEqual(
            redacted, ["--access-key=****", "--bulk-add-proxy-list=****", "--note=x"]
        )
        self.assertNotIn("supersecret", viewer_config.redacted_line(args))

    def test_estimate_sessions(self):
        env = {
            "EX_PROXY_SESSIONS": "5",
            "BULK_ADD_PROXY_LIST": "a:1;u;p|b:2;u;p",
            "SYSTEM_SESSION": "yes",
        }
        self.assertEqual(viewer_config.estimate_sessions(env), 8)
        self.assertEqual(viewer_config.estimate_sessions({}), 0)

    def test_truthiness(self):
        for value in ("yes", "YES", "true", "1", "on"):
            self.assertTrue(viewer_config.is_truthy(value))
        for value in ("no", "false", "0", "", None, "maybe"):
            self.assertFalse(viewer_config.is_truthy(value))


# ---------------------------------------------------------------------------
class TestRunPty(unittest.TestCase):
    def test_captures_output_and_exit_code(self):
        buffer = io.BytesIO()
        code = run_pty.run(["/bin/sh", "-c", "echo hello-pty; exit 3"], writer=buffer)
        self.assertEqual(code, 3)
        self.assertIn(b"hello-pty", buffer.getvalue())

    def test_child_sees_a_tty(self):
        buffer = io.BytesIO()
        code = run_pty.run(
            ["/bin/sh", "-c", "test -t 1 && echo IS_TTY || echo NO_TTY"], writer=buffer
        )
        self.assertEqual(code, 0)
        self.assertIn(b"IS_TTY", buffer.getvalue())

    def test_pid_callback_fires(self):
        seen = []
        run_pty.run(["/bin/sh", "-c", "true"], writer=io.BytesIO(), pid_callback=seen.append)
        self.assertEqual(len(seen), 1)
        self.assertGreater(seen[0], 1)

    def test_exec_failure_returns_127(self):
        buffer = io.BytesIO()
        code = run_pty.run(["/nonexistent/binary-xyz"], writer=buffer)
        self.assertEqual(code, 127)

    def test_runs_off_the_main_thread(self):
        """The supervisor calls run() from a worker thread; signal handler
        registration must not explode there."""
        result = {}

        def worker():
            result["code"] = run_pty.run(
                ["/bin/sh", "-c", "echo threaded"], writer=io.BytesIO()
            )

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=15)
        self.assertEqual(result.get("code"), 0)


# ---------------------------------------------------------------------------
class TestDownloadAndExtract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hits4me-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_ensure_viewer_downloads_and_strips_top_level(self):
        serve_dir = os.path.join(self.tmp, "www")
        os.makedirs(serve_dir)
        make_viewer_tarball(os.path.join(serve_dir, "viewer.tar.bz2"))
        install_dir = os.path.join(self.tmp, "install")

        with TarballServer(serve_dir) as server:
            binary = run_native.ensure_viewer(
                install_dir, url=server.url("viewer.tar.bz2")
            )

        self.assertEqual(binary, os.path.join(install_dir, "nhviewer"))
        self.assertTrue(os.path.isfile(binary))
        self.assertTrue(os.access(binary, os.X_OK))
        # top-level directory was stripped
        self.assertTrue(os.path.isfile(os.path.join(install_dir, "README")))
        self.assertFalse(os.path.isdir(os.path.join(install_dir, "9hitsv6-linux64")))

    def test_ensure_viewer_is_cached(self):
        install_dir = os.path.join(self.tmp, "cached")
        os.makedirs(install_dir)
        write_executable(os.path.join(install_dir, "nhviewer"), "#!/bin/sh\ntrue\n")
        # An unreachable URL proves no download is attempted.
        binary = run_native.ensure_viewer(
            install_dir, url="http://127.0.0.1:1/nope.tar.bz2"
        )
        self.assertTrue(os.path.isfile(binary))

    def test_safe_extract_blocks_path_traversal(self):
        malicious = os.path.join(self.tmp, "evil.tar.bz2")
        with tarfile.open(malicious, "w:bz2") as archive:
            data = b"pwned"
            info = tarfile.TarInfo("top/../../../../tmp/hits4me-pwned")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))

        target = os.path.join(self.tmp, "extract-here")
        os.makedirs(target)
        with tarfile.open(malicious, "r:bz2") as archive:
            with self.assertRaises(RuntimeError):
                run_native._safe_extract(archive, target)
        self.assertFalse(os.path.exists("/tmp/hits4me-pwned"))


# ---------------------------------------------------------------------------
class TestSupervisor(unittest.TestCase):
    def test_relaunches_the_viewer_when_it_exits(self):
        supervisor = run_native.ViewerSupervisor(
            "/bin/sh", ["-c", "exit 7"], env={}, delay=0
        )
        supervisor.start()
        deadline = time.time() + 20
        while supervisor.restarts < 3 and time.time() < deadline:
            time.sleep(0.2)
        # Read the code before stop(), which SIGTERMs the current child.
        exit_code = supervisor.last_exit_code
        restarts = supervisor.restarts
        supervisor.stop()
        self.assertGreaterEqual(restarts, 3)
        self.assertEqual(exit_code, 7, "supervisor should record the child's code")

    def test_status_shape(self):
        supervisor = run_native.ViewerSupervisor("/bin/sh", ["-c", "sleep 30"], env={})
        status = supervisor.status()
        for key in (
            "viewer_running",
            "supervisor_running",
            "viewer_pid",
            "restarts",
            "uptime_seconds",
        ):
            self.assertIn(key, status)
        self.assertFalse(status["viewer_running"])
        self.assertFalse(status["supervisor_running"])

    def test_custom_writer_and_logger_capture_output(self):
        """app.py relies on these to feed the Gradio dashboard."""
        buffer = io.BytesIO()
        messages = []
        supervisor = run_native.ViewerSupervisor(
            "/bin/sh",
            ["-c", "echo from-the-viewer; exit 0"],
            env={},
            delay=0,
            writer=buffer,
            logger=messages.append,
        )
        supervisor.start()
        deadline = time.time() + 20
        while supervisor.restarts < 1 and time.time() < deadline:
            time.sleep(0.1)
        supervisor.stop()

        self.assertIn(b"from-the-viewer", buffer.getvalue())
        self.assertTrue(any("launching:" in m for m in messages))

    def test_stop_terminates_a_running_viewer(self):
        supervisor = run_native.ViewerSupervisor(
            "/bin/sh", ["-c", "sleep 300"], env={}, delay=0
        )
        supervisor.start()
        deadline = time.time() + 15
        while supervisor.viewer_pid == 0 and time.time() < deadline:
            time.sleep(0.1)
        pid = supervisor.viewer_pid
        self.assertGreater(pid, 1, "viewer never started")
        supervisor.stop()
        time.sleep(0.5)
        with self.assertRaises(OSError):
            for _ in range(20):
                os.kill(pid, 0)
                time.sleep(0.1)


# ---------------------------------------------------------------------------
class TestHealthServer(unittest.TestCase):
    def _get(self, port, path="/health"):
        with urllib.request.urlopen(
            "http://127.0.0.1:%d%s" % (port, path), timeout=5
        ) as response:
            return response.status, json.loads(response.read().decode())

    def _serve(self, status_provider):
        """Start a health server and make sure its socket is closed after."""
        server = health_server.serve_in_thread(free_port(), status_provider)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def test_reports_ok_when_everything_runs(self):
        status = {
            "viewer_running": True,
            "supervisor_running": True,
            "viewer_pid": 42,
            "restarts": 0,
            "uptime_seconds": 7,
        }
        port = self._serve(lambda: status)

        code, body = self._get(port)
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["service"], "9hits-viewer")
        self.assertEqual(body["viewer_pid"], 42)

    def test_restarting_and_error_states(self):
        state = {"viewer_running": False, "supervisor_running": True}
        port = self._serve(lambda: dict(state))

        code, body = self._get(port)
        self.assertEqual((code, body["status"]), (200, "restarting"))

        state["supervisor_running"] = False
        try:
            self._get(port)
            self.fail("expected HTTP 503")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 503)
            self.assertEqual(json.loads(exc.read().decode())["status"], "error")

    def test_all_aliases_and_404(self):
        port = self._serve(
            lambda: {"viewer_running": True, "supervisor_running": True}
        )

        for path in ("/", "/health", "/healthz", "/ping"):
            code, _ = self._get(port, path)
            self.assertEqual(code, 200, path)

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get(port, "/nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_broken_status_provider_does_not_500(self):
        def boom():
            raise RuntimeError("probe exploded")

        port = self._serve(boom)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get(port)
        self.assertEqual(ctx.exception.code, 503)


# ---------------------------------------------------------------------------
class TestProxyResolution(unittest.TestCase):
    def test_fetches_list_from_url(self):
        tmp = tempfile.mkdtemp(prefix="hits4me-proxy-")
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "proxies.txt"), "w", encoding="utf-8") as handle:
            handle.write("1.2.3.4:1080:user:pass\n5.6.7.8:1080:user2:pass2\n")

        with TarballServer(tmp) as server:
            env = {"BULK_ADD_PROXY_LIST_URL": server.url("proxies.txt")}
            run_native.resolve_proxy_list(env)

        self.assertEqual(
            env["BULK_ADD_PROXY_LIST"],
            "1.2.3.4:1080;user;pass|5.6.7.8:1080;user2;pass2",
        )
        self.assertEqual(env["BULK_ADD_PROXY_TYPE"], "socks5")

    def test_existing_list_wins(self):
        env = {
            "BULK_ADD_PROXY_LIST_URL": "http://127.0.0.1:1/x",
            "BULK_ADD_PROXY_LIST": "9.9.9.9:1080;u;p",
        }
        run_native.resolve_proxy_list(env)
        self.assertEqual(env["BULK_ADD_PROXY_LIST"], "9.9.9.9:1080;u;p")

    def test_fetch_failure_is_non_fatal(self):
        env = {"BULK_ADD_PROXY_LIST_URL": "http://127.0.0.1:1/unreachable"}
        run_native.resolve_proxy_list(env)  # must not raise
        self.assertNotIn("BULK_ADD_PROXY_LIST", env)


# ---------------------------------------------------------------------------
class TestCliMapping(unittest.TestCase):
    def test_cli_overrides_environment(self):
        parser = run_native.build_parser()
        args = parser.parse_args(
            ["--access-key=fromcli", "--system-session", "--port=12345"]
        )
        env = run_native.apply_cli_to_env(args, {"ACCESS_KEY": "fromenv"})
        self.assertEqual(env["ACCESS_KEY"], "fromcli")
        self.assertEqual(env["SYSTEM_SESSION"], "yes")
        self.assertEqual(env["PORT"], "12345")

    def test_environment_used_when_no_flag(self):
        args = run_native.build_parser().parse_args([])
        env = run_native.apply_cli_to_env(args, {"ACCESS_KEY": "fromenv"})
        self.assertEqual(env["ACCESS_KEY"], "fromenv")
        self.assertNotIn("SYSTEM_SESSION", env)

    def test_negative_flags(self):
        args = run_native.build_parser().parse_args(["--no-system-session"])
        env = run_native.apply_cli_to_env(args, {"SYSTEM_SESSION": "yes"})
        self.assertEqual(env["SYSTEM_SESSION"], "no")


# ---------------------------------------------------------------------------
class TestEndToEnd(unittest.TestCase):
    """Runs run_native.py as a real subprocess against fake Xvfb + nhviewer."""

    def test_full_boot_health_and_shutdown(self):
        tmp = tempfile.mkdtemp(prefix="hits4me-e2e-")
        self.addCleanup(shutil.rmtree, tmp, True)

        bin_dir = os.path.join(tmp, "bin")
        www_dir = os.path.join(tmp, "www")
        install_dir = os.path.join(tmp, "install")
        os.makedirs(bin_dir)
        os.makedirs(www_dir)

        write_executable(os.path.join(bin_dir, "Xvfb"), FAKE_XVFB)
        make_viewer_tarball(os.path.join(www_dir, "viewer.tar.bz2"))
        viewer_log = os.path.join(tmp, "viewer-args.log")

        port = free_port()
        with TarballServer(www_dir) as server:
            env = dict(
                os.environ,
                PATH=bin_dir + os.pathsep + os.environ["PATH"],
                FAKE_VIEWER_LOG=viewer_log,
                HOME=tmp,
            )
            for stale in ("DISPLAY", "ACCESS_KEY", "BULK_ADD_PROXY_LIST_URL"):
                env.pop(stale, None)

            process = subprocess.Popen(
                [
                    sys.executable,
                    os.path.join(REPO_ROOT, "run_native.py"),
                    "--skip-checks",
                    "--access-key=testkey123",
                    "--system-session",
                    "--session-note=e2e",
                    "--reset-interval=2h",
                    "--supervisor-delay=1",
                    "--install-dir=%s" % install_dir,
                    "--download-url=%s" % server.url("viewer.tar.bz2"),
                    "--port=%d" % port,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )

            try:
                body = None
                deadline = time.time() + 90
                while time.time() < deadline:
                    if process.poll() is not None:
                        break
                    try:
                        with urllib.request.urlopen(
                            "http://127.0.0.1:%d/health" % port, timeout=2
                        ) as response:
                            candidate = json.loads(response.read().decode())
                        if candidate.get("status") == "ok":
                            body = candidate
                            break
                    except Exception:
                        time.sleep(0.5)

                self.assertIsNotNone(
                    body,
                    "health endpoint never reported ok; runner still alive=%s"
                    % (process.poll() is None),
                )
                self.assertEqual(body["status"], "ok")
                self.assertTrue(body["viewer_running"])
                self.assertTrue(body["supervisor_running"])
                self.assertGreater(body["viewer_pid"], 1)

                # The viewer really was downloaded and executed.
                self.assertTrue(os.path.isfile(os.path.join(install_dir, "nhviewer")))
                with open(viewer_log, encoding="utf-8") as handle:
                    invocations = handle.read()

                # init pass carried the configuration...
                self.assertIn("--access-key=testkey123", invocations)
                self.assertIn("--system-session", invocations)
                self.assertIn("--session-note=e2e", invocations)
                self.assertIn("--exit-on-init", invocations)
                # ...and the supervised run carried the runtime flags only.
                run_line = [
                    line
                    for line in invocations.splitlines()
                    if "--exit-on-init" not in line
                ]
                self.assertTrue(run_line, "supervised run never happened")
                self.assertIn("--auto-start", run_line[-1])
                self.assertIn("--reset-interval=2h", run_line[-1])
                self.assertNotIn("--access-key", run_line[-1])

                # Graceful shutdown on SIGTERM.
                process.send_signal(signal.SIGTERM)
                process.wait(timeout=45)
                output = process.stdout.read()
                self.assertNotIn("testkey123", output, "access key leaked into logs")
                self.assertIn("--access-key=****", output)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                process.stdout.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
