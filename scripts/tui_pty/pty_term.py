"""A small PTY driver for the zypi TUI: run a command under a pseudo-terminal, render what it draws
with pyte (a terminal emulator), send keystrokes, and look for processes it left behind.

Linux only: the process helpers read ``/proc``. ``Term(argv, env)`` makes ``argv`` the session
leader of the PTY (what ``pty.fork()`` gives you): when it dies the kernel hangs up the foreground
process group. To get the other situation - an interactive bash is the session leader and zypi is
one of its jobs (a person running ``zypi`` from a prompt: job control, its own process group, no
hang-up when the job dies) - start ``["bash", "--norc", "--noprofile", "-i"]`` and type the
command (see ``exit_matrix.py``).
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import pty
import re
import select
import signal
import struct
import subprocess
import termios
import time
from collections.abc import Callable

import pyte

QUERIES: list[tuple[bytes, Callable[[Term], bytes]]] = [
    # Cursor position report: answer with the emulated cursor.
    (b"\x1b[6n", lambda t: f"\x1b[{t.screen.cursor.y + 1};{t.screen.cursor.x + 1}R".encode()),
    # Primary / secondary device attributes.
    (b"\x1b[c", lambda t: b"\x1b[?62;c"),
    (b"\x1b[0c", lambda t: b"\x1b[?62;c"),
    (b"\x1b[>c", lambda t: b"\x1b[>1;10;0c"),
    # Foreground / background colour queries (OSC 10 / 11).
    (b"\x1b]11;?\x07", lambda t: b"\x1b]11;rgb:0000/0000/0000\x1b\\"),
    (b"\x1b]11;?\x1b\\", lambda t: b"\x1b]11;rgb:0000/0000/0000\x1b\\"),
    (b"\x1b]10;?\x07", lambda t: b"\x1b]10;rgb:ffff/ffff/ffff\x1b\\"),
    (b"\x1b]10;?\x1b\\", lambda t: b"\x1b]10;rgb:ffff/ffff/ffff\x1b\\"),
]


class Term:
    def __init__(
        self,
        argv: list[str],
        env: dict[str, str],
        *,
        cwd: str | None = None,
        rows: int = 40,
        cols: int = 120,
    ) -> None:
        self.rows, self.cols = rows, cols
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        self.raw = bytearray()
        self.eof = False
        self._tail = b""
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

        def become_leader() -> None:
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)  # the slave is fd 0 in the child

        self.proc = subprocess.Popen(
            argv,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            cwd=cwd,
            preexec_fn=become_leader,
            close_fds=True,
        )
        os.close(slave)
        self.master = master
        self.master_open = True

    # ---- output -------------------------------------------------------------------------
    def pump(self, seconds: float = 0.2) -> None:
        end = time.monotonic() + seconds
        while self.master_open:
            left = end - time.monotonic()
            if left <= 0:
                break
            ready, _, _ = select.select([self.master], [], [], min(left, 0.05))
            if not ready:
                continue
            try:
                data = os.read(self.master, 65536)
            except OSError:
                self.eof = True
                break
            if not data:
                self.eof = True
                break
            self.raw += data
            window = self._tail + data
            for query, answer in QUERIES:
                if query in window:
                    self.send(answer(self))
            self._tail = window[-16:]
            self.stream.feed(data)

    def text(self) -> str:
        return "\n".join(line.rstrip() for line in self.screen.display).rstrip()

    def wait_for(self, pattern: str | re.Pattern[str], timeout: float = 15.0) -> bool:
        regex = re.compile(pattern) if isinstance(pattern, str) else pattern
        end = time.monotonic() + timeout
        while True:
            self.pump(0.1)
            if regex.search(self.text()):
                return True
            if time.monotonic() >= end or (self.eof and self.proc.poll() is not None):
                return bool(regex.search(self.text()))

    # ---- input --------------------------------------------------------------------------
    def send(self, data: bytes | str) -> None:
        if not self.master_open:
            return
        if isinstance(data, str):
            data = data.encode()
        with contextlib.suppress(OSError):
            os.write(self.master, data)

    def type(self, text: str, delay: float = 0.02) -> None:
        for ch in text:
            self.send(ch)
            self.pump(delay)

    def resize(self, rows: int, cols: int) -> None:
        """The terminal window changes size: the program gets SIGWINCH and repaints."""
        self.rows, self.cols = rows, cols
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.screen.resize(rows, cols)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.proc.pid, signal.SIGWINCH)

    def close_master(self) -> None:
        """The terminal window goes away."""
        if self.master_open:
            os.close(self.master)
            self.master_open = False

    # ---- lifecycle ----------------------------------------------------------------------
    def wait_exit(self, timeout: float) -> int | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.pump(0.05)
            code = self.proc.poll()
            if code is not None:
                return code
            if not self.master_open:
                time.sleep(0.05)
        return self.proc.poll()

    def kill_group(self) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.proc.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(timeout=3)
        self.close_master()


# ---- /proc helpers ----------------------------------------------------------------------
def proc_state(pid: int) -> str | None:
    """Single-letter state of ``pid`` (``Z`` = zombie), or None when it does not exist."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            return fh.read().rsplit(b")", 1)[1].split()[0].decode()
    except (OSError, IndexError):
        return None


def alive(pid: int) -> bool:
    state = proc_state(pid)
    return state is not None and state != "Z"


def cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        return ""


def pgrp_sid(pid: int) -> tuple[int, int] | None:
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            fields = fh.read().rsplit(b")", 1)[1].split()
        # after the ")": state, ppid, pgrp, session, tty_nr, ...
        return int(fields[2]), int(fields[3])
    except (OSError, IndexError, ValueError):
        return None


def processes_with_env(marker: str) -> list[tuple[int, str, str]]:
    """(pid, state, cmdline) of every non-zombie process whose environment contains ``marker``."""
    needle = marker.encode()
    found = []
    me = os.getpid()
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == me:
            continue
        try:
            with open(f"/proc/{entry}/environ", "rb") as fh:
                if needle not in fh.read():
                    continue
        except OSError:
            continue
        state = proc_state(int(entry))
        if state is None or state == "Z":
            continue
        found.append((int(entry), state, cmdline(int(entry))))
    return sorted(found)


def kill_marked(marker: str) -> None:
    """SIGKILL whatever still carries ``marker``: what a run that went wrong left behind."""
    for pid, _state, _cmd in processes_with_env(marker):
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)


def children_of(pid: int) -> list[int]:
    out = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as fh:
                fields = fh.read().rsplit(b")", 1)[1].split()
            if int(fields[1]) == pid:
                out.append(int(entry))
        except (OSError, IndexError, ValueError):
            continue
    return out


def descendants(pid: int) -> list[int]:
    out, todo = [], [pid]
    while todo:
        for child in children_of(todo.pop()):
            out.append(child)
            todo.append(child)
    return out
