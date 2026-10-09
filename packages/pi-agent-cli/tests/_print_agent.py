"""``python -m pi_agent_cli -p`` for the shutdown tests: its one LLM turn runs a long ``bash`` call.

The real ``main()`` of the one-shot mode runs (argument parsing, signal handling, the parent
watch), with the LLM replaced by a scripted turn. The tool command writes what it sees of
``PI_AGENT_PARENT_PID`` to ``PI_TEST_TOOL_ENVFILE``, then its own pid to ``PI_TEST_TOOL_PIDFILE``,
and ``exec``s ``sleep``, so the pid in that file is the pid of the tool process itself. This
process writes its own pid to ``PI_TEST_AGENT_PIDFILE``. Run it as ``python _print_agent.py`` with
``PI_HOME`` set; ``permission`` is forced to ``auto`` by ``-p``, so the tool call never waits.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pi_agent_cli.headless as headless
from pi_agent_cli.__main__ import main
from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.messages import ToolCallContent
from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
from pi_agent_core.types import DoneEvent, StartEvent

PIDFILE = os.environ["PI_TEST_TOOL_PIDFILE"]
ENVFILE = os.environ["PI_TEST_TOOL_ENVFILE"]
AGENT_PIDFILE = os.environ["PI_TEST_AGENT_PIDFILE"]


async def _long_bash_once_stream(model, context, options=None):
    if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
        return await mock_text_stream(model, context, options)
    stream = AssistantMessageEventStream()
    command = (
        f"echo ${{PI_AGENT_PARENT_PID-unset}} > {ENVFILE}; echo $$ > {PIDFILE}; exec sleep 300"
    )
    tc: ToolCallContent = {
        "type": "toolCall",
        "id": "call_bash",
        "name": "bash",
        "arguments": {"command": command},
    }
    partial = _base_partial(model, [tc])
    partial.stopReason = "toolUse"
    stream.push(StartEvent(partial=partial.model_copy(deep=True)))
    stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
    stream.set_final_message(partial)
    stream.end()
    return stream


if __name__ == "__main__":
    Path(AGENT_PIDFILE).write_text(str(os.getpid()))
    headless.default_stream_fn = lambda: _long_bash_once_stream
    sys.argv = ["pi-agent-cli", "-p", "run it", "--cwd", os.environ["PI_HOME"]]
    main()
