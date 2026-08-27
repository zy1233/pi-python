#!/usr/bin/env python3
"""Smoke-test pi_agent_cli ACP stdio: send initialize, expect JSON-RPC response."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    python = os.environ.get("PI_PYTHON", sys.executable)
    env = os.environ.copy()
    env.setdefault("PI_HOME", str(Path.home() / ".pi-python"))
    env.setdefault("PI_USE_MOCK", "1")

    cmd = [python, "-m", "pi_agent_cli"]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=repo,
        env=env,
    )
    assert proc.stdin and proc.stdout
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": 1},
    }
    proc.stdin.write(json.dumps(req) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    proc.terminate()
    err = proc.stderr.read()
    if err:
        print("stderr:", err, file=sys.stderr)
    if not line:
        print("FAIL: no stdout line (agent exited?)", file=sys.stderr)
        return 1
    try:
        resp = json.loads(line)
    except json.JSONDecodeError as exc:
        print(f"FAIL: invalid JSON: {line!r} ({exc})", file=sys.stderr)
        return 1
    if resp.get("id") != 1 or "result" not in resp:
        print(f"FAIL: unexpected response: {resp}", file=sys.stderr)
        return 1
    print("OK:", json.dumps(resp["result"], ensure_ascii=False)[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
