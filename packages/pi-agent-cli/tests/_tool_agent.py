"""Stdio ACP agent for the tool tests: its first LLM turn calls one tool.

By default the call is one long ``bash`` command, for the shutdown tests: it records the shell pid
in ``PI_TEST_TOOL_PIDFILE`` and then ``exec``s ``sleep``, so the pid in that file is the pid of the
tool process itself. With ``PI_TEST_STUCK_THREAD`` set the turn instead blocks in a thread that
cannot be cancelled (the file then holds the agent's pid), to test a shutdown that hangs.

``PI_TEST_TOOL_CALL`` replaces the call: a JSON object ``{"name": ..., "arguments": {...}}``, and
optionally ``"id"`` (default ``call_1``). The tool-call tests use it to make the agent call
``write``, which in ``ask`` mode has to be put to the client.

``PI_TEST_PERMISSION`` is the permission mode, ``auto`` by default so that the tool call does not
wait for the client; the tests that are about that wait set ``ask``. Whatever the call, the turn
after its result is the mock reply, unless the turn was cancelled in the meantime: the stream
then ends as ``aborted``, as a real provider's does. Run it as ``python _tool_agent.py`` with
``PI_HOME`` set.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from pi_agent_cli.__main__ import serve
from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig
from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.messages import ToolCallContent
from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
from pi_agent_core.types import DoneEvent, ErrorEvent, StartEvent


def _tool_call() -> ToolCallContent:
    scripted = os.environ.get("PI_TEST_TOOL_CALL")
    if scripted:
        spec = json.loads(scripted)
        return {
            "type": "toolCall",
            "id": spec.get("id", "call_1"),
            "name": spec["name"],
            "arguments": spec["arguments"],
        }
    pidfile = os.environ["PI_TEST_TOOL_PIDFILE"]
    return {
        "type": "toolCall",
        "id": "call_bash",
        "name": "bash",
        "arguments": {"command": f"echo $$ > {pidfile}; exec sleep 300"},
    }


def _aborted_stream(model) -> AssistantMessageEventStream:
    """What the LangChain adapter returns when the signal is set before it sends the request."""
    stream = AssistantMessageEventStream()
    partial = _base_partial(model)
    partial.stopReason = "aborted"
    partial.errorMessage = "Operation aborted"
    stream.push(
        ErrorEvent(
            partial=partial.model_copy(deep=True),
            reason="aborted",
            error_message="Operation aborted",
        )
    )
    stream.set_final_message(partial)
    stream.end()
    return stream


async def _tool_once_stream(model, context, options=None):
    # Like a real provider's stream: a cancelled turn does not go on to another LLM call.
    signal = getattr(options, "signal", None)
    if signal is not None and getattr(signal, "aborted", False):
        return _aborted_stream(model)
    if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
        return await mock_text_stream(model, context, options)
    if os.environ.get("PI_TEST_STUCK_THREAD"):
        # A blocking call that cannot be cancelled: `asyncio.run` waits for it at shutdown, so
        # the agent hangs there. The pidfile holds the agent's own pid in this mode.
        Path(os.environ["PI_TEST_TOOL_PIDFILE"]).write_text(str(os.getpid()))
        await asyncio.to_thread(time.sleep, 120)
    stream = AssistantMessageEventStream()
    partial = _base_partial(model, [_tool_call()])
    partial.stopReason = "toolUse"
    stream.push(StartEvent(partial=partial.model_copy(deep=True)))
    stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
    stream.set_final_message(partial)
    stream.end()
    return stream


async def _amain() -> None:
    agent = PiAcpAgent(
        stream_fn=_tool_once_stream,
        home=Path(os.environ["PI_HOME"]),
        config=CliConfig(  # type: ignore[arg-type]
            permission=os.environ.get("PI_TEST_PERMISSION", "auto"),
            provider="mock",
            model_id="mock",
        ),
    )
    await serve(agent)  # the production stdio loop, signal handling included


if __name__ == "__main__":
    asyncio.run(_amain())
