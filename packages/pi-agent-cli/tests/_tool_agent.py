"""Stdio ACP agent for the shutdown tests: its first LLM turn runs one long ``bash`` tool call.

The command records the shell pid in ``PI_TEST_TOOL_PIDFILE`` and then ``exec``s ``sleep``, so the
pid in that file is the pid of the tool process itself. ``permission = "auto"`` keeps the tool
call from waiting for the client. Run it as ``python _tool_agent.py`` with ``PI_HOME`` set.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from pi_agent_cli.__main__ import serve
from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig
from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.messages import ToolCallContent
from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
from pi_agent_core.types import DoneEvent, StartEvent

PIDFILE = os.environ["PI_TEST_TOOL_PIDFILE"]


async def _long_bash_once_stream(model, context, options=None):
    if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
        return await mock_text_stream(model, context, options)
    stream = AssistantMessageEventStream()
    tc: ToolCallContent = {
        "type": "toolCall",
        "id": "call_bash",
        "name": "bash",
        "arguments": {"command": f"echo $$ > {PIDFILE}; exec sleep 300"},
    }
    partial = _base_partial(model, [tc])
    partial.stopReason = "toolUse"
    stream.push(StartEvent(partial=partial.model_copy(deep=True)))
    stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
    stream.set_final_message(partial)
    stream.end()
    return stream


async def _amain() -> None:
    agent = PiAcpAgent(
        stream_fn=_long_bash_once_stream,
        home=Path(os.environ["PI_HOME"]),
        config=CliConfig(permission="auto", provider="mock", model_id="mock"),  # type: ignore[arg-type]
    )
    await serve(agent)  # the production stdio loop, signal handling included


if __name__ == "__main__":
    asyncio.run(_amain())
