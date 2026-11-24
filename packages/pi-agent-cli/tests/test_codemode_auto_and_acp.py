"""Tests for ``[codemode]`` CLI wiring, prompt deferred tools, Auto runtime gating, and ACP."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acp import text_block
from acp.schema import AllowedOutcome, RequestPermissionResponse
from pydantic import BaseModel

from pi_agent_cli import auto_mode
from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.auto_reasons import Reason, sentence
from pi_agent_cli.auto_rules import Outcome
from pi_agent_cli.config import AutoConfig, CliConfig, CodemodeConfig, load_config
from pi_agent_cli.events import project_event, tool_kind, tool_title
from pi_agent_cli.factory import create_session_harness
from pi_agent_cli.permissions import _prompt_title, needs_permission
from pi_agent_cli.system_prompt import BuildSystemPromptOptions, build_system_prompt
from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.extensions import ExtensionAPI, ToolDefinition
from pi_agent_core.messages import ToolCallContent
from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
from pi_agent_core.types import (
    AgentToolResult,
    DoneEvent,
    ErrorEvent,
    StartEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from pi_agent_harness import JsonlSessionRepo


class _World:
    def __init__(self, root: Path) -> None:
        self.home = root / "home" / ".pi-python"
        self.project = root / "project"
        self.project.mkdir(parents=True)
        self.home.mkdir(parents=True)


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


class _Client:
    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.updates: list[Any] = []
        self.requests: list[Any] = []

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append(update)

    async def request_permission(
        self, session_id: str, tool_call: Any, options: Any, **kwargs: Any
    ) -> RequestPermissionResponse:
        self.requests.append(tool_call)
        option_id = "allow-once" if self.allow else "reject-once"
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id=option_id)
        )

    def titles(self) -> list[str]:
        return [str(req.title) for req in self.requests]


def _codemode_stream(code: str, *, seen_results: list[str] | None = None):
    """Stream fn: first assistant message calls ``codemode``, second returns final text."""

    async def stream(model: Any, context: Any, options: Any = None):
        sig = getattr(options, "signal", None) if options is not None else None
        if sig is not None and getattr(sig, "aborted", False):
            events = AssistantMessageEventStream()
            partial = _base_partial(model, [])
            partial.stopReason = "aborted"
            events.push(StartEvent(partial=partial.model_copy(deep=True)))
            events.push(
                ErrorEvent(
                    partial=partial.model_copy(deep=True),
                    reason="aborted",
                    error_message="Aborted",
                )
            )
            events.set_final_message(partial)
            events.end()
            return events
        tool_msgs = [m for m in context.messages if getattr(m, "role", None) == "toolResult"]
        if tool_msgs:
            if seen_results is not None:
                for m in tool_msgs:
                    for block in getattr(m, "content", None) or []:
                        if isinstance(block, dict) and block.get("type") == "text":
                            seen_results.append(str(block.get("text") or ""))
                        elif getattr(block, "type", None) == "text":
                            seen_results.append(str(getattr(block, "text", "") or ""))
            return await mock_text_stream(model, context, options)
        events = AssistantMessageEventStream()
        content: list[ToolCallContent] = [
            {"type": "toolCall", "id": "call_cm", "name": "codemode", "arguments": {"code": code}}
        ]
        partial = _base_partial(model, content)
        partial.stopReason = "toolUse"
        events.push(StartEvent(partial=partial.model_copy(deep=True)))
        events.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
        events.set_final_message(partial)
        events.end()
        return events

    return stream


async def _open_agent(
    world: _World,
    stream_fn: Any = mock_text_stream,
    *,
    permission: str = "auto",
    client: _Client | None = None,
    codemode: CodemodeConfig | None = None,
    auto: AutoConfig | None = None,
) -> tuple[PiAcpAgent, _Client, str]:
    config = CliConfig(
        permission=permission,  # type: ignore[arg-type]
        provider="mock",
        model_id="mock",
        auto=auto or AutoConfig(classifier="rules"),
        codemode=codemode or CodemodeConfig(),
    )
    agent = PiAcpAgent(stream_fn=stream_fn, home=world.home, config=config)
    cli = client or _Client()
    agent.on_connect(cli)
    created = await agent.new_session(cwd=str(world.project))
    await asyncio.gather(*agent._background_tasks)
    return agent, cli, created.session_id


# ---------------------------------------------------------------------------- [codemode] config


def test_codemode_config_defaults_and_valid_overrides(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    assert load_config(home).codemode == CodemodeConfig()

    (home / "agent.toml").write_text(
        """
