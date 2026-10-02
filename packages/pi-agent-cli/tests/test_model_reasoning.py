"""The CLI never set ``Model.reasoning``, so ``thinking_level`` did nothing through it (P6-03).

The adapter asks a provider to think only when the model can reason (``Model.reasoning``) and
the request asks for it (a thinking level other than ``off``). The CLI took the level from its
configuration and built every model with ``reasoning=False``: on OpenAI and Anthropic the level
was dropped, and on DeepSeek's own API, which thinks unless told not to, it was always told not
to. Now the configuration carries both.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pi_agent_cli.config import CliConfig
from pi_agent_cli.factory import create_session_harness, default_stream_fn
from pi_agent_harness import JsonlSessionRepo


async def build_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: CliConfig):
    monkeypatch.setenv("PI_USE_MOCK", "1")
    monkeypatch.setenv("PI_HOME", str(tmp_path))
    session = await JsonlSessionRepo(tmp_path / "sessions").create({"cwd": str(tmp_path)})
    harness = await create_session_harness(
        session=session,
        cwd=tmp_path,
        config=config,
        stream_fn=default_stream_fn(),
        home=tmp_path,
    )
    return harness


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "reasons"),
    [
        (CliConfig(), False),
        (CliConfig(thinking_level="off"), False),
        (CliConfig(thinking_level="high"), True),
        (CliConfig(thinking_level="xhigh"), True),
        (CliConfig(thinking_level="high", reasoning=False), False),
        (CliConfig(thinking_level="off", reasoning=True), True),
    ],
)
async def test_the_harness_model_reasons_as_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: CliConfig, reasons: bool
):
    harness = await build_model(tmp_path, monkeypatch, config)

    assert harness.model.reasoning is reasons
    assert harness.thinking_level == config.thinking_level
