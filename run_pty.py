#!/usr/bin/env python3
"""
Run a command attached to a freshly allocated pseudo-TTY (PTY) and mirror its
output to stdout.

The 9Hits v6 viewer (nhviewer --render-to-terminal) draws a live text
dashboard and refuses to run without a TTY. Render (and `docker run -d`) do
not allocate one, so we allocate a PTY here. While babysitting the viewer we
also:

  * Give the PTY a sane window size. A pty.fork() terminal defaults to 0x0,
    which is known to break (or hang) terminal UI renderers.
  * Maintain a heartbeat file whose mtime is the last time the child produced
    output (/tmp/viewer.lastoutput). The health endpoint reports this so a
    silently-wedged viewer is visible from the outside.
  * Watchdog: if the child is silent for --watchdog-stuck seconds AND makes
    zero CPU progress (jiffies) over a further --watchdog-confirm window, the
    process tree is deadlocked - kill it so the supervisor can restart it.
    Silence alone is NOT treated as fatal: an idle-but-healthy viewer keeps
    burning a little CPU; a truly wedged one burns none.

Usage:
    run_pty.py [--heartbeat-file PATH] [--watchdog-stuck SECONDS]
               [--watchdog-confirm SECONDS] [--cols N] [--rows N]
               [--] <command> [args...]

Exits with the command's exit code.
"""

import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import time

# Child pid; 0 until pty.fork() completes (signal handlers may run earlier).
pid = 0


def _forward_signal(signum, _frame):
    """Forward termination signals to the child so shutdown propagates."""
    if pid > 1:
        try:
            os.killpg(pid, signum)
        except OSError:
            try:
                os.kill(pid, signum)
            except OSError:
                pass


def _parse_args(argv):
    opts = {
        "heartbeat_file": None,
        "watchdog_stuck": _env_int("RUN_PTY_WATCHDOG_STUCK", 600),
        "watchdog_confirm": _env_int("RUN_PTY_WATCHDOG_CONFIRM", 120),
        "cols": _env_int("PTY_COLS", 120),
        "rows": _env_int("PTY_ROWS", 30),
    }
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            i += 1
            break
        if arg.startswith("--heartbeat-file="):
            opts["heartbeat_file"] = arg.split("=", 1)[1]
        elif arg == "--heartbeat-file" and i + 1 < len(argv):
            opts["heartbeat_file"] = argv[i + 1]
            i += 1
        elif arg.startswith("--watchdog-stuck="):
            opts["watchdog_stuck"] = _to_int(arg.split("=", 1)[1], 600)
        elif arg == "--watchdog-stuck" and i + 1 < len(argv):
            opts["watchdog_stuck"] = _to_int(argv[i + 1], 600)
            i += 1
        elif arg.startswith("--watchdog-confirm="):
            opts["watchdog_confirm"] = _to_int(arg.split("=", 1)[1], 120)
        elif arg == "--watchdog-confirm" and i + 1 < len(argv):
            opts["watchdog_confirm"] = _to_int(argv[i + 1], 120)
            i += 1
        elif arg.startswith("--cols="):
            opts["cols"] = _to_int(arg.split("=", 1)[1], 120)
        elif arg == "--cols" and i + 1 < len(argv):
            opts["cols"] = _to_int(argv[i + 1], 120)
            i += 1
        elif arg.startswith("--rows="):
            opts["rows"] = _to_int(arg.split("=", 1)[1], 30)
        elif arg == "--rows" and i + 1 < len(argv):
            opts["rows"] = _to_int(argv[i + 1], 30)
            i += 1
        else:
            break  # first non-option = start of the command
        i += 1
    return opts, argv[i:]


def _to_int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _env_int(name, default):
    return _to_int(os.environ.get(name, ""), default)


def _touch(path):
    """Update the heartbeat file mtime (create it if needed)."""
    if not path:
        return
    try:
        os.utime(path, None)
    except OSError:
        try:
            with open(path, "a", encoding="utf-8"):
                pass
            os.utime(path, None)
        except OSError:
            pass