[codemode]
enabled = false
mode = "ONLY"
defer_mcp = false
require_approval = true
timeout_seconds = 45.5
max_tool_calls = 25
max_output_kb = 128
""".strip(),
        encoding="utf-8",
    )
    cfg = load_config(home)
    assert cfg.codemode == CodemodeConfig(
        enabled=False,
        mode="only",
        defer_mcp=False,
        require_approval=True,
        timeout_seconds=45.5,
        max_tool_calls=25,
        max_output_kb=128,
        notes=(),
    )
    assert cfg.codemode.to_dict() == {
        "enabled": False,
        "mode": "only",
        "defer_mcp": False,
        "require_approval": True,
        "timeout_seconds": 45.5,
        "max_tool_calls": 25,
        "max_output_kb": 128,
    }
    assert cfg.auto.notes == ()


def test_codemode_config_rejects_invalid_values_with_notes(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "agent.toml").write_text(
        """
[codemode]
enabled = "yes"
mode = "turbo"
defer_mcp = 1
require_approval = "true"
timeout_seconds = true
max_tool_calls = 999
max_output_kb = 2
extra_flag = 123
""".strip(),
        encoding="utf-8",
    )
    cfg = load_config(home)
    assert cfg.codemode.enabled is True
    assert cfg.codemode.mode == "hybrid"
    assert cfg.codemode.defer_mcp is True
    assert cfg.codemode.require_approval is False
    assert cfg.codemode.timeout_seconds == 120.0
    assert cfg.codemode.max_tool_calls == 100
    assert cfg.codemode.max_output_kb == 64
    assert len(cfg.codemode.notes) == 8
    # Notes are surfaced alongside auto.notes without touching agent.py.
    for note in cfg.codemode.notes:
        assert note in cfg.auto.notes


def test_codemode_non_table_and_project_local_ignored(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    (project / ".pi-python").mkdir(parents=True)
    (project / ".pi-python" / "agent.toml").write_text(
        "[codemode]\nenabled = false\nrequire_approval = false\n", encoding="utf-8"
    )
    assert load_config(home).codemode == CodemodeConfig()

    (home / "agent.toml").write_text('codemode = "nope"\n', encoding="utf-8")
    cfg = load_config(home)
    assert cfg.codemode.enabled is True
    assert any("not a table" in n for n in cfg.auto.notes)


# --------------------------------------------------------------- system prompt & factory wiring


def test_system_prompt_renders_grouped_deferred_tools(tmp_path: Path) -> None:
    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            cwd=str(tmp_path),
            selected_tools=["read", "codemode", "tool_search"],
            tool_snippets={
                "read": "Read a file",
                "codemode": "Run script",
                "tool_search": "Search tools",
            },
            deferred_tool_names=[
                "mcp__github__get_issue",
                "mcp__github__create_issue",
                "mcp__github__list_pulls",
                "mcp__github__add_comment",
                "mcp__github__get_pr",
                "mcp__github__search_code",
                "mcp__slack__post_message",
                "custom_deferred_tool",
            ],
        )
    )
    assert "<deferred_tools>" in prompt
    assert "</deferred_tools>" in prompt
    assert "- mcp__github (6 tools:" in prompt
    assert ", ...)" in prompt
    assert "- mcp__slack (1 tool: mcp__slack__post_message)" in prompt
    assert "- other (1 tool: custom_deferred_tool)" in prompt
    assert prompt.index("</deferred_tools>") < prompt.index("Current working directory:")

    empty = build_system_prompt(BuildSystemPromptOptions(cwd=str(tmp_path)))
    assert "<deferred_tools>" not in empty


async def test_factory_wires_codemode_only_mode_and_extension_settings(world: _World) -> None:
    repo = JsonlSessionRepo(str(world.home / "sessions"))
    session = await repo.create({"cwd": str(world.project)})
    seen_prompts: list[str] = []

    class _PingParams(BaseModel):
        target: str = "localhost"

    def deferred_mcp_stub(pi: ExtensionAPI) -> None:
        async def _run(_id: str, _params: _PingParams, _sig: Any, _upd: Any) -> AgentToolResult:
            return AgentToolResult(content=[{"type": "text", "text": "pong"}])

        pi.register_tool(
            ToolDefinition(
                name="mcp__demo__ping",
                description="Ping demo MCP endpoint",
                parameters=_PingParams,
                execute=_run,
                deferred=True,
                annotations={"readOnlyHint": True},
            )
        )

    async def capture_stream(model: Any, context: Any, options: Any = None):
        seen_prompts.append(str(context.system_prompt or ""))
        return await mock_text_stream(model, context, options)

    cfg = CliConfig(
        permission="ask",
        provider="mock",
        model_id="mock",
        codemode=CodemodeConfig(enabled=True, mode="only", defer_mcp=True, require_approval=True),
    )
    harness = await create_session_harness(
        session=session,
        cwd=world.project,
        config=cfg,
        stream_fn=capture_stream,
        home=world.home,
        extensions=[deferred_mcp_stub],
    )
    assert harness.get_extension_settings("pi-codemode") == cfg.codemode.to_dict()
    assert harness.get_extension_settings("pi-mcp") == {"defer_mcp": True}

    await harness.load_extensions()
    await harness.prompt("hello")
    assert set(harness.active_tool_names) == {"codemode", "tool_search"}
    assert harness.get_tool("bash") is not None
    assert "mcp__demo__ping" in harness.deferred_tool_names
    assert seen_prompts and "<deferred_tools>" in seen_prompts[0]
    assert "mcp__demo__ping" in seen_prompts[0]


# --------------------------------------------------------------- Auto mode runtime gating & redteam


async def test_auto_mode_allows_safe_in_project_calls_and_refuses_unknown_origin(
    world: _World,
) -> None:
    seen: list[str] = []
    code = (
        'await tools.write(path="note.txt", content="from codemode\\n")\n'
        'content = await tools.read(path="note.txt")\n'
        "return content"
    )
    agent, client, session_id = await _open_agent(
        world,
        _codemode_stream(code, seen_results=seen),
        permission="auto",
        client=_Client(allow=False),
    )

    await agent.prompt(session_id=session_id, prompt=[text_block("write and read")])

    assert client.requests == []
    assert (world.project / "note.txt").read_text(encoding="utf-8") == "from codemode\n"
    assert any("from codemode" in text for text in seen)

    # Direct AutoSession checks: codemode allowed for safe in-project write, unknown still asks.
    auto = agent._autos[session_id]
    ok_verdict = await auto.decide(
        SimpleNamespace(
            toolName="write",
            toolCallId="cm-direct-1",
            input={"path": str(world.project / "direct.txt"), "content": "x"},
            annotations=None,
            origin={"kind": "codemode", "cwd": str(world.project)},
        )
    )
    assert ok_verdict.outcome is Outcome.ALLOW

    bad_verdict = await auto.decide(
        SimpleNamespace(
            toolName="write",
            toolCallId="cm-direct-2",
            input={"path": str(world.project / "direct.txt"), "content": "x"},
            annotations=None,
            origin={"kind": "other", "cwd": str(world.project)},
        )
    )
    assert bad_verdict.outcome is Outcome.ASK
    assert bad_verdict.rule_id == "session.unknown_origin"


async def test_auto_redteam_runtime_dynamic_protected_path_caught_before_write(
    world: _World,
) -> None:
    """Dynamically constructed path inside script defeats static analysis, not runtime gating."""
    seen: list[str] = []
    code = (
        'target = ".github/" + "workflows/ci.yml"\n'
        'await tools.write(path=target, content="name: pwn\\n")\n'
        'return "wrote"'
    )
    agent, client, session_id = await _open_agent(
        world,
        _codemode_stream(code, seen_results=seen),
        permission="auto",
        client=_Client(allow=False),
    )

    await agent.prompt(session_id=session_id, prompt=[text_block("attempt stealth write")])

    assert len(client.requests) == 1
    title = client.titles()[0]
    assert title.startswith("write (")
    assert sentence(Reason.PROTECTED_PATH) in title
    assert "codemode" in title
    assert not (world.project / ".github" / "workflows" / "ci.yml").exists()
    assert any("ToolPermissionError" in text for text in seen)


async def test_auto_redteam_runtime_outside_path_and_destructive_bash_caught(
    world: _World, tmp_path: Path
) -> None:
    outside = tmp_path / "escape.txt"
    seen: list[str] = []
    code = (
        "results = []\n"
        f"for action in [('write', {{'path': {str(outside)!r}, 'content': 'bad'}}), "
        "('bash', {'command': 'rm -rf build'})]:\n"
        "    try:\n"
        "        await call_tool(action[0], action[1])\n"
        "        results.append('ALLOWED')\n"
        "    except ToolPermissionError as exc:\n"
        "        results.append(f'BLOCKED:{exc.tool_name}')\n"
        "return ','.join(results)"
    )
    agent, client, session_id = await _open_agent(
        world,
        _codemode_stream(code, seen_results=seen),
        permission="auto",
        client=_Client(allow=False),
    )

    await agent.prompt(session_id=session_id, prompt=[text_block("try escape and rm")])

    assert len(client.requests) == 2
    assert sentence(Reason.OUTSIDE_PROJECT) in client.titles()[0]
    assert "codemode" in client.titles()[0]
    assert sentence(Reason.DESTRUCTIVE) in client.titles()[1]
    assert "codemode" in client.titles()[1]
    assert not outside.exists()
    assert any("BLOCKED:write,BLOCKED:bash" in text for text in seen)


async def test_auto_parallel_inner_calls_are_serialized_decide_run_decide_run(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    log: list[str] = []
    real_decide = auto_mode.decide_rules_only

    def spy_decide(call: Any, ctx: Any):
        log.append(f"decide:{call.name}:{call.input.get('path', '')}")
        return real_decide(call, ctx)

    monkeypatch.setattr(auto_mode, "decide_rules_only", spy_decide)
    seen: list[str] = []
    code = (
        "await parallel(\n"
        '    tools.write(path="p1.txt", content="1"),\n'
        '    tools.write(path="p2.txt", content="2"),\n'
        ")\n"
        'return "done"'
    )
    agent, client, session_id = await _open_agent(
        world,
        _codemode_stream(code, seen_results=seen),
        permission="auto",
        client=_Client(allow=False),
    )
    write_tool = agent._harnesses[session_id].get_tool("write")
    inner_exec = write_tool.execute_fn

    async def spy_exec(call_id: str, params: Any, signal: Any = None, on_update: Any = None):
        path = getattr(params, "path", "")
        log.append(f"run:start:{path}")
        await asyncio.sleep(0.02)
        try:
            return await inner_exec(call_id, params, signal, on_update)
        finally:
            log.append(f"run:end:{path}")

    write_tool.execute_fn = spy_exec

    await agent.prompt(session_id=session_id, prompt=[text_block("parallel writes")])

    assert client.requests == []
    assert log == [
        "decide:write:p1.txt",
        "run:start:p1.txt",
        "run:end:p1.txt",
        "decide:write:p2.txt",
        "run:start:p2.txt",
        "run:end:p2.txt",
    ]
    assert any("done" in text for text in seen)


async def test_auto_bash_limits_applied_to_inner_codemode_bash(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_limits: list[Any] = []
    real_take = auto_mode.BashLimitRegistry.take

    def spy_take(self: Any, call_id: str) -> Any:
        lim = real_take(self, call_id)
        seen_limits.append((call_id, lim))
        return lim

    monkeypatch.setattr(auto_mode.BashLimitRegistry, "take", spy_take)
    auto = AutoConfig(classifier="rules", bash_timeout_seconds=12.5, bash_max_output_mb=7)
    code = 'return await tools.bash(command="echo INNER-BASH-OK")'
    seen: list[str] = []
    agent, client, session_id = await _open_agent(
        world,
        _codemode_stream(code, seen_results=seen),
        permission="auto",
        auto=auto,
        client=_Client(allow=False),
    )

    await agent.prompt(session_id=session_id, prompt=[text_block("run readonly bash")])

    assert client.requests == []
    assert len(seen_limits) == 1
    call_id, lim = seen_limits[0]
    assert call_id.startswith("codemode-")
    assert lim is not None
    assert lim.timeout_seconds == 12.5
    assert lim.max_output_bytes == 7 * 1024 * 1024
    # Once taken, the registry entry is consumed.
    assert real_take(agent._autos[session_id].limits, call_id) is None


# --------------------------------------------------------------- ACP events, permissions, cancel


def test_acp_event_projection_and_permission_titles() -> None:
    assert tool_kind("codemode") == "execute"
    assert tool_kind("tool_search") == "search"
    assert tool_title("codemode", {"code": "x = 1\nreturn x\n"}) == "codemode (2 lines)"
    assert tool_title("codemode", {"code": "1"}) == "codemode (1 line)"
    assert tool_title("tool_search", {"query": "github issue"}) == "tool_search github issue"

    start = list(
        project_event(
            ToolExecutionStartEvent(
                tool_call_id="c1",
                tool_name="codemode",
                args={"code": "return 1"},
            )
        )
    )
    assert len(start) == 1
    assert start[0].kind == "execute"
    assert start[0].title == "codemode (1 line)"

    upd = list(
        project_event(
            ToolExecutionUpdateEvent(
                tool_call_id="c1",
                tool_name="codemode",
                args={"code": "return 1"},
                partial_result={"content": [{"type": "text", "text": "▸ read a.py"}]},
            )
        )
    )
    assert len(upd) == 1
    assert upd[0].status == "in_progress"

    end = list(
        project_event(
            ToolExecutionEndEvent(
                tool_call_id="c1",
                tool_name="codemode",
                args={"code": "return 1"},
                result={"content": [{"type": "text", "text": "1"}], "details": {}},
                is_error=False,
            )
        )
    )
    assert len(end) == 1
    assert end[0].status == "completed"

    assert _prompt_title("write", {"kind": "codemode", "cwd": "/repo"}) == "write (codemode)"
    assert "Auto:" in _prompt_title(
        "write", {"kind": "codemode", "cwd": "/repo"}, auto_reason=Reason.PROTECTED_PATH
    )
    assert "codemode" in _prompt_title(
        "write", {"kind": "codemode", "cwd": "/repo"}, auto_reason=Reason.PROTECTED_PATH
    )
    assert needs_permission("ask", {"destructiveHint": False, "openWorldHint": False}) is False
    assert needs_permission("ask", None) is True


async def test_require_approval_prompts_outer_codemode_call(world: _World) -> None:
    seen: list[str] = []
    agent, client, session_id = await _open_agent(
        world,
        _codemode_stream('return "ok"', seen_results=seen),
        permission="ask",
        codemode=CodemodeConfig(require_approval=True),
        client=_Client(allow=False),
    )

    await agent.prompt(session_id=session_id, prompt=[text_block("run codemode")])

    assert client.titles() == ["codemode"]
    assert any("User denied permission" in text for text in seen)


async def test_ask_mode_prompts_inner_write_with_codemode_title(world: _World) -> None:
    seen: list[str] = []
    code = 'await tools.write(path="asked.txt", content="yes\\n")\nreturn "ok"'
    agent, client, session_id = await _open_agent(
        world,
        _codemode_stream(code, seen_results=seen),
        permission="ask",
        client=_Client(allow=True),
    )

    await agent.prompt(session_id=session_id, prompt=[text_block("ask inner write")])

    assert client.titles() == ["write (codemode)"]
    assert (world.project / "asked.txt").read_text(encoding="utf-8") == "yes\n"


async def test_session_cancel_aborts_running_codemode(world: _World) -> None:
    import time

    started = asyncio.Event()
    agent, _, session_id = await _open_agent(
        world,
        _codemode_stream("import asyncio\nawait asyncio.sleep(30)\nreturn 'never'"),
        permission="always-approve",
    )

    def on_event(event: Any, signal: Any = None) -> None:
        if (
            getattr(event, "type", None) == "tool_execution_start"
            and getattr(event, "tool_name", "") == "codemode"
        ):
            started.set()

    agent._harnesses[session_id].subscribe(on_event)

    t0 = time.monotonic()
    task = asyncio.create_task(
        agent.prompt(session_id=session_id, prompt=[text_block("sleep long")])
    )
    await asyncio.wait_for(started.wait(), timeout=10.0)
    await asyncio.sleep(0.2)
    await agent.cancel(session_id=session_id)
    resp = await asyncio.wait_for(task, timeout=10.0)
    elapsed = time.monotonic() - t0
    assert resp.stop_reason == "cancelled"
    assert elapsed < 10.0
