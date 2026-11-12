"""Runs a program in a pseudo-terminal and types answers when its prompts show up.

    python pty_answers.py PROMPT=ANSWER ... -- PROGRAM [ARGS...]

Used to run ``--setup`` as a user does. A prompt is a piece of the text that the program prints
before it waits. The answers go in order. The exit status is the one of the program (124 after
a timeout of 60 seconds, 125 when a prompt did not show up). The terminal output goes to stdout
with the answers left out: a hidden answer (a key) is never echoed by the program.
"""

from __future__ import annotations

import os
import pty
import select
import signal
import sys
import time

TIMEOUT = 60.0


def main() -> int:
    split = sys.argv.index("--")
    steps = [item.split("=", 1) for item in sys.argv[1:split]]
    command = sys.argv[split + 1 :]
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(command[0], command)
    seen = ""
    out = ""
    deadline = time.time() + TIMEOUT
    pending = list(steps)
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if ready:
            try:
                data = os.read(fd, 4096)
            except OSError:
                data = b""
            if not data:
                break
            text = data.decode(errors="replace")
            seen += text
            out += text
        if pending and pending[0][0] in seen:
            os.write(fd, (pending[0][1] + "\n").encode())
            seen = ""
            pending.pop(0)
    else:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        sys.stdout.write(out)
        return 124
    _, raw = os.waitpid(pid, 0)
    sys.stdout.write(out)
    if pending:
        print(f"\nprompt not seen: {pending[0][0]!r}", file=sys.stderr)
        return 125
    return os.waitstatus_to_exitcode(raw)


if __name__ == "__main__":
    sys.exit(main())