def _tree_cpu_jiffies(root):
    """Sum utime+stime (jiffies) of `root` and all its descendants.

    Returns 0 if the process tree is gone/unreadable.
    """
    if root <= 1:
        return 0
    ppid_map = {}
    cpu_map = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return 0
    for entry in entries:
        if not entry.isdigit():
            continue
        p = int(entry)
        try:
            with open("/proc/%d/stat" % p, "rb") as fh:
                data = fh.read()
            rparen = data.rfind(b")")  # comm may contain spaces/parens
            if rparen < 0:
                continue
            rest = data[rparen + 2:].split()
            ppid = int(rest[1])    # stat field 4
            utime = int(rest[11])  # stat field 14
            stime = int(rest[12])  # stat field 15
            ppid_map.setdefault(ppid, []).append(p)
            cpu_map[p] = utime + stime
        except (OSError, ValueError, IndexError):
            continue
    total = 0
    stack = [root]
    seen = set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        total += cpu_map.get(current, 0)
        stack.extend(ppid_map.get(current, ()))
    return total


def _kill_tree(signum):
    """Signal the child's whole process group (pty.fork child = session leader)."""
    if pid > 1:
        try:
            os.killpg(pid, signum)
            return
        except OSError:
            pass
        try:
            os.kill(pid, signum)
        except OSError:
            pass


def main():
    global pid
    opts, cmd = _parse_args(sys.argv[1:])
    if not cmd:
        sys.stderr.write(
            "usage: run_pty.py [--heartbeat-file PATH] [--watchdog-stuck SEC]\n"
            "                  [--watchdog-confirm SEC] [--cols N] [--rows N]\n"
            "                  [--] <command> [args...]\n"
        )
        return 2

    signal.signal(signal.SIGTERM, _forward_signal)
    signal.signal(signal.SIGINT, _forward_signal)

    rows = max(1, opts["rows"])
    cols = max(1, opts["cols"])
    stuck_limit = max(0, opts["watchdog_stuck"])
    confirm_window = max(15, opts["watchdog_confirm"])
    heartbeat = opts["heartbeat_file"]

    pid, master_fd = pty.fork()
    if pid == 0:
        # Child: the PTY slave is already wired to our stdin/stdout/stderr.
        # Give the terminal a real size before the renderer looks at it.
        try:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except Exception:
            pass
        try:
            os.execvp(cmd[0], cmd)
        except Exception as exc:  # exec failed
            os.write(
                2,
                ("run_pty: exec %r failed: %s\n" % (cmd[0], exc)).encode(
                    "utf-8", "replace"
                ),
            )
            os._exit(127)

    # Parent: pump PTY output to our stdout until the child exits.
    _touch(heartbeat)
    last_output = time.monotonic()
    stuck_since = None
    cpu_mark = 0
    out = sys.stdout.buffer
    eof = False
    while True:
        try:
            done_pid, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            done_pid, status = pid, 0
        if done_pid == pid:
            break

        now = time.monotonic()
        if not eof:
            try:
                readable, _, _ = select.select([master_fd], [], [], 1.0)
            except (OSError, ValueError):
                readable = []
            if readable:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    eof = True
                else:
                    if not data:
                        eof = True  # child closed the PTY; just wait for it
                    else:
                        last_output = now
                        stuck_since = None
                        _touch(heartbeat)
                        try:
                            out.write(data)
                            out.flush()
                        except OSError:
                            pass  # log pipe gone; keep the child alive
        else:
            time.sleep(0.5)

        # ------------------------------------------------ wedge watchdog
        if stuck_limit > 0:
            silent = now - last_output
            if silent <= stuck_limit:
                stuck_since = None
            elif stuck_since is None:
                stuck_since = now
                cpu_mark = _tree_cpu_jiffies(pid)
            elif now - stuck_since >= confirm_window:
                if _tree_cpu_jiffies(pid) <= cpu_mark:
                    sys.stderr.write(
                        "[run_pty] WATCHDOG: no output for %ds and no CPU "
                        "progress for %ds - the viewer is wedged, killing it "
                        "so the supervisor can restart it\n" % (int(silent), confirm_window)
                    )
                    sys.stderr.flush()
                    _kill_tree(signal.SIGTERM)
                    reaped = False
                    for _ in range(20):  # up to ~10s for a graceful exit
                        try:
                            chk, status = os.waitpid(pid, os.WNOHANG)
                        except ChildProcessError:
                            chk, status = pid, 0
                        if chk == pid:
                            reaped = True
                            break
                        time.sleep(0.5)
                    if not reaped:
                        _kill_tree(signal.SIGKILL)
                        continue  # normal waitpid at the top reaps it
                    break
                # It burned CPU while quiet -> busy, not wedged. Keep waiting.
                stuck_since = None

    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return 1


if __name__ == "__main__":
    sys.exit(main())
