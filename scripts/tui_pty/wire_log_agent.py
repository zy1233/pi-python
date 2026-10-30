"""A stand-in agent that records what zypi tells the Python agent, and what it answers.

    PI_AGENT_COMMAND="python wire_log_agent.py"   WIRE_LOG=/path/to/wire.jsonl

It starts ``python -m pi_agent_cli`` (with the interpreter that runs this file, and the arguments
and environment zypi gave it) and copies the newline-delimited JSON-RPC of ACP both ways,
untouched. Nothing changes for either side. ``WIRE_AGENT_SCRIPT=/path/agent.py`` starts that
script instead of the module (``context_spy_agent.py`` is one). One JSON object per line goes to
``$WIRE_LOG``:

- the first one is
  ``{"d": "start", "argv": [...], "env": {...}, "env_names": [...], "cwd": "..."}``.
  ``env`` holds the value of each variable whose name starts with ``PI_``, ``GROK_``, ``ZYPI_`` or
  ``ACP_`` and does not look like a secret; ``env_names`` is every name that is set;
- then ``{"d": "c2a" | "a2c", "t": seconds, "line": "<the raw line>"}`` for each line the client
  (zypi) sent to the agent and each line the agent sent back.

``flags_probe.py`` and ``settings_probe.py`` read the file: whatever the Python agent can act on
must be in it, because zypi gives the agent no other input (no argument, and no variable but
``PI_HOME``). Stdlib only.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import threading
import time

SECRET = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.IGNORECASE)
SHOWN = re.compile(r"^(PI_|GROK_|ZYPI_|ACP_)")


def start_record() -> dict:
    env = os.environ
    shown = {
        name: value[:200]
        for name, value in sorted(env.items())
        if SHOWN.match(name) and not SECRET.search(name)
    }
    return {
        "d": "start",
        "argv": sys.argv[1:],
        "env": shown,
        "env_names": sorted(env),
        "cwd": os.getcwd(),
    }


class Log:
    def __init__(self, path: str | None) -> None:
        self._fh = open(path, "a", encoding="utf-8") if path else None  # noqa: SIM115
        self._lock = threading.Lock()
        self._t0 = time.monotonic()

    def write(self, record: dict) -> None:
        if self._fh is None:
            return
        with self._lock:
            self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._fh.flush()

    def line(self, direction: str, raw: bytes) -> None:
        text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        self.write({"d": direction, "t": round(time.monotonic() - self._t0, 3), "line": text})


def agent_command() -> list[str]:
    script = os.environ.get("WIRE_AGENT_SCRIPT")
    base = [sys.executable, script] if script else [sys.executable, "-m", "pi_agent_cli"]
    return [*base, *sys.argv[1:]]


def main() -> int:
    log = Log(os.environ.get("WIRE_LOG"))
    log.write(start_record())
    child = subprocess.Popen(
        agent_command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=os.environ.copy(),
    )

    def forward_input() -> None:
        assert child.stdin is not None
        try:
            for raw in sys.stdin.buffer:
                log.line("c2a", raw)
                child.stdin.write(raw)
                child.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            with contextlib.suppress(OSError):
                child.stdin.close()

    threading.Thread(target=forward_input, daemon=True).start()
    out = sys.stdout.buffer
    assert child.stdout is not None
    for raw in child.stdout:
        log.line("a2c", raw)
        try:
            out.write(raw)
            out.flush()
        except (BrokenPipeError, OSError):
            break
    return child.wait()


if __name__ == "__main__":
    sys.exit(main())
