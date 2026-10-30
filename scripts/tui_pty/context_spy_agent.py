"""The Python agent, with what its LLM call would be sent written down.

    WIRE_AGENT_SCRIPT=context_spy_agent.py  (through ``wire_log_agent.py``, as ``PI_AGENT_COMMAND``)
    SPY_LOG=/path/to/spy.jsonl

It is ``PiAcpAgent`` as ``python -m pi_agent_cli`` builds it (configuration from ``agent.toml`` in
``PI_HOME``), except that the stream function is wrapped: before each LLM call it appends one JSON
line to ``$SPY_LOG`` with the model, the reasoning level, the system prompt, the names of the tools
and the number of messages. The reply is the scripted test LLM's ("Hello from mock").

``PI_TEST_PERMISSION`` sets the permission mode (``ask`` by default). ``PI_TEST_TOOL_CALL`` (a JSON
object ``{"name": ..., "arguments": {...}}``, as in ``tests/_tool_agent.py``) makes each turn that
starts with a user message call that tool once; the turn after the result is the plain reply.
``flags_probe.py`` and ``settings_probe.py`` use it to find out what a flag or a setting changes
on the agent's side of the wire.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from dataclasses import replace
from pathlib import Path

from pi_agent_cli.__main__ import serve
from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import load_config, pi_home
from pi_agent_cli.factory import default_stream_fn
from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
from pi_agent_core.types import DoneEvent, StartEvent


def tool_names(tools: object) -> list[str]:
    names = []
    for tool in tools or []:  # type: ignore[attr-defined]
        name = tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None)
        names.append(str(name))
    return sorted(names)


def text_of(message: object) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts)


def record(model: object, context: object, options: object) -> None:
    path = os.environ.get("SPY_LOG")
    if not path:
        return
    messages = list(getattr(context, "messages", []) or [])
    entry = {
        "model": {
            "provider": getattr(model, "provider", None),
            "id": getattr(model, "model_id", None),
            "reasoning": getattr(model, "reasoning", None),
        },
        "reasoning": getattr(options, "reasoning", None),
        "system_prompt": getattr(context, "system_prompt", None),
        "tools": tool_names(getattr(context, "tools", None)),
        "messages": len(messages),
        "last_role": getattr(messages[-1], "role", None) if messages else None,
        "last_text": text_of(messages[-1])[:300] if messages else "",
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def tool_call_stream(model: object, call: dict) -> AssistantMessageEventStream:
    block = {
        "type": "toolCall",
        "id": call.get("id", "call_1"),
        "name": call["name"],
        "arguments": call["arguments"],
    }
    stream = AssistantMessageEventStream()
    partial = _base_partial(model, [block])
    partial.stopReason = "toolUse"
    stream.push(StartEvent(partial=partial.model_copy(deep=True)))
    stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
    stream.set_final_message(partial)
    stream.end()
    return stream


def make_stream_fn():  # type: ignore[no-untyped-def]
    inner = default_stream_fn()
    scripted = os.environ.get("PI_TEST_TOOL_CALL")
    call = json.loads(scripted) if scripted else None

    async def stream(model, context, options=None):  # type: ignore[no-untyped-def]
        record(model, context, options)
        messages = list(getattr(context, "messages", []) or [])
        if call is not None and messages and getattr(messages[-1], "role", None) == "user":
            return tool_call_stream(model, call)
        if call is not None:
            return await mock_text_stream(model, context, options)
        result = inner(model, context, options)
        return await result if inspect.isawaitable(result) else result

    return stream


async def amain() -> None:
    home = pi_home(None)
    config = load_config(home)
    config = replace(config, permission=os.environ.get("PI_TEST_PERMISSION", "ask"))  # type: ignore[arg-type]
    agent = PiAcpAgent(stream_fn=make_stream_fn(), home=Path(home), config=config)
    await serve(agent)


if __name__ == "__main__":
    asyncio.run(amain())
