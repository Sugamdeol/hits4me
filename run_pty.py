#!/usr/bin/env python3
"""
Run a command attached to a freshly allocated pseudo-TTY (PTY) and mirror its
output to stdout.

The 9Hits viewer renders a text dashboard and refuses to run without a TTY.
Render (and `docker run -d`) do not allocate one, so we allocate a PTY here.

Usage: run_pty.py <command> [args...]
Exits with the command's exit code.

Importable API (used by run_native.py):
    run(argv, writer=None, pid_callback=None) -> exit code
"""

import os
import pty
import select
import signal
import sys
import threading
import time

# Child pid of the CLI invocation; 0 until pty.fork() completes (signal
# handlers may run earlier).
pid = 0


def _forward_signal(signum, _frame):
    """Forward termination signals to the child so shutdown propagates."""
    if pid > 1:
        try:
            os.kill(pid, signum)
        except OSError:
            pass


def run(argv, writer=None, pid_callback=None, forward_signals=True):
    """Run ``argv`` under a PTY, streaming its output to ``writer``.

    ``writer`` is any object with ``write(bytes)`` / ``flush()`` (default:
    ``sys.stdout.buffer``). ``pid_callback`` is called with the child pid right
    after the fork so callers can supervise/terminate it from another thread.
    Signal forwarding is only installed when running on the main thread
    (Python only allows signal handlers there).

    Returns the child's exit code (128 + signal number if it was killed).
    """
    global pid

    if not argv:
        raise ValueError("run() requires at least a command")

    if writer is None:
        writer = sys.stdout.buffer

    on_main_thread = threading.current_thread() is threading.main_thread()
    previous_handlers = {}
    if forward_signals and on_main_thread:
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                previous_handlers[signum] = signal.signal(signum, _forward_signal)
            except (ValueError, OSError):
                pass

    child_pid, master_fd = pty.fork()
    if child_pid == 0:
        # Child: replace itself with the target command. The PTY slave is
        # already wired to the child's stdin/stdout/stderr.
        try:
            os.execvp(argv[0], list(argv))
        except Exception as exc:  # exec failed
            os.write(
                2,
                ("run_pty: exec %r failed: %s\n" % (argv[0], exc)).encode(
                    "utf-8", "replace"
                ),
            )
            os._exit(127)

    pid = child_pid
    if pid_callback:
        try:
            pid_callback(child_pid)
        except Exception:
            pass

    # Parent: pump PTY output to the writer until the child exits.
    status = 0
    eof = False
    try:
        while True:
            try:
                done_pid, status = os.waitpid(child_pid, os.WNOHANG)
            except ChildProcessError:
                done_pid, status = child_pid, 0
            if done_pid == child_pid:
                break

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
                            try:
                                writer.write(data)
                                writer.flush()
                            except OSError:
                                pass  # log pipe gone; keep the child alive
            else:
                time.sleep(0.5)
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
        for signum, handler in previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):
                pass
        if pid == child_pid:
            pid = 0

    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return 1


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: run_pty.py <command> [args...]\n")
        return 2
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
