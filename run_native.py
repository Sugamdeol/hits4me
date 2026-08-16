#!/usr/bin/env python3
"""
=============================================================================
9Hits Viewer v6 - Docker-free native runner (pure Python, stdlib only)
=============================================================================

Use this wherever Docker is unavailable: shared VPS without a container
runtime, Hugging Face free Gradio Spaces, Google Colab, WSL, an LXC container,
a locked-down CI box, or simply a machine where you don't want to install
Docker.

It is a drop-in replacement for the Docker image + `start.sh` entrypoint and
does everything the container did, in one process tree:

  1. verifies the platform (Linux x86_64, glibc >= 2.31)
  2. downloads + extracts the official 9Hits Viewer v6 tarball (once, cached)
  3. checks the shared libraries the viewer needs, and can install them
     (`--install-deps`) when running as root
  4. starts its own Xvfb virtual display (no desktop / no GUI required)
  5. runs the one-time init pass that applies ACCESS_KEY, sessions and proxies
  6. supervises the viewer under a pseudo-TTY, relaunching it whenever it exits
  7. serves the very same `/health` endpoint on 0.0.0.0:$PORT for uptime bots

Unlike the official install.sh it needs **neither root nor systemd** — the
supervision loop and the virtual display are managed in-process.

Configuration comes from the same environment variables as the Docker image
(ACCESS_KEY, SYSTEM_SESSION, RESET_INTERVAL, ... - see README.md), and every
one of them also has a command-line flag that takes precedence.

Quick start:

    export ACCESS_KEY=<your-9hits-access-key>
    python3 run_native.py --system-session

=============================================================================
"""

import argparse
import errno
import glob
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_pty  # noqa: E402
import health_server  # noqa: E402
import viewer_config  # noqa: E402
from fetch_proxy_list import ProxyFetchError, fetch as fetch_proxy_list  # noqa: E402

DOWNLOAD_URL = "https://dl.9hits.com/9hitsv6-linux64.tar.bz2"
DEFAULT_CACHE_LIMIT = "209715200"  # 200 MB, matches the official installer
VIEWER_BINARY = "nhviewer"

# Shared libraries the viewer's bundled Chromium needs, and the Debian /
# RedHat packages that provide them.
REQUIRED_LIBS = {
    "libnss3.so": ("libnss3", "nss"),
    "libgtk-3.so.0": ("libgtk-3-0", "gtk3"),
    "libgbm.so.1": ("libgbm1", "mesa-libgbm"),
    "libasound.so.2": ("libasound2", "alsa-lib"),
    "libatk-1.0.so.0": ("libatk1.0-0", "atk"),
    "libatk-bridge-2.0.so.0": ("libatk-bridge2.0-0", "at-spi2-atk"),
    "libatspi.so.0": ("libatspi2.0-0", "at-spi2-core"),
    "libcups.so.2": ("libcups2", "cups-libs"),
    "libdrm.so.2": ("libdrm2", "libdrm"),
    "libxkbcommon.so.0": ("libxkbcommon0", "libxkbcommon"),
    "libXcomposite.so.1": ("libxcomposite1", "libXcomposite"),
    "libXdamage.so.1": ("libxdamage1", "libXdamage"),
    "libXfixes.so.3": ("libxfixes3", "libXfixes"),
    "libXrandr.so.2": ("libxrandr2", "libXrandr"),
    "libXss.so.1": ("libxss1", "libXScrnSaver"),
    "libXtst.so.6": ("libxtst6", "libXtst"),
    "libatomic.so.1": ("libatomic1", "libatomic"),
}

APT_EXTRA = ("xvfb", "bzip2", "ca-certificates", "wget")
YUM_EXTRA = ("xorg-x11-server-Xvfb", "bzip2", "ca-certificates", "wget")

_START_TIME = time.time()


# ---------------------------------------------------------------- logging
def log(message):
    print("[native] %s" % message, flush=True)


