"""Smoke test of an agent program over ACP, with a fake model server on this machine.

    python packaging/agent/smoke.py [--frozen] PROGRAM [ARGS...]

Starts a small OpenAI-format server on 127.0.0.1 and the agent with ``provider = "openai"``
pointing at it, then plays one turn over ACP stdio:

1. The model asks for the ``workflow`` tool with a script that calls ``agent()`` once. That
   runs the script in the sandbox's script process (the frozen one, in a frozen build), and a
   sub-agent that asks the model again.
2. On Linux the model also asks for ``bash``, to show that a tool the agent starts does not
   load the bundle's libraries (``LD_LIBRARY_PATH``).
3. The model answers in text, and the turn ends.

With ``--frozen`` it then starts the agent with a provider that the build does not hold
(``anthropic``) and checks that the turn fails with the advice to use ``provider = "openai"``
and ``base_url``.

The test needs only the standard library and ``acp`` (a dependency of the agent), and it makes
no connection to a non-local address. Exit status 0: all checks passed.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

from acp import PROTOCOL_VERSION, spawn_agent_process, text_block
from acp.schema import ClientCapabilities

SCRIPT = """
async def main():
    answer = await agent("SUB-PROMPT")
    result({"answer": answer})
"""
TURN_TIMEOUT = 180


def _chunk(delta: dict, finish: str | None = None) -> bytes:
    body = {
        "id": "chatcmpl-smoke",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "smoke-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return b"data: " + json.dumps(body).encode() + b"\n\n"


def _usage_chunk() -> bytes:
    body = {
        "id": "chatcmpl-smoke",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "smoke-model",
        "choices": [],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }
    return b"data: " + json.dumps(body).encode() + b"\n\n"


def _text(content: object) -> str:
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content if isinstance(content, str) else ""


class FakeModel(BaseHTTPRequestHandler):
    """What the model says depends on the request: the marker of the first turn, a tool result."""

    protocol_version = "HTTP/1.0"
    requests: ClassVar[list[str]] = []

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        messages = body.get("messages", [])
        tool_results = [_text(m.get("content")) for m in messages if m.get("role") == "tool"]
        users = [_text(m.get("content")) for m in messages if m.get("role") == "user"]
        if tool_results:
            kind, parts = "final", [_chunk({"role": "assistant", "content": "FINAL"})]
            parts.append(_chunk({"content": " " + " | ".join(tool_results)}))
            parts.append(_chunk({}, "stop"))
        elif users and "RUN-SMOKE" in users[-1]:
            kind = "tools"
            calls = [("call_workflow", "workflow", {"script": SCRIPT})]
            if sys.platform.startswith("linux"):
                command = 'echo "ld=[$LD_LIBRARY_PATH]"; git --version'
                calls.append(("call_bash", "bash", {"command": command}))
            tool_calls = [
                {
                    "index": i,
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
                for i, (call_id, name, arguments) in enumerate(calls)
            ]
            parts = [
                _chunk({"role": "assistant", "content": None, "tool_calls": tool_calls}),
                _chunk({}, "tool_calls"),
            ]
        else:
            kind = "sub-agent"
            parts = [_chunk({"role": "assistant", "content": "SUB-ANSWER"}), _chunk({}, "stop")]
        type(self).requests.append(kind)
        parts.append(_usage_chunk())
        parts.append(b"data: [DONE]\n\n")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for part in parts:
            self.wfile.write(part)
        self.wfile.flush()


class Client:
    def __init__(self) -> None:
        self.text: list[str] = []
        self.tool_output: list[str] = []

    async def session_update(self, session_id: str, update: object, **kwargs: object) -> None:
        kind = getattr(update, "session_update", None)
        content = getattr(update, "content", None)
        if kind == "agent_message_chunk" and getattr(content, "text", None):
            self.text.append(content.text)
        elif kind in ("tool_call", "tool_call_update"):
            for item in getattr(update, "content", None) or []:
                inner = getattr(item, "content", None)
                if getattr(inner, "text", None):
                    self.tool_output.append(inner.text)

    async def request_permission(self, **kwargs: object) -> object:
        raise NotImplementedError


async def play(
    command: list[str], home: Path, port: int, provider: str = "openai"
) -> tuple[Client, float]:
    (home / "agent.toml").write_text(
        'permission = "auto"\n\n'
        "[model]\n"
        f'provider = "{provider}"\n'
        'id = "smoke-model"\n'
        f'base_url = "http://127.0.0.1:{port}/v1"\n'
        'api_key_env = "SMOKE_API_KEY"\n',
        encoding="utf-8",
    )
    work = home / "project"
    work.mkdir()
    env = {**os.environ, "PI_HOME": str(home), "SMOKE_API_KEY": "not-a-key"}
    env.pop("PI_USE_MOCK", None)
    client = Client()
    program, *args = command
    async with spawn_agent_process(
        client,
        program,
        *args,
        env=env,
        cwd=str(work),
        transport_kwargs={"stderr": subprocess.DEVNULL, "shutdown_timeout": 15.0},
    ) as (conn, _process):
        started = time.perf_counter()
        init = await asyncio.wait_for(
            conn.initialize(
                protocol_version=PROTOCOL_VERSION, client_capabilities=ClientCapabilities()
            ),
            TURN_TIMEOUT,
        )
        initialize_seconds = time.perf_counter() - started
        print(f"agent: {init.agent_info.name} {init.agent_info.version}")
        session = await asyncio.wait_for(conn.new_session(cwd=str(work), mcp_servers=[]), 60)
        await asyncio.wait_for(
            conn.prompt(prompt=[text_block("RUN-SMOKE")], session_id=session.session_id),
            TURN_TIMEOUT,
        )
    return client, initialize_seconds


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    frozen = sys.argv[1] == "--frozen"
    command = sys.argv[2:] if frozen else sys.argv[1:]
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    with tempfile.TemporaryDirectory(prefix="agent-smoke-") as tmp:
        client, initialize_seconds = asyncio.run(play(command, Path(tmp), port))

    said = "".join(client.text)
    tools = "\n".join(client.tool_output)
    print(f"initialize: {initialize_seconds:.2f} s")
    print(f"model requests: {FakeModel.requests}")
    print(f"reply: {said[:300]!r}")

    failures: list[str] = []
    if "SUB-ANSWER" not in said:
        failures.append("the workflow script did not run, or its sub-agent got no answer")
    if "FINAL" not in said:
        failures.append("the turn did not end with the model's last answer")
    if FakeModel.requests.count("sub-agent") < 1:
        failures.append("no request came from the sub-agent")
    if sys.platform.startswith("linux"):
        if "git version" not in said:
            failures.append("the bash tool did not run git")
        expected = f"ld=[{os.environ.get('LD_LIBRARY_PATH', '')}]"
        if expected not in said:
            failures.append(
                f"a tool started by the agent does not see the LD_LIBRARY_PATH that the agent "
                f"was started with (expected {expected})"
            )
    if frozen:
        with tempfile.TemporaryDirectory(prefix="agent-smoke-") as tmp:
            other, _ = asyncio.run(play(command, Path(tmp), port, provider="anthropic"))
        advice = "".join(other.text) + "\n".join(other.tool_output)
        print(f"unbundled provider: {advice[:300]!r}")
        if 'provider = "openai"' not in advice or "base_url" not in advice:
            failures.append("a provider that the build lacks did not say to use provider openai")
    server.shutdown()
    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        print(f"tool output seen: {tools[:1500]!r}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
