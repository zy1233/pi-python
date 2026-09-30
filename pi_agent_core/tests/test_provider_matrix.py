"""Opt-in live matrix over openai / anthropic / deepseek / SiliconFlow.

The live cases are marked ``real_llm``, which the pytest configuration leaves out of a plain
run (``addopts``), so keys that happen to be in a developer's shell never turn ``pytest`` into
paid API calls. They also skip when the row's API key is unset. Not a CI gate; run with:

    pytest -m real_llm pi_agent_core/tests/test_provider_matrix.py -v

The unmarked tests at the end of the file run offline: they check the table and the workflow.
``test_real_llm_selection.py`` checks that a plain run leaves the live cases out.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from pi_agent_core import AgentContext, AgentLoopConfig, Model, run_agent_loop
from pi_agent_core.adapters.langchain_convert import default_convert_to_llm
from pi_agent_core.messages import UserMessage
from pi_agent_core.tests.provider_matrix import MATRIX, ProviderRow, resolve_row
from pi_agent_core.tools import SimpleTool
from pi_agent_core.types import AgentToolResult

_ROOT = Path(__file__).resolve().parents[2]

_CASES = [
    pytest.param(row, capability, id=f"{row.id}-{capability}")
    for row in MATRIX
    for capability in row.capabilities
]


class _Signal:
    def __init__(self) -> None:
        self.aborted = False
        self._event = asyncio.Event()

    def abort(self) -> None:
        self.aborted = True
        self._event.set()

    async def wait_aborted(self) -> None:
        await self._event.wait()


def _model(row: ProviderRow, *, reasoning: bool = False) -> tuple[Model, str]:
    api_key, model_id, base_url = resolve_row(row)
    if not api_key:
        pytest.skip(f"{row.api_key_env} not set")
    return (
        Model(
            provider=row.provider,
            model_id=model_id,
            base_url=base_url,
            context_window=32_000,
            reasoning=reasoning,
        ),
        api_key,
    )


def _config(row: ProviderRow, *, reasoning: bool = False, **overrides) -> AgentLoopConfig:
    model, api_key = _model(row, reasoning=reasoning)
    return AgentLoopConfig(
        model=model,
        convert_to_llm=default_convert_to_llm,
        api_key=api_key,
        thinking_level="low" if reasoning else "off",
        **overrides,
    )


def _text(message: object) -> str:
    return "".join(
        str(block.get("text") or "")
        for block in getattr(message, "content", None) or []
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _has_thinking(message: object) -> bool:
    return any(
        isinstance(block, dict)
        and block.get("type") == "thinking"
        and str(block.get("thinking") or "").strip()
        for block in getattr(message, "content", None) or []
    )


async def _run(row: ProviderRow, prompt: str, *, reasoning: bool = False, tools=None, signal=None):
    events: list = []

    async def emit(event) -> None:
        events.append(event)

    messages = await run_agent_loop(
        [UserMessage(content=prompt)],
        AgentContext(system_prompt="Be concise.", messages=[], tools=tools or []),
        _config(row, reasoning=reasoning),
        emit,
        signal=signal,
    )
    return messages, events


@pytest.mark.real_llm
@pytest.mark.asyncio
@pytest.mark.parametrize(("row", "capability"), _CASES)
async def test_provider_capability(row: ProviderRow, capability: str):
    if capability == "text":
        messages, _events = await _run(row, "Reply with the single word pong.")
        final = messages[-1]
        assert _text(final).strip()
        assert final.stopReason == "stop"
        return

    if capability == "usage":
        messages, _events = await _run(row, "Reply with the single word pong.")
        usage = messages[-1].usage
        assert usage.input > 0 or usage.totalTokens > 0
        return

    if capability == "tools":

        class EchoParams(BaseModel):
            text: str = Field(description="Text to echo back")

        async def echo(_tool_call_id, params: EchoParams, _signal, _on_update):
            return AgentToolResult(content=[{"type": "text", "text": f"echo:{params.text}"}])

        tool = SimpleTool(
            name="echo",
            description="Echo the given text exactly. Always call this tool.",
            label="echo",
            parameters=EchoParams,
            execute_fn=echo,
        )
        messages, _events = await _run(
            row,
            "Call the echo tool with text matrix-token. Then repeat the tool result.",
            tools=[tool],
        )
        combined = " ".join(_text(message) for message in messages)
        assert "echo:matrix-token" in combined or any(
            getattr(message, "role", None) == "toolResult" for message in messages
        )
        tool_results = [
            message for message in messages if getattr(message, "role", None) == "toolResult"
        ]
        assert tool_results, "model did not call the echo tool"
        assert "echo:matrix-token" in _text(tool_results[-1]) or "matrix-token" in str(
            tool_results[-1]
        )
        return

    if capability == "thinking":
        messages, _events = await _run(
            row, "Think briefly, then reply with the single word pong.", reasoning=True
        )
        final = messages[-1]
        if _has_thinking(final):
            return
        if row.require_thinking:
            pytest.fail(f"{row.id} did not stream reasoning_content as a thinking block")
        pytest.xfail(f"{row.id} did not stream thinking; not treated as a protocol break")

    if capability == "abort":
        signal = _Signal()
        events: list = []

        async def emit(event) -> None:
            events.append(event)
            if getattr(event, "type", None) == "message_update" and not signal.aborted:
                signal.abort()

        messages = await run_agent_loop(
            [UserMessage(content="Count slowly from 1 to 200, one number per line.")],
            AgentContext(system_prompt="Be verbose.", messages=[], tools=[]),
            _config(row),
            emit,
            signal=signal,
        )
        assert messages[-1].stopReason == "aborted"
        assert events[-1].type == "agent_end"
        return

    raise AssertionError(f"unknown capability {capability}")


def test_matrix_rows_cover_spec():
    by_id = {row.id: row for row in MATRIX}
    assert set(by_id) == {"openai", "anthropic", "deepseek", "siliconflow"}
    assert "thinking" not in by_id["openai"].capabilities
    assert by_id["deepseek"].require_thinking is True
    assert by_id["siliconflow"].require_thinking is True
    assert by_id["siliconflow"].base_url == "https://api.siliconflow.cn/v1"


def test_unconfigured_row_skips(monkeypatch: pytest.MonkeyPatch):
    """Collecting the matrix without secrets must skip, not fail."""
    row = MATRIX[0]
    monkeypatch.delenv(row.api_key_env, raising=False)
    with pytest.raises(pytest.skip.Exception):
        _model(row)


def test_no_row_asks_for_a_model_deepseek_has_retired():
    """``deepseek-chat`` and ``deepseek-reasoner`` were retired on 2026-07-24 (audit P6-03).

    ``deepseek-chat`` was also the non-thinking alias, so the row's thinking case could not pass.
    """
    assert {row.model_id for row in MATRIX} & {"deepseek-chat", "deepseek-reasoner"} == set()


class TestWorkflow:
    """``.github/workflows/provider-matrix.yml``, read as text (no YAML parser needed)."""

    @pytest.fixture
    def text(self) -> str:
        return (_ROOT / ".github" / "workflows" / "provider-matrix.yml").read_text(encoding="utf-8")

    def test_it_can_still_be_started_by_hand(self, text: str):
        assert re.search(r"^\s*workflow_dispatch:", text, re.MULTILINE)

    def test_it_also_runs_on_a_schedule(self, text: str):
        """Nobody notices a provider drifting if the matrix only ever runs by hand (P6-03)."""
        match = re.search(
            r"^\s*schedule:[ \t]*\n"
            r"(?:[ \t]*#[^\n]*\n)*"  # comment lines may sit between the key and the entry
            r"[ \t]*-[ \t]*cron:[ \t]*['\"]([^'\"\n]+)['\"]",
            text,
            re.MULTILINE,
        )

        assert match, "no cron schedule"
        assert len(match.group(1).split()) == 5, match.group(1)

    def test_it_asks_for_the_live_cases_itself(self, text: str):
        """A plain run leaves them out, so this one has to select them."""
        assert re.search(r"^\s*run: pytest .*-m real_llm\b", text, re.MULTILINE)