def warn(message):
    print("[native] WARNING: %s" % message, flush=True)


def fail(message, hint=None):
    print("[native] ERROR: %s" % message, file=sys.stderr, flush=True)
    if hint:
        print("[native]        %s" % hint, file=sys.stderr, flush=True)
    sys.exit(1)


# ------------------------------------------------------- platform checks
def glibc_version():
    try:
        version = platform.libc_ver()[1]
        parts = version.split(".")
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except (ValueError, IndexError):
        return None


def check_platform(skip=False):
    problems = []

    if sys.platform != "linux":
        problems.append(
            "the 9Hits Viewer is a Linux binary; this is %r. "
            "On macOS/Windows run it inside WSL2 or a Linux VM." % sys.platform
        )

    machine = platform.machine()
    if machine not in ("x86_64", "amd64"):
        problems.append(
            "unsupported CPU architecture %r - only x86_64 is supported "
            "(ARM boxes such as Oracle Ampere cannot run this viewer)." % machine
        )

    version = glibc_version()
    if version and version < (2, 31):
        problems.append(
            "glibc %d.%d is too old (need >= 2.31; Ubuntu 20.04+, Debian 11+)."
            % version
        )

    if problems:
        for problem in problems:
            (warn if skip else lambda m: print("[native] ERROR: %s" % m, file=sys.stderr))(problem)
        if not skip:
            sys.exit(1)
        return False

    log(
        "platform ok: %s %s, glibc %s"
        % (
            platform.system(),
            machine,
            "%d.%d" % version if version else "unknown",
        )
    )
    return True


# ------------------------------------------------------------ dependencies
def find_library(soname):
    """True if the dynamic loader can resolve ``soname``."""
    import ctypes

    try:
        ctypes.CDLL(soname)
        return True
    except OSError:
        pass

    # Fall back to scanning the usual multiarch directories: some libs are
    # present but not directly loadable (missing dev symlink etc.).
    patterns = [
        "/lib/*/%s*" % soname,
        "/usr/lib/*/%s*" % soname,
        "/lib64/%s*" % soname,
        "/usr/lib64/%s*" % soname,
        "/usr/lib/%s*" % soname,
    ]
    return any(glob.glob(pattern) for pattern in patterns)


def missing_libraries():
    return [soname for soname in sorted(REQUIRED_LIBS) if not find_library(soname)]


def detect_package_manager():
    for manager in ("apt-get", "dnf", "yum", "apk", "pacman", "zypper"):
        if shutil.which(manager):
            return manager
    return None


def dependency_install_command(missing, need_xvfb):
    manager = detect_package_manager()
    if manager == "apt-get":
        packages = sorted({REQUIRED_LIBS[lib][0] for lib in missing})
        if need_xvfb:
            packages += list(APT_EXTRA)
        return manager, ["apt-get", "install", "-y", "--no-install-recommends"] + sorted(set(packages))
    if manager in ("dnf", "yum"):
        packages = sorted({REQUIRED_LIBS[lib][1] for lib in missing})
        if need_xvfb:
            packages += list(YUM_EXTRA)
        return manager, [manager, "install", "-y"] + sorted(set(packages))
    return manager, None


def install_dependencies(missing, need_xvfb):
    manager, command = dependency_install_command(missing, need_xvfb)
    if not command:
        warn(
            "cannot auto-install dependencies (no supported package manager "
            "found%s). Install them manually." % ("" if not manager else ": " + manager)
        )
        return False

    if os.geteuid() != 0:
        warn("--install-deps needs root; re-run with sudo or install manually.")
        return False

    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    if manager == "apt-get":
        log("running: apt-get update")
        subprocess.call(["apt-get", "update", "-q"], env=env)

    log("running: %s" % " ".join(command))
    return subprocess.call(command, env=env) == 0


