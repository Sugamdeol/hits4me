#!/usr/bin/env python3
"""
supervisor.py - 24x7 process manager for the 9Hits viewer container.

This is a tiny, stdlib-only init/supervisor. It is designed for
**continuous operation**: the supervisor is the only long-lived process in
the container, and it keeps every other long-lived child alive
independently for as long as the container itself is running.

Design principles
=================

* **No "sleep & pray" / no HTTP keep-alive.** The
  supervisor runs a tight loop, owns every child as a direct subprocess
  child, reaps dead children immediately, and restarts them on their own
  schedule. It does not depend on a web request or any user interaction
  to keep the viewers running.

* **Each service is an independent slot.** A crash in 9Hits does NOT
  touch the /health server and vice versa. Each slot has its own PID,
  log file, restart counter, exponential backoff, and optional memory
  cap.

* **The supervisor is the parent of every child.** Children are spawned
  in their own session (os.setsid) so a SIGTERM to the supervisor
  reaches the whole subtree.

* **No duplicate processes.** ``ManagedSlot.start()`` checks that no
  proc object is already alive for the slot, and an additional
  /proc-based guard at supervisor startup refuses to launch a second
  instance of a viewer if the binary is already running externally.

* **Crash-loop safety.** A slot that crash-loops in rapid succession
  backs off exponentially (10 -> 20 -> 40 -> 80 -> 120 s, capped by
  ``SUPERVISOR_MAX_DELAY``) so a broken deploy cannot burn the host's
  CPU. After a configurable number of rapid crashes the slot is parked
  for a long interval (default 5 minutes) before trying again - it
  will still recover automatically once the underlying issue clears.

* **Log rotation per slot.** Each slot rotates its log file when it
  exceeds ``SLOT_LOG_MAX_BYTES`` (default 10 MB), keeping
  ``SLOT_LOG_BACKUPS`` backup files. /logs/9hits.log.1 etc. are the
  previous copies. A 10 MB cap with 2 backups = 30 MB worst case per
  slot - well under a free-tier disk.

* **Per-slot memory cap.** Optional ``NH_MAX_MEMORY_MB`` (also
  accepted: ``NINEHITS_MAX_MEMORY_MB``) makes the supervisor gracefully
  restart ONLY the offending slot when its process tree's RSS exceeds
  the cap. The per-slot cap catches a runaway viewer before the total
  RSS trips the cgroup OOM.

* **Per-slot health-check interval.** ``NINEHITS_CHECK_INTERVAL``
  (default 30 s) sets the cadence at
  which the supervisor's expensive per-slot checks (memory, process
  count) run for each slot. The main loop still ticks at 500 ms for
  fast signal/crash response.

* **Clean shutdown.** SIGTERM, SIGINT, SIGQUIT, and SIGHUP all stop
  the supervisor. It then propagates SIGTERM to every child, waits
  their ``stop_grace``, then SIGKILLs anything still alive. No orphans.

* **No external dependencies.** Pure Python 3.7+ stdlib.

Environment variables (new in this revision)
============================================

Slot visibility / cadence
  NINEHITS_CHECK_INTERVAL         seconds between expensive 9Hits checks
                                   (memory, proc count). Default 30.
  SUPERVISOR_TICK                 main loop tick in seconds. Default 0.5.
                                   Lower = faster signal/crash response,
                                   slightly higher steady-state CPU.

Restart policy
  SUPERVISOR_DELAY                base restart cooldown (s). Default 10.
  SUPERVISOR_MAX_DELAY            backoff ceiling (s). Default 120.
  SUPERVISOR_PARK_AFTER           after N rapid crashes, park the slot
                                   for SUPERVISOR_PARK_SECS before
                                   trying again. Default 10 / 300 s.

Memory protection
  NINEHITS_MAX_MEMORY_MB / NH_MAX_MEMORY_MB
                                  when 9Hits process tree RSS exceeds
                                   this, the supervisor gracefully
                                   restarts ONLY 9Hits. Default 0 = off.
  NINEHITS_MAX_CHILDREN / NH_MAX_CHILDREN
                                  refuse to start a 9Hits slot whose
                                   process tree would exceed N
                                   children (fork-bomb guard).
                                   Default 0 = off.

Log rotation
  SLOT_LOG_MAX_BYTES              rotate per-slot log at this size.
                                   Default 10 MiB.
  SLOT_LOG_BACKUPS                number of rotated copies to keep.
                                   Default 2.

Paths (all already supported)
  LOG_DIR, HEALTH_SERVER_PATH, NINEHITS_LAUNCHER_PATH,
  SUPERVISOR_DISABLED

All other env vars (LOW_MEMORY, etc.) are honoured by the existing
start.sh layer; the supervisor does not consume them.
"""

import errno
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Paths & env
# --------------------------------------------------------------------------- #

STATE_FILE = "/tmp/supervisor_state.json"
CONTROL_DIR = "/tmp/supervisor_ctl"
ALIVE_FILE = "/tmp/supervisor.alive"
DEFAULT_LOG_DIR = "/logs"
LOG_DIR = os.environ.get("LOG_DIR", DEFAULT_LOG_DIR)
SUPERVISOR_VERSION = "2.0.0"

# Path overrides - allow local testing outside the Docker image where the
# launcher scripts do not live at /.  Production deployments do not set
# these.
HEALTH_SERVER_PATH = os.environ.get("HEALTH_SERVER_PATH", "/health_server.py")
NINEHITS_LAUNCHER_PATH = os.environ.get("NINEHITS_LAUNCHER_PATH", "/start.sh")

