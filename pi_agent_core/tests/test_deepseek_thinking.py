"""DeepSeek's thinking switch (audit P6-03).

DeepSeek's own API thinks unless it is told not to, and takes the switch as ``thinking`` in
the request body plus ``reasoning_effort``. The adapter sent neither, so ``thinking_level``
did nothing there and the API's default decided; the matrix row for it used ``deepseek-chat``,
the non-thinking alias, and asked for a thinking block.

Gateways that serve DeepSeek models (SiliconFlow, vLLM, ...) are not DeepSeek's API and are
left as they were.
"""

from __future__ import annotations

import pytest

from pi_agent_core.adapters.langchain_stream import (
    _apply_reasoning_params,
    _is_deepseek_api,
    resolve_chat_model,
)
from pi_agent_core.types import Model

OFFICIAL = [
    None,
    "",
    "   ",
    " https://api.deepseek.com/v1 ",
    "https://api.deepseek.com",
    "https://api.deepseek.com/v1",
    "https://API.DeepSeek.com/v1",
    "api.deepseek.com",
    "https://deepseek.com/v1",
    "http://api.deepseek.com:443/v1",
]
GATEWAYS = [
    "https://api.siliconflow.cn/v1",
    "http://localhost:8000/v1",
    "https://api.deepseek.com.evil.example/v1",
    "https://evil.example/api.deepseek.com",
    "https://notdeepseek.com/v1",
    "https://deepseek.com.cn/v1",
    "https://",
]
THINKING = {"type": "enabled"}
NOT_THINKING = {"type": "disabled"}


@pytest.fixture(autouse=True)
def _no_ambient_endpoint(monkeypatch: pytest.MonkeyPatch):
    """ChatDeepSeek reads DEEPSEEK_API_BASE when no base_url is given."""
    monkeypatch.delenv("DEEPSEEK_API_BASE", raising=False)


def deepseek(*, reasoning: bool = True, base_url: str | None = None) -> Model:
    return Model(
        provider="deepseek", model_id="deepseek-v4-flash", reasoning=reasoning, base_url=base_url
    )