def check_dependencies(auto_install=False, skip=False):
    """Verify Xvfb + shared libraries; optionally install what's missing."""
    missing = missing_libraries()
    need_xvfb = shutil.which("Xvfb") is None

    if not missing and not need_xvfb:
        log("dependencies ok (Xvfb + %d shared libraries)" % len(REQUIRED_LIBS))
        return True

    if auto_install:
        install_dependencies(missing, need_xvfb)
        missing = missing_libraries()
        need_xvfb = shutil.which("Xvfb") is None
        if not missing and not need_xvfb:
            log("dependencies installed")
            return True

    parts = []
    if need_xvfb:
        parts.append("Xvfb (virtual display)")
    if missing:
        parts.append("%d shared libraries: %s" % (len(missing), ", ".join(missing)))

    message = "missing dependencies - " + "; ".join(parts)
    _, command = dependency_install_command(missing, need_xvfb)
    hint = None
    if command:
        prefix = "" if os.geteuid() == 0 else "sudo "
        hint = "install them with:  %s%s" % (prefix, " ".join(command))

    if skip:
        warn(message)
        if hint:
            log(hint)
        return False

    fail(
        message,
        hint or "install Xvfb and the Chromium runtime libraries for your distro",
    )


# ---------------------------------------------------------------- download
def default_install_dir():
    if os.geteuid() == 0:
        return "/opt/9hits"
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    return os.path.join(base, "9hits")


