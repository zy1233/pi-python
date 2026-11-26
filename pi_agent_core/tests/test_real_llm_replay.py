"""``replay_reasoning`` against a real gateway (``real_llm``: left out of a plain run).

The fake-server tests cannot show what a real gateway does with ``reasoning_content``. These
tests run the task of ``scripts/probe_reasoning_replay.py`` against a live gateway of the OpenAI
format (``provider = "openai"``, as in ``agent.toml``). For the whole report, run the script.

They assert only what the agent promises: with ``replay_reasoning`` off, no thinking goes back,
and the run ends with an answer; with it on, the run ends with an answer too, because the
gateway takes the field or the agent asks again without it. What the model does with the thinking
is printed and not asserted, because a model does not behave the same way each time.

Run with (PowerShell):
    $env:REAL_LLM_API_KEY = 'sk-...'
    $env:REAL_LLM_BASE_URL = 'https://gateway.example/v1'
    $env:REAL_LLM_MODEL = 'model-name'
    .venv-test-real\\Scripts\\python.exe -m pytest -m real_llm -v -s `
        pi_agent_core/tests/test_real_llm_replay.py

Environment variables:
    REAL_LLM_API_KEY   - (required) the key of the gateway. The tests never print it.
    REAL_LLM_BASE_URL  - defaults to https://api.siliconflow.cn/v1
    REAL_LLM_MODEL     - defaults to deepseek-ai/DeepSeek-V4-Flash
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("langchain_openai")

BASE_URL = os.environ.get("REAL_LLM_BASE_URL", "https://api.siliconflow.cn/v1")
API_KEY = os.environ.get("REAL_LLM_API_KEY", "")
MODEL_ID = os.environ.get("REAL_LLM_MODEL", "deepseek-ai/DeepSeek-V4-Flash")

pytestmark = [
    pytest.mark.real_llm,
    pytest.mark.skipif(not API_KEY, reason="REAL_LLM_API_KEY not set"),
]

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "probe_reasoning_replay.py"


def _load():
    spec = importlib.util.spec_from_file_location("probe_reasoning_replay_live", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(module)
    return module


probe = _load()


def _settings():
    return probe.Settings(
        provider="openai",
        model_id=MODEL_ID,
        base_url=BASE_URL,
        api_key=API_KEY,
        thinking_level="high",
        hops=1,
        timeout=300.0,
    )


def _report(run) -> str:
    """The lines of the probe for one run. The key is taken out of them."""
    return "\n".join(probe.format_run(run, _settings(), 1, 1))


async def test_with_replay_off_no_thinking_goes_back():
    run = await probe.run_one(_settings(), replay=False)

    print("\n" + _report(run))
    assert not run.failed, _report(run)
    assert probe.judge_wire(run, API_KEY).code == "not_sent", _report(run)


async def test_with_replay_on_the_run_ends_with_an_answer():
    """The gateway takes ``reasoning_content``, or it refuses it and the agent asks again."""
    run = await probe.run_one(_settings(), replay=True)

    print("\n" + _report(run))
    wire = probe.judge_wire(run, API_KEY)
    assert wire.code in ("accepted", "refused_retried"), _report(run)
    assert not run.failed, _report(run)