# Restart / backoff policy. Concrete numbers are read from the
# environment below, after the helper functions are defined.
DEFAULT_RESTART_DELAY = 10.0
MAX_RESTART_DELAY = 120.0
# After this many rapid crashes, the slot is parked for PARK_SECS
# before the next try. It will still recover automatically - the goal
# is just to prevent the supervisor from burning the host's CPU when
# a viewer is permanently broken on this host.
PARK_AFTER_CRASHES = 10
PARK_SECS = 300.0

# Main loop cadence.
TICK_SECONDS = 0.5

# Per-slot health check interval. The main loop runs every TICK_SECONDS
# (fast signal/crash response), but the per-slot expensive checks
# (/proc memory + proc count) only run once per this interval.
def _env_pos_int(name: str, default: int) -> int:
    try:
        v = int(str(os.environ.get(name, "") or "").strip() or default)
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


# Per-slot health check interval.
NINEHITS_CHECK_INTERVAL = _env_pos_int("NINEHITS_CHECK_INTERVAL", 30)

# Per-slot memory cap. 0 = disabled. Two names are accepted:
# NINEHITS_MAX_MEMORY_MB and the shorter NH_MAX_MEMORY_MB.
NINEHITS_MAX_MEMORY_MB = _env_pos_int("NINEHITS_MAX_MEMORY_MB", 0) or _env_pos_int(
    "NH_MAX_MEMORY_MB", 0
)
# Per-slot child-process count cap. 0 = disabled.
NINEHITS_MAX_CHILDREN = _env_pos_int("NINEHITS_MAX_CHILDREN", 0) or _env_pos_int(
    "NH_MAX_CHILDREN", 0
)

# Log rotation.
SLOT_LOG_MAX_BYTES = _env_pos_int("SLOT_LOG_MAX_BYTES", 10 * 1024 * 1024)
SLOT_LOG_BACKUPS = max(0, _env_pos_int("SLOT_LOG_BACKUPS", 2))

# In-memory log tail shown on the dashboard.
LOG_TAIL_LINES = 200

# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "yes", "true", "on"):
        return True
    if raw in ("0", "no", "false", "off", ""):
        return False
    return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "") or "").strip() or default)
    except (TypeError, ValueError):
        return default


def _load_config_from_env() -> None:
    """Populate the supervisor's module-level configuration constants
    from the environment. Called once from main() at startup, AFTER
    the helper functions are defined. We use ``global`` so the
    constants can keep their module-level names - slot code reads them
    as bare names (``PARK_AFTER_CRASHES``) which is far clearer than
    threading them through every constructor."""
    global DEFAULT_RESTART_DELAY, MAX_RESTART_DELAY
    global PARK_AFTER_CRASHES, PARK_SECS, TICK_SECONDS
    global NINEHITS_CHECK_INTERVAL
    global NINEHITS_MAX_MEMORY_MB
    global NINEHITS_MAX_CHILDREN
    global SLOT_LOG_MAX_BYTES, SLOT_LOG_BACKUPS
    DEFAULT_RESTART_DELAY = float(
        str(os.environ.get("SUPERVISOR_DELAY", "10") or "10").strip() or "10"
    )
    MAX_RESTART_DELAY = max(
        DEFAULT_RESTART_DELAY * 4,
        float(str(os.environ.get("SUPERVISOR_MAX_DELAY", "120") or "120").strip() or "120"),
    )
    PARK_AFTER_CRASHES = _env_int("SUPERVISOR_PARK_AFTER", 10)
    PARK_SECS = float(
        str(os.environ.get("SUPERVISOR_PARK_SECS", "300") or "300").strip() or "300"
    )
    TICK_SECONDS = float(
        str(os.environ.get("SUPERVISOR_TICK", "0.5") or "0.5").strip() or "0.5"
    )
    NINEHITS_CHECK_INTERVAL = _env_pos_int("NINEHITS_CHECK_INTERVAL", 30)
    NINEHITS_MAX_MEMORY_MB = _env_pos_int("NINEHITS_MAX_MEMORY_MB", 0) or _env_pos_int(
        "NH_MAX_MEMORY_MB", 0
    )
    NINEHITS_MAX_CHILDREN = _env_pos_int("NINEHITS_MAX_CHILDREN", 0) or _env_pos_int(
        "NH_MAX_CHILDREN", 0
    )
    SLOT_LOG_MAX_BYTES = _env_pos_int("SLOT_LOG_MAX_BYTES", 10 * 1024 * 1024)
    SLOT_LOG_BACKUPS = max(0, _env_pos_int("SLOT_LOG_BACKUPS", 2))


def _self_log(msg: str) -> None:
    line = "[supervisor] %s\n" % msg
    _SELF_LOG_FP_LOCK.acquire()
    try:
        if _SELF_LOG_FP is not None:
            _SELF_LOG_FP.write(line)
            _SELF_LOG_FP.flush()
        else:
            sys.stdout.write(line)
            sys.stdout.flush()
    finally:
        _SELF_LOG_FP_LOCK.release()


_SELF_LOG_FP = None
_SELF_LOG_FP_LOCK = threading.Lock()


def _open_self_log(path: str) -> None:
    global _SELF_LOG_FP
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _SELF_LOG_FP = open(path, "a", buffering=1, encoding="utf-8", errors="replace")