def download(url, destination):
    """Download ``url`` to ``destination`` with a simple progress line."""
    request = urllib.request.Request(
        url, headers={"User-Agent": "hits4me-native/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            last_report = 0.0
            with open(destination, "wb") as handle:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_report >= 2.0:
                        last_report = now
                        if total:
                            log(
                                "  downloaded %.1f/%.1f MB (%d%%)"
                                % (
                                    downloaded / 1048576.0,
                                    total / 1048576.0,
                                    downloaded * 100 // total,
                                )
                            )
                        else:
                            log("  downloaded %.1f MB" % (downloaded / 1048576.0))
    except (urllib.error.URLError, OSError, socket.timeout) as exc:
        raise RuntimeError("download failed: %s" % exc)

    if os.path.getsize(destination) == 0:
        raise RuntimeError("download failed: empty file")

    # Validate it really is the bzip2 tarball and not an HTML error page /
    # captive-portal redirect, which would otherwise fail confusingly later.
    try:
        with tarfile.open(destination, "r:bz2") as archive:
            archive.next()
    except tarfile.TarError as exc:
        raise RuntimeError(
            "downloaded file is not a valid .tar.bz2 archive (%s) - "
            "the URL may be wrong or returned an error page" % exc
        )


def _safe_extract(archive, target):
    """Extract ``archive`` into ``target``, stripping the top-level directory
    and refusing any member that would escape the target (tar path traversal).
    """
    target = os.path.abspath(target)
    members = []
    for member in archive.getmembers():
        name = member.name.lstrip("./")
        parts = name.split("/", 1)
        stripped = parts[1] if len(parts) > 1 else ""
        if not stripped:
            continue
        member.name = stripped
        destination = os.path.abspath(os.path.join(target, stripped))
        if destination != target and not destination.startswith(target + os.sep):
            raise RuntimeError("refusing unsafe path in archive: %s" % name)
        members.append(member)
    archive.extractall(target, members=members)


def ensure_viewer(install_dir, url=DOWNLOAD_URL, force=False):
    """Return the path to the nhviewer binary, downloading it if needed."""
    binary = os.path.join(install_dir, VIEWER_BINARY)

    if os.path.isfile(binary) and not force:
        log("viewer already installed: %s" % binary)
        os.chmod(binary, 0o755)
        return binary

    log("downloading 9Hits Viewer v6 from %s" % url)
    os.makedirs(install_dir, exist_ok=True)

    handle, tmp_path = tempfile.mkstemp(prefix="nhviewer-", suffix=".tar.bz2")
    os.close(handle)
    try:
        download(url, tmp_path)
        log("extracting to %s" % install_dir)
        with tarfile.open(tmp_path, "r:bz2") as archive:
            _safe_extract(archive, install_dir)
    except (RuntimeError, tarfile.TarError) as exc:
        fail(
            str(exc),
            "check your network/DNS, or pass --download-url to use a mirror.",
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if not os.path.isfile(binary):
        fail(
            "%s not found after extraction" % binary,
            "the archive layout may have changed; inspect %s" % install_dir,
        )

    os.chmod(binary, 0o755)
    log("installed %s" % binary)
    return binary


# ----------------------------------------------------------- virtual display
class VirtualDisplay:
    """A supervised Xvfb instance (the viewer needs an X server even headless)."""

    def __init__(self, resolution=None, display=None):
        self.resolution = resolution or self._auto_resolution()
        self.display = display
        self.process = None
        self._owned = False

    @staticmethod
    def _auto_resolution():
        try:
            cores = os.cpu_count() or 1
        except NotImplementedError:
            cores = 1
        memory_mb = 0
        try:
            with open("/proc/meminfo", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("MemTotal:"):
                        memory_mb = int(line.split()[1]) // 1024
                        break
        except (OSError, ValueError, IndexError):
            pass
        if cores >= 4 and memory_mb >= 4000:
            return "2560x1440x24"
        return "1920x1080x24"

    @staticmethod
    def _display_free(number):
        return not (
            os.path.exists("/tmp/.X%d-lock" % number)
            or os.path.exists("/tmp/.X11-unix/X%d" % number)
        )

    def _pick_display(self):
        for number in range(99, 130):
            if self._display_free(number):
                return ":%d" % number
        fail("could not find a free X display number between :99 and :129")

    def start(self):
        existing = os.environ.get("DISPLAY", "").strip()
        if existing and shutil.which("xdpyinfo"):
            probe = subprocess.call(
                ["xdpyinfo", "-display", existing],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if probe == 0:
                log("reusing existing X display %s" % existing)
                self.display = existing
                return existing

        if shutil.which("Xvfb") is None:
            fail(
                "Xvfb is not installed and no usable DISPLAY was found",
                "install it (Debian/Ubuntu: sudo apt-get install -y xvfb) "
                "or re-run with --install-deps as root.",
            )

        self.display = self.display or self._pick_display()
        number = int(self.display.lstrip(":").split(".")[0])
        for path in ("/tmp/.X%d-lock" % number, "/tmp/.X11-unix/X%d" % number):
            try:
                os.unlink(path)
            except OSError:
                pass

        log("starting Xvfb on %s (%s)" % (self.display, self.resolution))
        self.process = subprocess.Popen(
            [
                "Xvfb",
                self.display,
                "-screen",
                "0",
                self.resolution,
                "-nolisten",
                "tcp",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._owned = True

        deadline = time.time() + 15
        while time.time() < deadline:
            if self.process.poll() is not None:
                fail(
                    "Xvfb exited immediately (code %s)" % self.process.returncode,
                    "try a different --resolution, or check that /tmp is writable.",
                )
            if os.path.exists("/tmp/.X11-unix/X%d" % number):
                time.sleep(0.5)
                log("virtual display ready on %s" % self.display)
                return self.display
            time.sleep(0.25)

        fail("Xvfb did not become ready within 15s")

    def alive(self):
        if not self._owned:
            return True
        return self.process is not None and self.process.poll() is None

    def stop(self):
        if not self._owned or self.process is None:
            return
        if self.process.poll() is None:
            log("stopping Xvfb")
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            except OSError:
                pass


# ------------------------------------------------------------- supervisor
class ViewerSupervisor:
    """Keeps the viewer alive: relaunch whenever it exits (crash or reset)."""

    def __init__(self, binary, run_flags, env, delay=10, writer=None, logger=None):
        self.binary = binary
        self.run_flags = list(run_flags)
        self.env = env
        self.delay = delay
        # Where the viewer's PTY output goes (default: this process' stdout).
        # app.py passes a capturing writer to feed the Gradio dashboard.
        self.writer = writer
        self.log = logger or log
        self.restarts = 0
        self.viewer_pid = 0
        self.last_exit_code = None
        self.started_at = None
        self._stop = threading.Event()
        self._thread = None

    # -- state used by the /health endpoint -------------------------------
    def status(self):
        return {
            "viewer_running": self.viewer_pid > 0,
            "supervisor_running": bool(self._thread and self._thread.is_alive()),
            "viewer_pid": self.viewer_pid,
            "restarts": self.restarts,
            "last_exit_code": self.last_exit_code,
            "viewer_uptime_seconds": (
                int(time.time() - self.started_at) if self.started_at else 0
            ),
            "uptime_seconds": int(time.time() - _START_TIME),
        }

    def _record_pid(self, pid):
        self.viewer_pid = pid
        self.started_at = time.time()

    def _loop(self):
        argv = [self.binary] + self.run_flags
        while not self._stop.is_set():
            self.log("launching: %s" % viewer_config.redacted_line(argv))
            previous_environ = os.environ.copy()
            os.environ.update(self.env)
            try:
                code = run_pty.run(
                    argv,
                    writer=self.writer or sys.stdout.buffer,
                    pid_callback=self._record_pid,
                    forward_signals=False,
                )
            except Exception as exc:  # never let the supervisor thread die
                self.log("WARNING: failed to launch viewer: %s" % exc)
                code = 127
            finally:
                os.environ.clear()
                os.environ.update(previous_environ)
                self.viewer_pid = 0

            self.last_exit_code = code
            if self._stop.is_set():
                break

            self.restarts += 1
            self.log(
                "viewer exited (code %s) - restart #%d in %ds"
                % (code, self.restarts, self.delay)
            )
            self._stop.wait(self.delay)

        self.log("supervisor stopped")

    def start(self):
        self._thread = threading.Thread(
            target=self._loop, name="viewer-supervisor", daemon=True
        )
        self._thread.start()

    def stop(self, timeout=15):
        self._stop.set()
        pid = self.viewer_pid
        if pid > 1:
            self.log("stopping viewer (pid %d)" % pid)
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            deadline = time.time() + timeout
            while time.time() < deadline and self.viewer_pid == pid:
                time.sleep(0.25)
            if self.viewer_pid == pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        if self._thread:
            self._thread.join(timeout=5)


def kill_stray_viewers():
    """Terminate leftover nhviewer/browser processes from a previous run."""
    if not shutil.which("pkill"):
        return
    for name in ("nhviewer", "may"):
        subprocess.call(
            ["pkill", "-TERM", "-x", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    time.sleep(1)
    for name in ("nhviewer", "may"):
        subprocess.call(
            ["pkill", "-KILL", "-x", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


# ------------------------------------------------------------------- CLI
def build_parser():
    parser = argparse.ArgumentParser(
        prog="run_native.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Run the 9Hits Viewer v6 natively, without Docker.\n"
            "Every option also reads its environment variable counterpart "
            "(see README.md); the command line wins."
        ),
        epilog=(
            "examples:\n"
            "  ACCESS_KEY=xxxx python3 run_native.py --system-session\n"
            "  python3 run_native.py --access-key=xxxx --system-session "
            "--reset-interval=2h --port=10000\n"
            "  sudo python3 run_native.py --install-deps --access-key=xxxx "
            "--system-session\n"
        ),
    )

    viewer = parser.add_argument_group("viewer configuration")
    viewer.add_argument("--access-key", help="9Hits access key (env ACCESS_KEY)")
    viewer.add_argument(
        "--system-session",
        action="store_true",
        default=None,
        help="run a session on this machine's own IP (env SYSTEM_SESSION)",
    )
    viewer.add_argument(
        "--no-system-session", dest="system_session", action="store_false"
    )
    viewer.add_argument(
        "--clear-all-sessions",
        action="store_true",
        default=None,
        help="wipe previously created sessions first (env CLEAR_ALL_SESSIONS)",
    )
    viewer.add_argument(
        "--no-clear-all-sessions", dest="clear_all_sessions", action="store_false"
    )
    viewer.add_argument("--session-note", help="session label (env SESSION_NOTE)")
    viewer.add_argument("--note", help="machine label (env NOTE)")
    viewer.add_argument("--hide-browser", help="yes/no (env HIDE_BROWSER)")
    viewer.add_argument("--allow-popups", help="yes/no (env ALLOW_POPUPS)")
    viewer.add_argument("--allow-adult", help="yes/no (env ALLOW_ADULT)")
    viewer.add_argument("--allow-crypto", help="yes/no (env ALLOW_CRYPTO)")
    viewer.add_argument("--cache-limit", help="disk cache bytes (env CACHE_LIMIT)")
    viewer.add_argument("--cache-path", help="cache directory (env CACHE_PATH)")
    viewer.add_argument(
        "--reset-interval", help="periodic reset, e.g. 2h (env RESET_INTERVAL)"
    )
    viewer.add_argument("--hide-columns", help="columns to hide (env HIDE_COLUMNS)")

    proxies = parser.add_argument_group("proxies")
    proxies.add_argument(
        "--bulk-add-proxy-list", help="pipe-delimited list (env BULK_ADD_PROXY_LIST)"
    )
    proxies.add_argument(
        "--bulk-add-proxy-type", help="socks5/http/socks4/ssh (env BULK_ADD_PROXY_TYPE)"
    )
    proxies.add_argument(
        "--bulk-add-proxy-list-url",
        help="download the proxy list at boot (env BULK_ADD_PROXY_LIST_URL)",
    )
    proxies.add_argument(
        "--ex-proxy-sessions", help="pool session count (env EX_PROXY_SESSIONS)"
    )
    proxies.add_argument("--ex-proxy-url", help="custom pool URL (env EX_PROXY_URL)")

    runtime = parser.add_argument_group("runner")
    runtime.add_argument(
        "--install-dir",
        help="where to download/extract the viewer "
        "(env INSTALL_DIR; default /opt/9hits as root, else ~/.local/share/9hits)",
    )
    runtime.add_argument("--download-url", default=None, help="override the tarball URL")
    runtime.add_argument(
        "--port",
        type=int,
        help="port for the /health endpoint (env PORT, default 10000)",
    )
    runtime.add_argument(
        "--no-health-server",
        action="store_true",
        help="don't serve /health (for local runs)",
    )
    runtime.add_argument(
        "--supervisor-delay",
        type=int,
        help="seconds before relaunching an exited viewer (env SUPERVISOR_DELAY)",
    )
    runtime.add_argument(
        "--resolution", help="Xvfb resolution, e.g. 1920x1080x24 (default: auto)"
    )
    runtime.add_argument("--display", help="X display to use, e.g. :99 (default: auto)")
    runtime.add_argument(
        "--install-deps",
        action="store_true",
        help="install missing system packages (needs root)",
    )
    runtime.add_argument(
        "--skip-checks",
        action="store_true",
        help="warn instead of aborting on failed platform/dependency checks",
    )
    runtime.add_argument(
        "--force-download", action="store_true", help="re-download even if installed"
    )
    runtime.add_argument(
        "--skip-init",
        action="store_true",
        help="skip the one-time config pass (reuse the persisted configuration)",
    )
    runtime.add_argument(
        "--extra-arg",
        action="append",
        default=[],
        metavar="FLAG",
        help="extra flag passed straight to the viewer (repeatable)",
    )

    return parser


def apply_cli_to_env(args, env):
    """CLI flags override the environment; the result drives viewer_config."""
    mapping = {
        "access_key": "ACCESS_KEY",
        "session_note": "SESSION_NOTE",
        "note": "NOTE",
        "hide_browser": "HIDE_BROWSER",
        "allow_popups": "ALLOW_POPUPS",
        "allow_adult": "ALLOW_ADULT",
        "allow_crypto": "ALLOW_CRYPTO",
        "cache_limit": "CACHE_LIMIT",
        "cache_path": "CACHE_PATH",
        "reset_interval": "RESET_INTERVAL",
        "hide_columns": "HIDE_COLUMNS",
        "bulk_add_proxy_list": "BULK_ADD_PROXY_LIST",
        "bulk_add_proxy_type": "BULK_ADD_PROXY_TYPE",
        "bulk_add_proxy_list_url": "BULK_ADD_PROXY_LIST_URL",
        "ex_proxy_sessions": "EX_PROXY_SESSIONS",
        "ex_proxy_url": "EX_PROXY_URL",
        "install_dir": "INSTALL_DIR",
    }
    for attribute, variable in mapping.items():
        value = getattr(args, attribute, None)
        if value is not None:
            env[variable] = str(value)

    for attribute, variable in (
        ("system_session", "SYSTEM_SESSION"),
        ("clear_all_sessions", "CLEAR_ALL_SESSIONS"),
    ):
        value = getattr(args, attribute, None)
        if value is not None:
            env[variable] = "yes" if value else "no"

    if args.port is not None:
        env["PORT"] = str(args.port)
    if args.supervisor_delay is not None:
        env["SUPERVISOR_DELAY"] = str(args.supervisor_delay)

    return env


def resolve_proxy_list(env):
    """Honour BULK_ADD_PROXY_LIST_URL exactly like start.sh does."""
    url = viewer_config.env_str("BULK_ADD_PROXY_LIST_URL", env=env)
    if not url or viewer_config.env_str("BULK_ADD_PROXY_LIST", env=env):
        return
    try:
        proxies = fetch_proxy_list(url)
    except ProxyFetchError as exc:
        warn("failed to fetch proxy list from BULK_ADD_PROXY_LIST_URL (%s)" % exc)
        return
    env["BULK_ADD_PROXY_LIST"] = proxies
    env.setdefault("BULK_ADD_PROXY_TYPE", "socks5")
    if not env.get("BULK_ADD_PROXY_TYPE"):
        env["BULK_ADD_PROXY_TYPE"] = "socks5"
    log("fetched %d proxies from BULK_ADD_PROXY_LIST_URL" % len(proxies.split("|")))


def sanity_warnings(env):
    ex_sessions = viewer_config.env_str("EX_PROXY_SESSIONS", env=env)
    if (
        ex_sessions.isdigit()
        and int(ex_sessions) > 0
        and not viewer_config.env_str("EX_PROXY_URL", env=env)
        and not viewer_config.env_str("BULK_ADD_PROXY_LIST", env=env)
    ):
        warn(
            "EX_PROXY_SESSIONS is set (%s) but EX_PROXY_URL and "
            "BULK_ADD_PROXY_LIST are empty. The 9Hits public pool is CLOSED - "
            "every pool session will fail. Configure your own pool at "
            "https://dash.9hits.com/pool or use BULK_ADD_PROXY_LIST." % ex_sessions
        )

    sessions = viewer_config.estimate_sessions(env)
    memory_mb = 0
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    memory_mb = int(line.split()[1]) // 1024
                    break
    except (OSError, ValueError, IndexError):
        pass
    if 0 < memory_mb < 1024 and sessions >= 4:
        warn(
            "detected %d MB RAM with ~%d estimated sessions; machines under "
            "1 GB may get OOM-killed with 4+ sessions."
            % (memory_mb, sessions)
        )

    if not viewer_config.env_str("ACCESS_KEY", env=env):
        warn(
            "ACCESS_KEY is not set - the viewer will report 'User not found!'. "
            "Get yours at https://panel.9hits.com/user/profile"
        )


def run_init_pass(binary, config_args, env, install_dir):
    """One-time pass that persists settings/sessions, then exits."""
    argv = [binary] + config_args + ["--exit-on-init"]
    log("applying configuration: %s" % viewer_config.redacted_line(argv))
    code = run_pty.run(argv, writer=sys.stdout.buffer)
    if code != 0:
        warn("configuration pass exited with code %s" % code)
    else:
        log("configuration applied")
    return code


def main(argv=None):
    args = build_parser().parse_args(argv)

    env = apply_cli_to_env(args, dict(os.environ))

    print("=" * 74, flush=True)
    print(" 9Hits Viewer v6 - native runner (no Docker required)", flush=True)
    print("=" * 74, flush=True)

    check_platform(skip=args.skip_checks)
    check_dependencies(auto_install=args.install_deps, skip=args.skip_checks)

    install_dir = os.path.abspath(
        os.path.expanduser(
            args.install_dir
            or viewer_config.env_str("INSTALL_DIR", env=env)
            or default_install_dir()
        )
    )
    try:
        os.makedirs(install_dir, exist_ok=True)
    except OSError as exc:
        hint = None
        if exc.errno in (errno.EACCES, errno.EPERM):
            hint = "pick a writable path with --install-dir=$HOME/9hits"
        fail("cannot create install dir %s: %s" % (install_dir, exc), hint)

    binary = ensure_viewer(
        install_dir,
        url=args.download_url or DOWNLOAD_URL,
        force=args.force_download,
    )

    resolve_proxy_list(env)
    sanity_warnings(env)

    # The viewer keeps its config/cache next to the binary; make sure HOME
    # points somewhere writable for the bundled Chromium profile.
    env.setdefault("HOME", os.path.expanduser("~"))
    env.setdefault("CACHE_PATH", os.path.join(install_dir, "cache"))
    if not viewer_config.env_str("CACHE_LIMIT", env=env):
        env["CACHE_LIMIT"] = DEFAULT_CACHE_LIMIT

    config_args = viewer_config.build_config_args(env=env, extra=args.extra_arg)
    run_flags = viewer_config.build_run_flags(env=env)

    display = VirtualDisplay(resolution=args.resolution, display=args.display)
    supervisor = None
    server = None
    shutting_down = threading.Event()

    def shutdown(signum=None, _frame=None):
        if shutting_down.is_set():
            return
        shutting_down.set()
        if signum:
            log("received signal %s - shutting down" % signum)
        if supervisor:
            supervisor.stop()
        if server:
            server.shutdown()
        display.stop()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        kill_stray_viewers()
        os.environ["DISPLAY"] = display.start()
        env["DISPLAY"] = os.environ["DISPLAY"]
        env["INSTALL_DIR"] = install_dir

        if not args.skip_init:
            run_init_pass(binary, config_args, env, install_dir)
        else:
            log("skipping configuration pass (--skip-init)")

        if shutting_down.is_set():
            return 0

        supervisor = ViewerSupervisor(
            binary,
            run_flags,
            env,
            delay=int(
                viewer_config.env_str("SUPERVISOR_DELAY", "10", env=env) or 10
            ),
        )
        supervisor.start()

        if not args.no_health_server:
            port = int(viewer_config.env_str("PORT", "10000", env=env) or 10000)
            try:
                server = health_server.serve_in_thread(port, supervisor.status)
                log("health endpoint on 0.0.0.0:%d (GET /health)" % port)
            except OSError as exc:
                warn("could not start health server on port %d: %s" % (port, exc))

        log("running - press Ctrl+C to stop")

        # Main loop: keep the display alive and idle until interrupted.
        while not shutting_down.is_set():
            if not display.alive():
                warn("Xvfb died - restarting the virtual display")
                display.start()
                os.environ["DISPLAY"] = display.display
                env["DISPLAY"] = display.display
            time.sleep(2)
    except KeyboardInterrupt:
        shutdown()
    finally:
        shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())
