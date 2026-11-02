"""Headless `python -m pi_agent_cli -p` (no TUI, no ACP stdio)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pi_agent_cli.config import CliConfig
from pi_agent_cli.headless import (
    HeadlessPromptOverrides,
    apply_prompt_overrides,
    assistant_text,
    prompt_from_json,
    resolve_print_prompt,
    run_print,
)
from pi_agent_cli.prompt_options import load_system_prompt_options
from pi_agent_core.messages import AssistantMessage, Usage


def test_assistant_text_joins_text_blocks():
    message = AssistantMessage(
        content=[
            {"type": "text", "text": "Hello "},
            {"type": "thinking", "thinking": "skip"},
            {"type": "text", "text": "world"},
        ],
        usage=Usage(),
        stopReason="stop",
        timestamp=1,
    )
    assert assistant_text(message) == "Hello world"


def test_prompt_from_json_blocks():
    raw = json.dumps([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])
    assert prompt_from_json(raw) == "ab"
    assert prompt_from_json(json.dumps("plain")) == "plain"


def test_resolve_print_prompt_file(tmp_path: Path):
    path = tmp_path / "p.txt"
    path.write_text("from file", encoding="utf-8")
    got = resolve_print_prompt(print_prompt=None, prompt_json=None, prompt_file=path)
    assert got == "from file"


@pytest.mark.asyncio
async def test_run_print_mock_stdout(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("PI_USE_MOCK", "1")
    monkeypatch.setenv("PI_HOME", str(tmp_path))
    code = await run_print("hello", cwd=tmp_path, home=tmp_path)
    assert code == 0
    assert "Hello from mock" in capsys.readouterr().out
    sessions = list((tmp_path / "sessions").glob("*.jsonl"))
    assert sessions, "headless should persist a JSONL session"


async def _bash_once_stream(model, context, options=None):
    """A model that asks to run ``echo ran > marker.txt`` once, then answers."""
    from pi_agent_core.event_stream import AssistantMessageEventStream
    from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
    from pi_agent_core.types import DoneEvent, StartEvent

    if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
        return await mock_text_stream(model, context, options)
    call = {
        "type": "toolCall",
        "id": "call_bash",
        "name": "bash",
        "arguments": {"command": "echo ran > marker.txt"},
    }
    partial = _base_partial(model, [call])
    partial.stopReason = "toolUse"
    stream = AssistantMessageEventStream()
    stream.push(StartEvent(partial=partial.model_copy(deep=True)))
    stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
    stream.set_final_message(partial)
    stream.end()
    return stream


@pytest.mark.parametrize("configured", ["ask", "auto", "always-approve", None])
@pytest.mark.asyncio
async def test_run_print_asks_nobody_and_says_so_in_its_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: str | None
):
    """Nobody can be asked in ``-p``. The mode is ``always-approve`` whatever agent.toml says:
    ``auto`` is a real mode now (it asks about what it cannot prove), so it is not that word.
    """
    from pi_agent_cli import headless

    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    if configured is not None:
        (home / "agent.toml").write_text(f'permission = "{configured}"\n', encoding="utf-8")
    monkeypatch.setenv("PI_HOME", str(home))
    monkeypatch.setattr(headless, "default_stream_fn", lambda: _bash_once_stream)
    seen: list[str] = []
    original = headless.create_session_harness

    async def spy(**kwargs):
        seen.append(kwargs["config"].permission)
        return await original(**kwargs)

    monkeypatch.setattr(headless, "create_session_harness", spy)

    code = await run_print("go", cwd=project, home=home)

    assert code == 0
    assert seen == ["always-approve"]
    assert (project / "marker.txt").read_text(encoding="utf-8").strip() == "ran"


@pytest.mark.asyncio
async def test_run_print_rejects_empty(tmp_path: Path, capsys):
    code = await run_print("   ", cwd=tmp_path, home=tmp_path)
    assert code == 2
    assert "empty prompt" in capsys.readouterr().out


def test_module_print_flag(tmp_path: Path):
    env = os.environ.copy()
    env["PI_USE_MOCK"] = "1"
    env["PI_HOME"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "pi_agent_cli", "-p", "hello", "--cwd", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "Hello from mock" in result.stdout


def test_apply_prompt_overrides_text_and_no_context(tmp_path: Path):
    config = apply_prompt_overrides(
        CliConfig(custom_system_prompt_file="ignored.md"),
        HeadlessPromptOverrides(
            system_prompt="Custom body",
            append_system_prompt="Extra",
            no_context_files=True,
        ),
    )
    assert config.custom_system_prompt == "Custom body"
    assert config.custom_system_prompt_file is None
    assert config.append_system_prompt == "Extra"
    assert config.no_context_files is True


def test_apply_prompt_overrides_disables_git():
    config = apply_prompt_overrides(
        CliConfig(),
        HeadlessPromptOverrides(no_git_context=True),
    )
    assert config.git_enabled is False


def test_apply_prompt_overrides_reads_files(tmp_path: Path):
    system_path = tmp_path / "system.txt"
    append_path = tmp_path / "append.txt"
    system_path.write_text("From file", encoding="utf-8")
    append_path.write_text("Append file", encoding="utf-8")
    config = apply_prompt_overrides(
        CliConfig(),
        HeadlessPromptOverrides(
            system_prompt_file=system_path,
            append_system_prompt_file=append_path,
        ),
    )
    assert config.custom_system_prompt == "From file"
    assert config.append_system_prompt == "Append file"


def test_no_context_files_cli_skips_agents_md(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("Project rules", encoding="utf-8")
    without = load_system_prompt_options(cwd=tmp_path, config=CliConfig(), home=tmp_path)
    assert any(item.content == "Project rules" for item in without.context_files or [])

    with_flag = load_system_prompt_options(
        cwd=tmp_path,
        config=apply_prompt_overrides(CliConfig(), HeadlessPromptOverrides(no_context_files=True)),
        home=tmp_path,
    )
    assert with_flag.context_files is None


def test_prompt_flags_require_headless(tmp_path: Path):
    env = os.environ.copy()
    env["PI_HOME"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "pi_agent_cli", "--no-context-files"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 2
    assert "require headless mode" in result.stderr
