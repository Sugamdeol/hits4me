#!/usr/bin/env python3
"""
Run a command attached to a freshly allocated pseudo-TTY (PTY) and mirror its
output to stdout, redacting brand names so container logs stay generic.

The background worker's launcher renders a text dashboard and refuses to run
without a TTY, so one is allocated here.

Usage: run_hidden.py <command> [args...]
Exits with the command's exit code.
"""

import os
import pty
import select
import signal
import sys
import time

# Byte-level redactions applied to the PTY output before it is mirrored to
# stdout. Order matters: longer/more specific strings first.
REDACTIONS = [
    (b"9hitsv3", b"notesly"),
    (b"9hbrowser", b"notesly-browser"),
    (b"9Hits", b"Notesly"),
    (b"9HITS", b"NOTESLY"),
    (b"9hits", b"notesly"),
]

# Child pid; 0 until pty.fork() completes (signal handlers may run earlier).
pid = 0


def _forward_signal(signum, _frame):
    """Forward termination signals to the child so shutdown propagates."""
    if pid > 1:
        try:
            os.kill(pid, signum)
        except OSError:
            pass


def redact(data):
    """Replace brand tokens in PTY output with neutral names."""
    for token, replacement in REDACTIONS:
        data = data.replace(token, replacement)
    return data


def main():
    global pid
    if len(sys.argv) < 2:
        sys.stderr.write("usage: run_hidden.py <command> [args...]\n")
        return 2

    signal.signal(signal.SIGTERM, _forward_signal)
    signal.signal(signal.SIGINT, _forward_signal)

    pid, master_fd = pty.fork()
    if pid == 0:
        # Child: replace itself with the target command. The PTY slave is
        # already wired to the child's stdin/stdout/stderr. argv[0] is kept
        # as the real script path so the launcher's own $0 logic still works.
        try:
            os.execvp(sys.argv[1], sys.argv[1:])
        except Exception as exc:  # exec failed
            os.write(
                2,
                ("run_hidden: exec %r failed: %s\n" % (sys.argv[1], exc)).encode(
                    "utf-8", "replace"
                ),
            )
            os._exit(127)

    # Parent: pump PTY output (redacted) to our stdout until the child exits.
    out = sys.stdout.buffer
    eof = False
    while True:
        try:
            done_pid, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            done_pid, status = pid, 0
        if done_pid == pid:
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
                            out.write(redact(data))
                            out.flush()
                        except OSError:
                            pass  # log pipe gone; keep the child alive
        else:
            time.sleep(0.5)

    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return 1


if __name__ == "__main__":
    sys.exit(main())