class TestWhichEndpointsAreDeepSeek:
    @pytest.mark.parametrize("base_url", OFFICIAL)
    def test_its_own_api_is_recognised(self, base_url: str | None):
        assert _is_deepseek_api(base_url) is True

    @pytest.mark.parametrize("base_url", GATEWAYS)
    def test_a_gateway_or_a_lookalike_is_not(self, base_url: str):
        assert _is_deepseek_api(base_url) is False

    def test_without_a_base_url_the_environment_decides_as_it_does_for_the_client(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("DEEPSEEK_API_BASE", "https://api.siliconflow.cn/v1")
        assert _is_deepseek_api(None) is False

        monkeypatch.setenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
        assert _is_deepseek_api(None) is True

    def test_a_base_url_wins_over_the_environment(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DEEPSEEK_API_BASE", "https://api.siliconflow.cn/v1")
        assert _is_deepseek_api("https://api.deepseek.com") is True

        monkeypatch.setenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
        assert _is_deepseek_api("https://api.siliconflow.cn/v1") is False


class TestThinkingOn:
    @pytest.mark.parametrize(
        ("level", "effort"),
        [
            ("minimal", "high"),
            ("low", "high"),
            ("medium", "high"),
            ("high", "high"),
            ("xhigh", "max"),
        ],
    )
    @pytest.mark.parametrize("base_url", [None, "https://api.deepseek.com/v1"])
    def test_a_reasoning_model_is_asked_to_think_with_the_documented_effort(
        self, level: str, effort: str, base_url: str | None
    ):
        kwargs = _apply_reasoning_params({}, deepseek(base_url=base_url), level)  # type: ignore[arg-type]

        assert kwargs == {"extra_body": {"thinking": THINKING}, "reasoning_effort": effort}

    def test_a_level_it_has_no_effort_for_still_thinks_at_the_default_effort(self):
        kwargs = _apply_reasoning_params({}, deepseek(), "ultra")  # type: ignore[arg-type]

        assert kwargs == {"extra_body": {"thinking": THINKING}}

    def test_what_the_caller_already_set_is_kept_and_not_modified(self):
        given = {"model": "deepseek-v4-flash", "extra_body": {"other": 1}}

        kwargs = _apply_reasoning_params(given, deepseek(), "high")

        assert kwargs["model"] == "deepseek-v4-flash"
        assert kwargs["extra_body"] == {"other": 1, "thinking": THINKING}
        assert given == {"model": "deepseek-v4-flash", "extra_body": {"other": 1}}

    def test_the_provider_name_is_not_case_sensitive(self):
        model = Model(provider="DeepSeek", model_id="m", reasoning=True)

        assert _apply_reasoning_params({}, model, "low")["extra_body"] == {"thinking": THINKING}


class TestThinkingOff:
    """Its default is on, so not thinking has to be asked for."""

    @pytest.mark.parametrize("level", ["off", None])
    def test_no_level_means_do_not_think(self, level: str | None):
        kwargs = _apply_reasoning_params({}, deepseek(), level)  # type: ignore[arg-type]

        assert kwargs == {"extra_body": {"thinking": NOT_THINKING}}

    @pytest.mark.parametrize("level", ["minimal", "high", "xhigh"])
    def test_a_model_not_declared_to_reason_does_not_think_whatever_the_level(self, level: str):
        kwargs = _apply_reasoning_params({}, deepseek(reasoning=False), level)  # type: ignore[arg-type]

        assert kwargs == {"extra_body": {"thinking": NOT_THINKING}}

    def test_what_the_caller_already_set_is_kept_and_not_modified(self):
        given = {"extra_body": {"other": 1}}

        kwargs = _apply_reasoning_params(given, deepseek(), "off")

        assert kwargs["extra_body"] == {"other": 1, "thinking": NOT_THINKING}
        assert given == {"extra_body": {"other": 1}}


class TestGatewaysAreLeftAlone:
    @pytest.mark.parametrize("base_url", GATEWAYS)
    @pytest.mark.parametrize("reasoning", [True, False])
    @pytest.mark.parametrize("level", ["off", "low", "xhigh", None])
    def test_nothing_is_added(self, base_url: str, reasoning: bool, level: str | None):
        model = deepseek(reasoning=reasoning, base_url=base_url)

        assert _apply_reasoning_params({}, model, level) == {}  # type: ignore[arg-type]

    def test_a_gateway_named_by_the_environment_is_left_alone_too(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("DEEPSEEK_API_BASE", "https://api.siliconflow.cn/v1")

        assert _apply_reasoning_params({}, deepseek(), "high") == {}


class TestOtherProvidersAreNotAffected:
    def test_openai_pointed_at_deepseek_keeps_its_own_rules(self):
        model = Model(
            provider="openai", model_id="m", reasoning=True, base_url="https://api.deepseek.com"
        )

        assert _apply_reasoning_params({}, model, "high") == {"reasoning_effort": "high"}
        assert _apply_reasoning_params({}, model, "off") == {}


class TestWhatTheChatModelSends:
    """The kwargs must reach the request: ChatDeepSeek has to take and pass them on."""

    @pytest.fixture(autouse=True)
    def _langchain_deepseek(self):
        pytest.importorskip("langchain_deepseek")

    @staticmethod
    def payload(model: Model, level: str | None) -> dict:
        from langchain_core.messages import HumanMessage

        chat = resolve_chat_model(model, "sk-test", level)  # type: ignore[arg-type]
        return chat._get_request_payload([HumanMessage("hi")])

    def test_thinking_and_effort_are_in_the_request(self):
        payload = self.payload(deepseek(), "xhigh")

        assert payload["extra_body"] == {"thinking": THINKING}
        assert payload["reasoning_effort"] == "max"

    def test_not_thinking_is_in_the_request(self):
        payload = self.payload(deepseek(), "off")

        assert payload["extra_body"] == {"thinking": NOT_THINKING}
        assert "reasoning_effort" not in payload

    def test_a_gateway_gets_neither(self):
        payload = self.payload(deepseek(base_url="https://api.siliconflow.cn/v1"), "high")

        assert not payload.get("extra_body")
        assert "reasoning_effort" not in payload
