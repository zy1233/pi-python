"""A frozen build holds only the providers that were put into it; the others fail clearly.

``pip install`` cannot help inside a frozen build, so the error names what can: the
OpenAI-format provider with a ``base_url`` (``provider = "openai"``), or the Python package.
"""

from __future__ import annotations

import asyncio
import importlib
import sys

import pytest

from pi_agent_core.frozen import is_frozen
from pi_agent_core.types import LlmContext, Model, StreamOptions

ls_mod = importlib.import_module("pi_agent_core.adapters.langchain_stream")


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)


def _block(monkeypatch: pytest.MonkeyPatch, *modules: str) -> None:
    for name in modules:
        monkeypatch.setitem(sys.modules, name, None)  # an import of it raises ImportError


def test_a_plain_interpreter_is_not_frozen() -> None:
    assert is_frozen() is False


def test_a_frozen_build_is_frozen(frozen: None) -> None:
    assert is_frozen() is True


@pytest.mark.parametrize(
    ("provider", "module"),
    [
        ("anthropic", "langchain_anthropic"),
        ("deepseek", "langchain_deepseek"),
    ],
)
def test_an_unbundled_provider_points_to_openai_with_a_base_url(
    frozen: None, monkeypatch: pytest.MonkeyPatch, provider: str, module: str
) -> None:
    _block(monkeypatch, module)

    with pytest.raises(ImportError) as info:
        ls_mod.resolve_chat_model(Model(provider=provider, model_id="m"), api_key="k")

    text = str(info.value)
    assert f"'{provider}' provider is not part of this build" in text
    assert 'provider = "openai"' in text
    assert "base_url" in text
    assert "pip install" not in text


def test_a_provider_that_langchain_would_load_is_refused_the_same_way(
    frozen: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _block(monkeypatch, "langchain", "langchain.chat_models")

    with pytest.raises(ImportError) as info:
        ls_mod.resolve_chat_model(Model(provider="google_genai", model_id="gemini"), api_key="k")

    text = str(info.value)
    assert "'google_genai' provider is not part of this build" in text
    assert 'provider = "openai"' in text


def test_outside_a_frozen_build_the_old_advice_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    _block(monkeypatch, "langchain_anthropic")

    with pytest.raises(ImportError, match=r"pip install 'pi-agent-core\[anthropic\]'"):
        ls_mod.resolve_chat_model(Model(provider="anthropic", model_id="m"))


def test_the_error_reaches_the_user_as_an_error_event_not_an_exception(
    frozen: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _block(monkeypatch, "langchain_anthropic")

    async def run() -> list:
        stream = await ls_mod.langchain_stream(
            Model(provider="anthropic", model_id="claude-x"),
            LlmContext(system_prompt=None, messages=[]),
            StreamOptions(),
        )
        return [event async for event in stream]

    events = asyncio.run(run())

    assert events[-1].type == "error"
    assert 'provider = "openai"' in events[-1].error_message