def _pid_alive(pid: int) -> bool:
    """True iff PID is a real, runnable process (zombies count as dead)."""
    if pid <= 1:
        return False
    try:
        with open("/proc/%d/stat" % pid, "rb") as fh:
            data = fh.read()
    except OSError:
        return False
    rparen = data.rfind(b")")
    if rparen < 0:
        return False
    state = chr(data[rparen + 2])
    if state in ("Z", "X"):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_int(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0


# --------------------------------------------------------------------------- #
# Per-process-tree memory / child-count sampling (Linux /proc only)
# --------------------------------------------------------------------------- #


def _all_pids() -> List[int]:
    try:
        return [int(e) for e in os.listdir("/proc") if e.isdigit()]
    except OSError:
        return []


def _parent_map() -> Dict[int, List[int]]:
    """Return {parent_pid: [child_pid, ...]} by reading /proc/<pid>/stat."""
    children: Dict[int, List[int]] = {}
    for pid in _all_pids():
        try:
            with open("/proc/%d/stat" % pid, "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        rparen = raw.rfind(b")")
        if rparen < 0:
            continue
        try:
            ppid = int(raw[rparen + 2:].split()[1])
        except (ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(pid)
    return children


def _process_tree(root_pid: int) -> List[int]:
    """All PIDs in the subtree rooted at root_pid (BFS)."""
    if root_pid <= 1:
        return []
    children = _parent_map()
    seen: set = set()
    queue: List[int] = [root_pid]
    out: List[int] = []
    while queue:
        pid = queue.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
        queue.extend(children.get(pid, ()))
    return out


def _rss_mb(pid: int) -> float:
    try:
        with open("/proc/%d/status" % pid, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def _pss_mb(pid: int) -> Optional[float]:
    """Proportional set size for one pid, or None when unavailable.

    PSS divides every shared page by the number of processes mapping it,
    so summing PSS over a process tree gives the tree's REAL footprint.
    Summing VmRSS instead counts Chromium's large shared mappings once
    per process (~1.4 GB reported for a ~160 MB tree), which tripped the
    per-slot cap and restarted the viewer every 30 seconds.
    Requires Linux >= 4.14 (smaps_rollup)."""
    try:
        with open("/proc/%d/smaps_rollup" % pid, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("Pss:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return None


def _proc_mem_mb(pid: int) -> float:
    """PSS when the kernel exposes it, else VmRSS (older kernels)."""
    pss = _pss_mb(pid)
    return pss if pss is not None else _rss_mb(pid)


def _tree_stats(root_pid: int) -> Tuple[float, int]:
    """Return (total_PSS_MB, process_count) for the subtree rooted at
    ``root_pid``. Uses PSS (proportional set size) so shared pages are
    counted once, not once per process. Falls back to VmRSS on kernels
    without smaps_rollup. Excludes the root if it is no longer alive."""
    if root_pid <= 0 or not _pid_alive(root_pid):
        return 0.0, 0
    pids = _process_tree(root_pid)
    rss = sum(_proc_mem_mb(p) for p in pids)
    return round(rss, 1), len(pids)


def _count_matching_processes(exe_basenames: Tuple[str, ...]) -> int:
    """How many alive /proc entries have a real exe whose basename is in
    ``exe_basenames``? Used as a startup-time guard so we never launch
    a second instance of a viewer that is already running outside our
    control."""
    if not exe_basenames:
        return 0
    n = 0
    for pid in _all_pids():
        if not _pid_alive(pid):
            continue
        try:
            exe = os.readlink("/proc/%d/exe" % pid)
        except OSError:
            continue
        if os.path.basename(exe) in exe_basenames:
            n += 1
    return n


# --------------------------------------------------------------------------- #
# Log file with size-based rotation (no external deps)
# --------------------------------------------------------------------------- #


class RotatingLogFile:
    """Append-mode text file that rotates when its size exceeds a limit,
    keeping N backups. Thread-safe (one write at a time)."""

    def __init__(self, path: str, max_bytes: int, backups: int):
        self.path = path
        self.max_bytes = max(0, int(max_bytes))
        self.backups = max(0, int(backups))
        self._lock = threading.Lock()
        self._fp = None
        self._open()

    def _open(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._fp = open(self.path, "a", buffering=1, encoding="utf-8", errors="replace")

    def _size(self) -> int:
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def _rotate_locked(self) -> None:
        if self._fp is not None:
            try:
                self._fp.flush()
                self._fp.close()
            except OSError:
                pass
            self._fp = None
        # Shift: log -> log.1, log.1 -> log.2, ... drop the oldest.
        for i in range(self.backups, 0, -1):
            src = "%s.%d" % (self.path, i)
            dst = "%s.%d" % (self.path, i + 1)
            try:
                if os.path.exists(src):
                    if i == self.backups:
                        try:
                            os.unlink(src)
                        except OSError:
                            pass
                    os.rename(src, dst)
            except OSError:
                pass
        try:
            os.rename(self.path, "%s.1" % self.path)
        except OSError:
            pass
        self._open()

    def write(self, data: str) -> None:
        if not data:
            return
        if not data.endswith("\n"):
            data = data + "\n"
        with self._lock:
            if self._fp is None:
                self._open()
            try:
                if self.max_bytes > 0 and self._size() + len(data) > self.max_bytes:
                    self._rotate_locked()
                if self._fp is not None:
                    self._fp.write(data)
                    self._fp.flush()
            except OSError:
                # Best-effort: the file may have been removed; re-open next
                # time. Do not crash the supervisor over a log write error.
                try:
                    if self._fp is not None:
                        self._fp.close()
                    self._fp = None
                except OSError:
                    pass

    def close(self) -> None:
        with self._lock:
            if self._fp is not None:
                try:
                    self._fp.flush()
                    self._fp.close()
                except OSError:
                    pass
                self._fp = None

    def tail(self, max_chars: int = 4000) -> str:
        """Best-effort tail of the current log file (NOT the rotated
        copies). Used for the dashboard preview."""
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return ""
        with open(self.path, "rb") as fh:
            if size > max_chars:
                fh.seek(size - max_chars)
                data = fh.read()
            else:
                data = fh.read()
        return data.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# ManagedSlot: one long-lived child.
# --------------------------------------------------------------------------- #


class ManagedSlot:
    """Owns one long-lived child. Spawns, monitors, restarts, and stops
    it independently of every other slot."""

    STATUS_DISABLED = "disabled"
    STATUS_STOPPED = "stopped"
    STATUS_STARTING = "starting"
    STATUS_RUNNING = "running"
    STATUS_CRASHED = "crashed"
    STATUS_STOPPING = "stopping"
    STATUS_PARKED = "parked"  # cooling off after too many rapid crashes

    # Substrings (case-insensitive) of the proc exe basename. Used as a
    # startup-time guard so the supervisor refuses to launch a slot
    # whose binary is already running outside its control. Empty tuple
    # = skip the guard.
    EXE_GUARD_BASENAMES: Tuple[str, ...] = ()

    def __init__(
        self,
        name: str,
        command: List[str],
        enabled: bool,
        log_file: str,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        stop_grace: float = 15.0,
        cpu_shares: Optional[int] = None,
        mem_limit_mb: Optional[int] = None,
        check_interval: int = 30,
        max_memory_mb: int = 0,
        max_children: int = 0,
        description: str = "",
        exe_guard_basenames: Tuple[str, ...] = (),
    ):
        self.name = name
        self.command = list(command)
        self.enabled = enabled
        self.log_file = log_file
        self.env = dict(env) if env else {}
        self.cwd = cwd
        self.stop_grace = float(stop_grace)
        self.cpu_shares = cpu_shares
        self.mem_limit_mb = mem_limit_mb  # legacy, kept for /health
        self.check_interval = int(max(1, check_interval))
        self.max_memory_mb = int(max(0, max_memory_mb))
        self.max_children = int(max(0, max_children))
        self.description = description
        self.EXE_GUARD_BASENAMES = exe_guard_basenames

        self._proc: Optional[subprocess.Popen] = None
        self._popen_lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._log = RotatingLogFile(log_file, SLOT_LOG_MAX_BYTES, SLOT_LOG_BACKUPS)
        self._log_tail: Deque[str] = deque(maxlen=LOG_TAIL_LINES)
        self._restart_count = 0
        self._crash_streak = 0
        self._last_exit_code: Optional[int] = None
        self._last_error: Optional[str] = None
        self._last_memory_mb: float = 0.0
        self._mem_breaches: int = 0
        self._last_child_count: int = 0
        self._started_at: Optional[float] = None
        self._last_crash_at: float = 0.0
        self._pending_action: Optional[str] = None  # 'start'|'stop'|'restart'
        self._last_user_request: Optional[str] = None
        self._current_delay: float = DEFAULT_RESTART_DELAY
        self._parked_until: float = 0.0  # 0 = not parked
        self._user_paused: bool = False
        self._last_check_at: float = 0.0  # expensive-check cadence
        self._status = self.STATUS_DISABLED if not enabled else self.STATUS_STOPPED

    # ------------------------------------------------------------------ public

    def snapshot(self) -> Dict:
        now = time.time()
        if self._status == self.STATUS_RUNNING and self._started_at:
            uptime = int(max(0, now - self._started_at))
        else:
            uptime = 0
        # Live liveness probe at snapshot time so the health endpoint
        # never reports a stale pid.
        pid = self._proc.pid if (self._proc and _pid_alive(self._proc.pid)) else None
        status = self._status
        if self._user_paused and status in (
            self.STATUS_RUNNING,
            self.STATUS_CRASHED,
            self.STATUS_STOPPED,
        ):
            status = self.STATUS_STOPPED
        if not self.enabled:
            status = self.STATUS_DISABLED
        # Uptime in human form so a JSON consumer can pick either.
        uptime_human = self._fmt_duration(uptime)
        return {
            "name": self.name,
            "enabled": self.enabled,
            "description": self.description,
            "status": status,
            "pid": pid,
            "command": self.command,
            "log_file": self.log_file,
            "started_at": int(self._started_at) if self._started_at else None,
            "uptime_seconds": uptime,
            "uptime": uptime_human,
            "restart_count": self._restart_count,
            "crash_streak": self._crash_streak,
            "last_exit_code": self._last_exit_code,
            "last_error": self._last_error,
            "last_user_request": self._last_user_request,
            "user_paused": self._user_paused,
            "parked_until_epoch": int(self._parked_until) if self._parked_until else None,
            "memory_mb": self._last_memory_mb,
            "child_count": self._last_child_count,
            "max_memory_mb": self.max_memory_mb,
            "max_children": self.max_children,
            "check_interval_seconds": self.check_interval,
            "log_tail": "".join(self._log_tail),
            "cpu_shares": self.cpu_shares,
            "mem_limit_mb": self.mem_limit_mb,
        }

    @staticmethod
    def _fmt_duration(seconds: int) -> str:
        if seconds < 0:
            seconds = 0
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

    def request(self, action: str) -> Tuple[bool, str]:
        action = action.strip().lower()
        if action not in ("start", "stop", "restart"):
            return False, "unknown action: %r" % action
        if not self.enabled and action in ("start", "restart"):
            return False, "%s is disabled by configuration" % self.name
        self._last_user_request = action
        self._pending_action = action
        if action == "stop":
            self._user_paused = True
        else:
            self._user_paused = False
        return True, "queued %s for %s" % (action, self.name)

    # ------------------------------------------------------------------ log

    def _append_log(self, line: str) -> None:
        if not line.endswith("\n"):
            line = line + "\n"
        self._log_tail.append(line)
        # Best-effort disk write (rotation is internal to RotatingLogFile).
        try:
            self._log.write(line)
        except Exception:
            pass

    # ------------------------------------------------------------------ lifecycle

    def start(self, manual: bool = False) -> Tuple[bool, str]:
        if not self.enabled:
            return False, "disabled"
        with self._popen_lock:
            if self._proc is not None and _pid_alive(self._proc.pid):
                return True, "already running (pid %d)" % self._proc.pid
            # External-instance guard: if a copy of this viewer's binary
            # is already running (e.g. started outside the supervisor),
            # refuse to spawn a second one. The operator can override
            # by sending "restart" (which kills the existing process
            # first via stop()).
            if manual is False and self.EXE_GUARD_BASENAMES:
                existing = _count_matching_processes(self.EXE_GUARD_BASENAMES)
                if existing > 0:
                    msg = (
                        "%d %s process(es) already running - refusing to start "
                        "a second instance of %s" % (
                            existing, "/".join(self.EXE_GUARD_BASENAMES), self.name,
                        )
                    )
                    self._last_error = msg
                    self._append_log("[supervisor] %s\n" % msg)
                    return False, msg
            self._status = self.STATUS_STARTING
            self._pending_action = None
            try:
                env = dict(os.environ)
                env.update(self.env)
                self._proc = subprocess.Popen(
                    args=self.command,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    bufsize=0,
                    universal_newlines=True,
                    errors="replace",
                    cwd=self.cwd,
                    preexec_fn=os.setsid,  # own pgroup so SIGTERM kills the tree
                )
            except (OSError, ValueError) as exc:
                self._last_error = "failed to spawn: %s" % exc
                self._append_log("[supervisor] failed to spawn %s: %s\n" % (self.name, exc))
                self._proc = None
                self._status = self.STATUS_CRASHED
                return False, self._last_error
            self._append_log(
                "[supervisor] %s started (pid %d) %s\n"
                % (self.name, self._proc.pid, "manually" if manual else "automatically")
            )
            self._started_at = time.time()
            self._status = self.STATUS_RUNNING
            self._last_check_at = self._started_at
            self._reader_thread = threading.Thread(
                target=self._pump_output, name="slot-%s" % self.name, daemon=True
            )
            self._reader_thread.start()
            return True, "started (pid %d)" % self._proc.pid

    def stop(self, reason: str = "user request", grace: Optional[float] = None) -> Tuple[bool, str]:
        if self._proc is None or not _pid_alive(self._proc.pid):
            self._status = self.STATUS_STOPPED if self.enabled else self.STATUS_DISABLED
            return True, "not running"
        with self._popen_lock:
            proc = self._proc
            if proc is None or not _pid_alive(proc.pid):
                self._status = self.STATUS_STOPPED if self.enabled else self.STATUS_DISABLED
                return True, "not running"
            grace = self.stop_grace if grace is None else float(grace)
            self._status = self.STATUS_STOPPING
            self._append_log(
                "[supervisor] %s stopping (%s) - sending SIGTERM to pid %d\n"
                % (self.name, reason, proc.pid)
            )
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError as exc:
                self._append_log("[supervisor] SIGTERM failed: %s\n" % exc)
            deadline = time.time() + grace
            while time.time() < deadline and _pid_alive(proc.pid):
                time.sleep(0.2)
            if _pid_alive(proc.pid):
                self._append_log(
                    "[supervisor] %s still alive after %.1fs - SIGKILL\n"
                    % (self.name, grace)
                )
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                time.sleep(0.5)
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
            self._last_exit_code = proc.returncode
            self._started_at = None
            self._status = self.STATUS_STOPPED if self.enabled else self.STATUS_DISABLED
            try:
                self._log.close()
            except Exception:
                pass
            return True, "stopped (exit %s)" % self._last_exit_code

    def restart(self) -> Tuple[bool, str]:
        ok_s, msg_s = self.stop(reason="restart request", grace=self.stop_grace)
        # Even after a stop, the EXE_GUARD might still see the binary
        # (e.g. SIGKILL was not yet fully reaped). Bypass the guard for
        # the post-stop restart so the operator's "Restart" button
        # always works.
        ok_r, msg_r = self._start_unchecked(manual=True)
        if ok_r:
            self._restart_count += 1
        return ok_r, "%s; %s" % (msg_s, msg_r)

    def _start_unchecked(self, manual: bool = False) -> Tuple[bool, str]:
        """Same as start() but skips the EXE_GUARD. Used by restart(),
        which has just stopped the previous instance and may briefly
        still see its binary in /proc before the kernel reaps it."""
        if not self.enabled:
            return False, "disabled"
        with self._popen_lock:
            if self._proc is not None and _pid_alive(self._proc.pid):
                return True, "already running (pid %d)" % self._proc.pid
            self._status = self.STATUS_STARTING
            self._pending_action = None
            try:
                env = dict(os.environ)
                env.update(self.env)
                self._proc = subprocess.Popen(
                    args=self.command,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    bufsize=0,
                    universal_newlines=True,
                    errors="replace",
                    cwd=self.cwd,
                    preexec_fn=os.setsid,
                )
            except (OSError, ValueError) as exc:
                self._last_error = "failed to spawn: %s" % exc
                self._append_log("[supervisor] failed to spawn %s: %s\n" % (self.name, exc))
                self._proc = None
                self._status = self.STATUS_CRASHED
                return False, self._last_error
            self._append_log(
                "[supervisor] %s started (pid %d) %s (restart)\n"
                % (self.name, self._proc.pid, "manually" if manual else "automatically")
            )
            self._started_at = time.time()
            self._status = self.STATUS_RUNNING
            self._last_check_at = self._started_at
            self._reader_thread = threading.Thread(
                target=self._pump_output, name="slot-%s" % self.name, daemon=True
            )
            self._reader_thread.start()
            return True, "started (pid %d)" % self._proc.pid

    def _pump_output(self) -> None:
        assert self._proc is not None
        proc = self._proc
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                self._append_log(line)
        except (OSError, ValueError) as exc:
            self._append_log("[supervisor] log read error: %s\n" % exc)
        finally:
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
            self._last_exit_code = proc.returncode
            self._append_log(
                "[supervisor] %s exited (code %s)\n" % (self.name, self._last_exit_code)
            )
            try:
                self._log.close()
            except Exception:
                pass

    def reap(self) -> Optional[int]:
        if self._proc is None:
            return None
        if _pid_alive(self._proc.pid):
            return None
        try:
            self._proc.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            return None
        except (OSError, ValueError):
            return None
        code = self._proc.returncode
        if self._last_exit_code is None:
            self._last_exit_code = code
        # A "crash" is anything other than a clean exit (0) or an
        # operator-initiated stop (negative SIGTERM / SIGKILL).
        if code not in (0, -signal.SIGTERM, -signal.SIGKILL):
            self._last_error = "exit code %s" % code
            self._last_crash_at = time.time()
            self._crash_streak += 1
            self._current_delay = min(
                MAX_RESTART_DELAY,
                max(
                    DEFAULT_RESTART_DELAY,
                    DEFAULT_RESTART_DELAY * (2 ** min(10, self._crash_streak - 1)),
                ),
            )
        else:
            self._crash_streak = 0
            self._current_delay = DEFAULT_RESTART_DELAY
        self._proc = None
        self._started_at = None
        # If this slot was parked because of too many rapid crashes,
        # re-evaluate after the park interval.
        self._status = self.STATUS_CRASHED if self.enabled else self.STATUS_DISABLED
        return code

    # ------------------------------------------------------------------ monitoring

    def _do_expensive_check(self) -> None:
        """Sample memory and child count for this slot. Called at
        ``check_interval`` cadence (not on every tick) so the steady-
        state CPU cost stays near zero."""
        if self._proc is None or not _pid_alive(self._proc.pid):
            return
        rss, nproc = _tree_stats(self._proc.pid)
        self._last_memory_mb = rss
        self._last_child_count = nproc

    def _check_memory_cap(self) -> Optional[str]:
        """Returns a reason string if the slot must be restarted for
        exceeding its memory cap, else None."""
        if self.max_memory_mb <= 0:
            self._mem_breaches = 0
            return None
        if self._proc is None or not _pid_alive(self._proc.pid):
            self._mem_breaches = 0
            return None
        if self._last_memory_mb <= 0:
            self._mem_breaches = 0
            return None
        if self._last_memory_mb > self.max_memory_mb:
            self._mem_breaches += 1
            if self._mem_breaches >= 2:
                self._mem_breaches = 0
                return (
                    "memory %.0f MB (PSS) > cap %d MB for 2 consecutive checks" % (self._last_memory_mb, self.max_memory_mb)
                )
            return None
        self._mem_breaches = 0
        return None

    def _check_children_cap(self) -> Optional[str]:
        """Returns a reason string if the slot has too many children,
        else None. Guards against fork-bombs / runaway renderer
        processes (e.g. a Chromium that started spawning helper
        processes indefinitely)."""
        if self.max_children <= 0:
            return None
        if self._proc is None or not _pid_alive(self._proc.pid):
            return None
        if self._last_child_count <= 0:
            return None
        if self._last_child_count > self.max_children:
            return (
                "child count %d > cap %d"
                % (self._last_child_count, self.max_children)
            )
        return None

    # ------------------------------------------------------------------ main tick

    def tick(self) -> None:
        # 1) Apply explicit user action first - that always wins.
        if self._pending_action == "stop":
            self.stop(reason="user stop")
            self._pending_action = None
            return
        if self._pending_action == "restart":
            self._user_paused = False
            self._parked_until = 0.0
            self.restart()
            self._pending_action = None
            return
        if self._pending_action == "start":
            self._user_paused = False
            self._parked_until = 0.0
            self.start(manual=True)
            self._pending_action = None
            return

        if not self.enabled:
            return
        if self._user_paused:
            return

        # 2) Expensive per-slot check at the configured cadence.
        now = time.time()
        if now - self._last_check_at >= self.check_interval:
            self._last_check_at = now
            self._do_expensive_check()
            # Memory cap -> graceful restart of THIS slot only.
            reason = self._check_memory_cap()
            if reason is not None:
                self._append_log(
                    "[supervisor] %s over memory cap: %s - graceful restart\n"
                    % (self.name, reason)
                )
                self._last_error = reason
                self._last_crash_at = time.time()
                self._crash_streak += 1
                # Restart bypasses the EXE_GUARD - we know we want a
                # fresh instance.
                self._user_paused = False
                self._parked_until = 0.0
                self.restart()
                self._restart_count += 1
                return
            # Child count cap -> graceful restart of THIS slot only.
            reason = self._check_children_cap()
            if reason is not None:
                self._append_log(
                    "[supervisor] %s over child-count cap: %s - graceful restart\n"
                    % (self.name, reason)
                )
                self._last_error = reason
                self._last_crash_at = time.time()
                self._crash_streak += 1
                self._user_paused = False
                self._parked_until = 0.0
                self.restart()
                self._restart_count += 1
                return

        # 3) Crash recovery with exponential backoff and parking.
        if self._proc is not None and _pid_alive(self._proc.pid):
            return  # all good, slot is running
        # Process is gone.
        if self._crash_streak >= PARK_AFTER_CRASHES:
            # Park the slot for PARK_SECS. While parked we do NOT try
            # to restart - this caps CPU burn when a viewer is
            # permanently broken on this host. The park expires
            # automatically (next tick after now > parked_until) and
            # the supervisor will try once more.
            if self._parked_until == 0.0:
                self._parked_until = time.time() + PARK_SECS
                self._append_log(
                    "[supervisor] %s has crashed %d times in a row - "
                    "parking for %ds before trying again\n"
                    % (self.name, self._crash_streak, int(PARK_SECS))
                )
                self._status = self.STATUS_PARKED
            if time.time() < self._parked_until:
                return  # still parked
            # Park expired: try once.
            self._append_log(
                "[supervisor] %s park expired - retrying\n" % self.name
            )
            self._parked_until = 0.0
            self._crash_streak = 0
            self._current_delay = DEFAULT_RESTART_DELAY
        # Normal restart path.
        if self._status == self.STATUS_RUNNING:
            self._status = self.STATUS_CRASHED
        since = time.time() - self._last_crash_at
        if since >= self._current_delay:
            ok, msg = self.start(manual=False)
            if ok:
                self._restart_count += 1


# --------------------------------------------------------------------------- #
# Top-level Supervisor
# --------------------------------------------------------------------------- #


class Supervisor:
    def __init__(self):
        self.slots: Dict[str, ManagedSlot] = {}
        self._stop_event = threading.Event()
        os.makedirs(CONTROL_DIR, exist_ok=True)
        os.makedirs(LOG_DIR, exist_ok=True)

    def add(self, slot: ManagedSlot) -> None:
        self.slots[slot.name] = slot

    def _drain_control(self) -> None:
        if not os.path.isdir(CONTROL_DIR):
            return
        for entry in os.listdir(CONTROL_DIR):
            full = os.path.join(CONTROL_DIR, entry)
            if not os.path.isfile(full):
                continue
            base, dot, action = entry.rpartition(".")
            if not dot or action not in ("start", "stop", "restart"):
                try:
                    os.unlink(full)
                except OSError:
                    pass
                continue
            slot = self.slots.get(base)
            if slot is None:
                try:
                    os.unlink(full)
                except OSError:
                    pass
                continue
            ok, msg = slot.request(action)
            _self_log("control: %s.%s -> %s" % (base, action, msg))
            try:
                os.unlink(full)
            except OSError:
                pass

    def request(self, name: str, action: str) -> Tuple[bool, str]:
        slot = self.slots.get(name)
        if slot is None:
            return False, "unknown slot: %r" % name
        if action in ("start", "stop", "restart"):
            ok, msg = slot.request(action)
            return ok, msg
        return False, "unknown action: %r" % action

    def snapshot(self) -> Dict:
        return {
            "version": SUPERVISOR_VERSION,
            "updated_at": int(time.time()),
            "log_dir": LOG_DIR,
            "slots": {name: s.snapshot() for name, s in self.slots.items()},
        }

    def write_state(self) -> None:
        try:
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.snapshot(), fh, indent=1, sort_keys=True)
            os.replace(tmp, STATE_FILE)
        except OSError as exc:
            _self_log("failed to write state file: %s" % exc)

    def _heartbeat(self) -> None:
        try:
            with open(ALIVE_FILE, "w", encoding="utf-8") as fh:
                fh.write(str(int(time.time())))
        except OSError:
            pass

    def shutdown(self, signum: int, _frame=None) -> None:
        if self._stop_event.is_set():
            return
        _self_log("received signal %d - shutting down" % signum)
        self._stop_event.set()

    def run(self) -> int:
        # 1) Start the auto-managed slots.
        for name, slot in self.slots.items():
            if slot.enabled:
                ok, msg = slot.start(manual=False)
                _self_log("%s auto-start: %s" % (name, msg))
            else:
                _self_log("%s disabled by configuration" % name)
        # 2) Main loop. Fast tick for signals/crashes, slow per-slot
        # checks handled inside each slot.
        while not self._stop_event.is_set():
            self._heartbeat()
            self._drain_control()
            for slot in self.slots.values():
                slot.reap()
                slot.tick()
            self.write_state()
            self._stop_event.wait(timeout=TICK_SECONDS)
        # 3) Clean shutdown: stop every slot, then write a final state.
        _self_log("stopping all managed slots")
        for slot in self.slots.values():
            try:
                slot.stop(reason="supervisor shutdown", grace=slot.stop_grace)
            except Exception as exc:
                _self_log("error stopping %s: %s" % (slot.name, exc))
        self.write_state()
        _self_log("supervisor exited cleanly")
        return 0


# --------------------------------------------------------------------------- #
# Slot builders
# --------------------------------------------------------------------------- #


# Binaries the external-instance guard recognises. The 9Hits viewer is
# a Chromium-based binary whose process tree includes "may" (the
# upstream engine) and "nhviewer" (the launcher).
_NH_EXE_BASENAMES = ("may", "nhviewer", "chrome", "electron")


def _build_slots() -> List[ManagedSlot]:
    """Build the managed slots.

    The launchers / commands mirror the proven bash logic so existing
    behaviour is preserved. 9Hits still goes through /start.sh because
    that script does the Xvfb setup, the init pass and the wedge watchdog.
    """
    nh_enabled = _env_bool("NINEHITS_ENABLED", True)

    nh_cpu = _env_int("NH_CPU_SHARES", 0) or None
    nh_mem = _env_int("NH_MEM_LIMIT_MB", 0) or None

    slots: List[ManagedSlot] = []

    # --- ninehits ---------------------------------------------------------
    if nh_enabled:
        slots.append(ManagedSlot(
            name="ninehits",
            command=[NINEHITS_LAUNCHER_PATH, "ninehits-only"],
            enabled=True,
            log_file=os.path.join(LOG_DIR, "9hits.log"),
            env={"NINEHITS_ENABLED": "yes", "SUPERVISOR_MANAGED": "1"},
            stop_grace=20.0,
            cpu_shares=nh_cpu,
            mem_limit_mb=nh_mem,
            check_interval=NINEHITS_CHECK_INTERVAL,
            max_memory_mb=NINEHITS_MAX_MEMORY_MB,
            max_children=NINEHITS_MAX_CHILDREN,
            description=(
                "9Hits Viewer v6 (init+run passes, wedge watchdog, Xvfb :99)"
            ),
            exe_guard_basenames=_NH_EXE_BASENAMES,
        ))
    else:
        slots.append(ManagedSlot(
            name="ninehits",
            command=["/bin/true"],
            enabled=False,
            log_file=os.path.join(LOG_DIR, "9hits.log"),
            stop_grace=1.0,
            check_interval=NINEHITS_CHECK_INTERVAL,
            description="9Hits Viewer v6 (disabled by NINEHITS_ENABLED)",
        ))

    # --- health server ----------------------------------------------------
    slots.append(ManagedSlot(
        name="health",
        command=["python3", HEALTH_SERVER_PATH],
        enabled=True,
        log_file=os.path.join(LOG_DIR, "health.log"),
        env={"SUPERVISOR_MANAGED": "1", "SUPERVISOR_VERSION": SUPERVISOR_VERSION},
        stop_grace=5.0,
        check_interval=5,
        description="/health + dashboard HTTP server (port $PORT)",
    ))

    return slots


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main() -> int:
    # First: read all env-driven config so the rest of the supervisor
    # (slot constructors, _load_config_from_env consumers) see the
    # right values. Done at runtime, not at import time, so the
    # helper functions above are guaranteed to exist.
    _load_config_from_env()

    # Optional back-compat escape hatch: re-exec the legacy /start.sh
    # entrypoint if the operator really wants the old bash layout
    # (mostly useful for debugging).
    if os.environ.get("SUPERVISOR_DISABLED", "").lower() in ("1", "yes", "true", "on"):
        _self_log("SUPERVISOR_DISABLED set - re-execing /start.sh with the original layout")
        launcher = os.environ.get("NINEHITS_LAUNCHER_PATH", "/start.sh")
        os.execv(launcher, [launcher] + sys.argv[1:])

    _open_self_log(os.path.join(LOG_DIR, "supervisor.log"))
    sup = Supervisor()
    for slot in _build_slots():
        sup.add(slot)
    # All of the following must shut the container down cleanly:
    #   SIGTERM  - docker stop / docker kill, systemd stop, k8s pod stop
    #   SIGINT   - Ctrl-C in foreground
    #   SIGQUIT  - core-dump request, also used by some orchestrators
    #   SIGHUP   - terminal hangup, also a "reload" hint in some envs
    for sig_num in (signal.SIGTERM, signal.SIGINT, signal.SIGQUIT, signal.SIGHUP):
        try:
            signal.signal(sig_num, sup.shutdown)
        except (ValueError, OSError):
            pass
    _self_log(
        "starting (version=%s, log_dir=%s, slots=%s, tick=%.1fs)"
        % (SUPERVISOR_VERSION, LOG_DIR, ",".join(sup.slots.keys()), TICK_SECONDS)
    )
    return sup.run()


if __name__ == "__main__":
    sys.exit(main())
