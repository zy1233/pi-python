"""A stand-in agent for status_line_probe.py: the Python agent, plus the update it never sends.

    PI_AGENT_COMMAND="python status_proxy_agent.py"   (zypi starts it like any agent)

It starts ``python -m pi_agent_cli`` (with the interpreter that runs this file, and the
arguments and environment zypi gave it) and copies the newline-delimited JSON-RPC of ACP both
ways, untouched. After each response that carries a ``sessionId`` (the reply to ``session/new``)
or a ``stopReason`` (the end of a turn) it adds one notification: the ACP extension method
``pi/session_notification`` (``_pi/session_notification`` on the wire) with
``sessionUpdate = "session_status"``. That is the status-line message of the old Rust agent, in
the one carrier the pager still reads: it drops the ``x.ai/`` namespace that agent used. Nothing
else changes, so what the pager does with the message is the only difference from a run against
the plain agent. Stdlib only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterable, Iterator

MODEL = "proxy-model"


def session_status(session_id: str, cwd: str) -> dict:
    """The notification: a ``StatusLineContext`` (pi-status-line) in a ``session_status`` update."""
    return {
        "jsonrpc": "2.0",
        "method": "_pi/session_notification",
        "params": {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": "session_status",
                "schema_version": 1,
                "cwd": cwd,
                "session_id": session_id,
                "model": {"id": MODEL, "display_name": MODEL},
                "workspace": {"current_dir": cwd},
                "version": "0.0.0",
                "cost": {"total_duration_ms": 1000},
                "context_window": {
                    "context_window_size": 1000,
                    "context_tokens": 250,
                    "used_percentage": 25,
                    "remaining_percentage": 75,
                },
            },
        },
    }


def trigger(line: bytes, last_session: str | None) -> str | None:
    """The session id to report a status for after this line of agent output, or None."""
    try:
        message = json.loads(line)
    except ValueError:
        return None
    result = message.get("result") if isinstance(message, dict) else None
    if not isinstance(result, dict):
        return None
    if isinstance(result.get("sessionId"), str):
        return result["sessionId"]
    if "stopReason" in result:
        return last_session
    return None


def with_status(lines: Iterable[bytes], cwd: str) -> Iterator[bytes]:
    """The agent's output lines, each followed by a status notification where one is due."""
    last_session = None
    for line in lines:
        yield line
        session = trigger(line, last_session)
        if session is not None:
            last_session = session
            yield (json.dumps(session_status(session, cwd)) + "\n").encode()


def main() -> int:
    child = subprocess.Popen(
        [sys.executable, "-m", "pi_agent_cli", *sys.argv[1:]],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=os.environ.copy(),
    )

    def forward_input() -> None:
        for line in sys.stdin.buffer:
            child.stdin.write(line)
            child.stdin.flush()
        child.stdin.close()

    threading.Thread(target=forward_input, daemon=True).start()
    out = sys.stdout.buffer
    for chunk in with_status(child.stdout, os.getcwd()):
        out.write(chunk)
        out.flush()
    return child.wait()


if __name__ == "__main__":
    sys.exit(main())
