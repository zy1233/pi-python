"""CLI <-> extension wiring: system-prompt contributions and ``tool_call`` hook ordering.

Both behaviours cross the ``create_session_harness`` boundary (the CLI builds the prompt
and registers the permission layer; extensions load later, on the first ``prompt()``),
so they are covered here end to end rather than in the harness package alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from pi_agent_cli.config import CliConfig
from pi_agent_cli.factory import create_session_harness
from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.extensions import ToolDefinition
from pi_agent_core.messages import AssistantMessage, Usage
from pi_agent_core.tests.mock_stream import mock_text_stream, mock_tool_stream
from pi_agent_core.types import AgentToolResult, DoneEvent, StartEvent
from pi_agent_harness import JsonlSessionRepo


class _EchoParams(BaseModel):
    message: str = ""


def _echo_definition(ran: list[str], **extra: Any) -> ToolDefinition:
    async def execute(tool_call_id, params, signal, on_update):
        ran.append(tool_call_id)
        return AgentToolResult(content=[{"type": "text", "text": "echoed"}], details={})

    return ToolDefinition(
        name="echo",
        description="Echo a message back",
        parameters=_EchoParams,
        execute=execute,
        **extra,
    )


async def _make_harness(
    tmp_path: Path, *, stream_fn, extensions=(), on_tool_call=None, config: CliConfig | None = None
):
    repo = JsonlSessionRepo(tmp_path / "sessions")
    session = await repo.create({"cwd": str(tmp_path)})
    harness = await create_session_harness(
        session=session,
        cwd=tmp_path,
        config=config or CliConfig(),
        stream_fn=stream_fn,
        home=tmp_path,
        on_tool_call=on_tool_call,
        extensions=list(extensions),
    )
    return harness, session


async def _tool_call_stream(model, name: str, arguments: dict[str, Any]):
    """A one-shot assistant turn that calls tool *name* with *arguments*."""
    stream = AssistantMessageEventStream()
    partial = AssistantMessage(
        content=[{"type": "toolCall", "id": "call_1", "name": name, "arguments": arguments}],
        api=model.api,
        provider=model.provider,
        model=model.model_id,
        usage=Usage(),
        stopReason="toolUse",
    )
    stream.push(StartEvent(partial=partial.model_copy(deep=True)))
    stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
    stream.set_final_message(partial)
    stream.end()
    return stream


async def _tool_results(session) -> list[Any]:
    context = await session.build_context()
    return [m for m in context.messages if getattr(m, "role", None) == "toolResult"]


@pytest.mark.asyncio
async def test_extension_tool_prompt_contributions_reach_the_system_prompt(tmp_path: Path):
    """Extension tools exist only after ``load_extensions``; their snippets and guidelines
    must still be looked up when the system prompt is assembled."""
    captured: list[Any] = []

    async def capture(model, context, options=None):
        captured.append(context)
        return await mock_text_stream(model, context, options)

    def extension(pi):
        pi.register_tool(
            _echo_definition(
                [],
                prompt_snippet="Echo text back to the user (extension_snippet_marker)",
                prompt_guidelines=["extension_guideline_marker: only echo when asked."],
            )
        )

    harness, _ = await _make_harness(tmp_path, stream_fn=capture, extensions=[extension])
    await harness.prompt("hi")

    context = captured[-1]
    assert "echo" in {tool.name for tool in context.tools}  # the tool itself was always sent
    assert "- echo: Echo text back to the user (extension_snippet_marker)" in context.system_prompt
    assert "extension_guideline_marker: only echo when asked." in context.system_prompt


@pytest.mark.asyncio
async def test_extension_tool_without_prompt_contributions_adds_nothing_to_the_prompt(
    tmp_path: Path,
):
    captured: list[Any] = []

    async def capture(model, context, options=None):
        captured.append(context)
        return await mock_text_stream(model, context, options)

    def extension(pi):
        pi.register_tool(_echo_definition([]))

    harness, _ = await _make_harness(tmp_path, stream_fn=capture, extensions=[extension])
    await harness.prompt("hi")

    assert "- echo:" not in captured[-1].system_prompt


@pytest.mark.asyncio
async def test_extension_tool_runs_when_the_permission_layer_allows_it(tmp_path: Path):
    """Control for the denial tests below: proves the tool really executes when allowed."""
    ran: list[str] = []

    def extension(pi):
        pi.register_tool(_echo_definition(ran))
        pi.on("tool_call", lambda _event: {})

    harness, session = await _make_harness(
        tmp_path,
        stream_fn=mock_tool_stream,
        extensions=[extension],
        on_tool_call=lambda _event: None,
    )
    await harness.prompt("call the tool")

    assert ran == ["call_1"]
    (result,) = await _tool_results(session)
    assert result.isError is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extension_verdict",
    [None, {}, {"block": False}],
    ids=["none", "empty-dict", "block-false"],
)
async def test_extension_tool_call_hook_cannot_undo_a_permission_denial(
    tmp_path: Path, extension_verdict: Any
):
    """The permission layer registers first, extensions after; last-non-None dispatch let
    any extension overturn a user's "Reject" by returning ``{}``."""
    ran: list[str] = []

    async def permission_layer(_event):  # what the ACP agent returns on "Reject"
        return {"block": True, "reason": "User denied permission"}

    def extension(pi):
        pi.register_tool(_echo_definition(ran))
        pi.on("tool_call", lambda _event: extension_verdict)

    harness, session = await _make_harness(
        tmp_path,
        stream_fn=mock_tool_stream,
        extensions=[extension],
        on_tool_call=permission_layer,
    )
    await harness.prompt("call the tool")

    assert ran == []
    (result,) = await _tool_results(session)
    assert result.isError is True
    assert "User denied permission" in result.content[0]["text"]


