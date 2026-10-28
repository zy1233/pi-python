"""A user who has set no model is told so: in the turn error, in ``-p`` (status and stderr).

A configuration without a ``[model]`` table falls back to the provider ``mock``. LangChain has no
such provider, and its message (``pip install langchain``) hid the real cause.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from acp import text_block

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig, no_model_message
from pi_agent_cli.factory import default_stream_fn
from pi_agent_cli.headless import run_print
from pi_agent_core.types import LlmContext, Model, StreamOptions


class _Recorder:
    def __init__(self) -> None:
        self.updates: list[Any] = []

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append(update)

    def agent_text(self) -> str:
        return "".join(
            getattr(getattr(update, "content", None), "text", "") or ""
            for update in self.updates
            if getattr(update, "session_update", None) == "agent_message_chunk"
        )


def _no_mock(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.delenv("PI_USE_MOCK", raising=False)
    monkeypatch.setenv("PI_HOME", str(home))


@pytest.mark.asyncio
async def test_the_mock_provider_gets_an_error_that_says_no_model_is_set(monkeypatch, tmp_path):
    _no_mock(monkeypatch, tmp_path)
    stream_fn = default_stream_fn()
    stream = await stream_fn(
        Model(provider="mock", model_id="mock"),
        LlmContext(system_prompt=None, messages=[]),
        StreamOptions(),
    )
    message = await stream.message_result()
    assert message.stopReason == "error"
    assert message.errorMessage == no_model_message()
    assert "--setup" in message.errorMessage
    assert "agent.toml" in message.errorMessage
    assert "init_chat_model" not in message.errorMessage


@pytest.mark.asyncio
async def test_a_real_provider_still_goes_to_the_langchain_adapter(monkeypatch, tmp_path):
    _no_mock(monkeypatch, tmp_path)
    seen: list[str] = []

    async def fake_langchain_stream(model, context, options):
        seen.append(model.provider)
        return "the stream"

    # The package re-exports the function under the name of the module, so the module itself
    # has to be fetched by name.
    adapter = importlib.import_module("pi_agent_core.adapters.langchain_stream")
    monkeypatch.setattr(adapter, "langchain_stream", fake_langchain_stream)
    stream = await default_stream_fn()(
        Model(provider="openai", model_id="some-model"),
        LlmContext(system_prompt=None, messages=[]),
        StreamOptions(),
    )
    assert stream == "the stream"
    assert seen == ["openai"]


@pytest.mark.asyncio
async def test_the_scripted_llm_is_not_touched(monkeypatch, tmp_path):
    monkeypatch.setenv("PI_USE_MOCK", "1")
    monkeypatch.setenv("PI_HOME", str(tmp_path))
    stream = await default_stream_fn()(
        Model(provider="mock", model_id="mock"),
        LlmContext(system_prompt=None, messages=[]),
        StreamOptions(),
    )
    message = await stream.message_result()
    assert message.stopReason != "error"


@pytest.mark.asyncio
async def test_a_prompt_in_an_acp_session_shows_the_same_text(monkeypatch, tmp_path):
    _no_mock(monkeypatch, tmp_path)
    agent = PiAcpAgent(home=tmp_path, config=CliConfig())
    client = _Recorder()
    agent.on_connect(client)  # type: ignore[arg-type]
    session = await agent.new_session(cwd=str(tmp_path))
    await agent.prompt(session_id=session.session_id, prompt=[text_block("hi")])
    assert "Error: " + no_model_message() in client.agent_text()


@pytest.mark.asyncio
async def test_print_mode_fails_with_the_reason_on_stderr(monkeypatch, tmp_path, capsys):
    _no_mock(monkeypatch, tmp_path)
    code = await run_print("hi", cwd=tmp_path, home=tmp_path)
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err.strip().endswith("error: " + no_model_message())


def test_print_mode_process_exits_1(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in {"PI_USE_MOCK", "PI_HOME"}}
    env["PI_HOME"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "pi_agent_cli", "-p", "hi", "--cwd", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert result.returncode == 1, result.stderr
    assert result.stdout == ""
    assert "No model is set" in result.stderr