@pytest.mark.asyncio
async def test_workflow_subagents_stay_on_the_parent_provider_and_get_no_foreign_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Audit P7-03, end to end: CLI wiring -> ``workflow`` tool -> sub-agent stream.

    A ``tier`` used to resolve to an Anthropic model whatever the parent ran, and the
    one configured key was handed to every provider, so a DeepSeek session sent its
    DeepSeek key to Anthropic. Now a tier stays on the parent's provider (and key), and
    an explicit cross-provider model gets no key from the parent (its SDK uses its own).
    """
    pytest.importorskip("pi_dynamic_workflows")
    # Journals follow the session's home (``tmp_path``, which ``_make_harness`` passes). A
    # stand-in user home keeps a regression off the real one, and is asserted untouched.
    fake_user = tmp_path / "fake-user"
    monkeypatch.delenv("PI_HOME", raising=False)
    monkeypatch.setenv("HOME", str(fake_user))
    monkeypatch.setenv("USERPROFILE", str(fake_user))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_user))
    monkeypatch.setenv("PI_WIRING_KEY", "deepseek-secret")

    script = (
        'meta = {"name": "wiring"}\n'
        "async def main():\n"
        '    await agent("first", tier="small")\n'
        '    await agent("second", model="anthropic/claude-x")\n'
        '    result("done")\n'
    )
    subagent_calls: list[tuple[str, str, str | None]] = []

    async def stream(model, context, options=None):
        if "workflow" not in {tool.name for tool in context.tools or []}:
            # Only the parent agent is given the workflow tool: this is a sub-agent.
            subagent_calls.append((model.provider, model.model_id, options.api_key))
            return await mock_text_stream(model, context, options)
        if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
            return await mock_text_stream(model, context, options)  # parent, after the tool
        return await _tool_call_stream(model, "workflow", {"script": script})

    harness, session = await _make_harness(
        tmp_path,
        stream_fn=stream,
        config=CliConfig(
            provider="deepseek", model_id="deepseek-chat", api_key_env="PI_WIRING_KEY"
        ),
    )
    await harness.prompt("run the workflow")

    (result,) = await _tool_results(session)
    assert result.isError is False, result.content
    assert subagent_calls == [
        ("deepseek", "deepseek-chat", "deepseek-secret"),
        ("anthropic", "claude-x", None),
    ]
    journals = list((tmp_path / "workflow-journals").glob("*.jsonl"))
    assert len(journals) == 1  # written under the session's home ...
    assert not fake_user.exists()  # ... and nothing went to the user's own


@pytest.mark.asyncio
async def test_goal_guidelines_do_not_claim_goal_mode_before_a_goal_is_started(tmp_path: Path):
    """Rendering extension guidelines (P7-04) puts pi-goal-x's text into every session.

    The goal tools are always registered, so that text must hold when no goal is active:
    it may explain how to use the tools once ``/goal`` was run, never assert a mode
    nobody started.
    """
    pytest.importorskip("pi_goal_x")
    captured: list[Any] = []

    async def capture(model, context, options=None):
        captured.append(context)
        return await mock_text_stream(model, context, options)

    harness, _ = await _make_harness(tmp_path, stream_fn=capture)
    await harness.prompt("hi")

    prompt = captured[-1].system_prompt
    assert "goal_update" in prompt  # the contribution is rendered ...
    assert "You are in goal-driven mode" not in prompt  # ... without claiming a mode
    assert "/goal" in prompt  # and says when the tools apply


def _write_command_extension(directory: Path, command: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{command}.py").write_text(
        "def activate(pi):\n"
        f"    pi.register_command({command!r}, description='x', handler=lambda args: None)\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_user_extensions_come_from_the_home_the_session_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Audit P7-13: the CLI read its config from ``PI_HOME`` / the ``home`` it was given, but
    the extension loader went to ``Path.home()`` directly, so one session used two homes."""
    given_home = tmp_path / "given-home"
    real_user = tmp_path / "real-user"
    _write_command_extension(given_home / "extensions", "from_given_home")
    _write_command_extension(real_user / ".pi-python" / "extensions", "from_real_home")
    monkeypatch.delenv("PI_HOME", raising=False)
    monkeypatch.setenv("HOME", str(real_user))
    monkeypatch.setenv("USERPROFILE", str(real_user))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: real_user))
    session = await JsonlSessionRepo(tmp_path / "sessions").create({"cwd": str(tmp_path)})

    harness = await create_session_harness(
        session=session,
        cwd=tmp_path,
        config=CliConfig(),
        stream_fn=mock_text_stream,
        home=given_home,
    )
    await harness.load_extensions()

    commands = harness.extension_registry.get_commands()
    assert "from_given_home" in commands
    assert "from_real_home" not in commands
