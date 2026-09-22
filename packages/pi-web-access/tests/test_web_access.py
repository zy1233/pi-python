"""Tests for pi-web-access extension (mock HTTP, no real API keys)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pi_web_access.fetch_url import (
    FetchUrlParams,
    _simple_html_to_text,
    create_fetch_url_tool,
    fetch_url_execute,
)
from pi_web_access.web_search import (
    WebSearchParams,
    _detect_provider,
    create_web_search_tool,
    web_search_execute,
)

# ---------------------------------------------------------------------------
# _simple_html_to_text
# ---------------------------------------------------------------------------


class TestHtmlToText:
    def test_strips_tags(self) -> None:
        html = "<p>Hello <b>world</b></p>"
        assert "Hello world" in _simple_html_to_text(html)

    def test_strips_scripts(self) -> None:
        html = "<script>alert('x')</script><p>Content</p>"
        text = _simple_html_to_text(html)
        assert "alert" not in text
        assert "Content" in text

    def test_strips_styles(self) -> None:
        html = "<style>body{color:red}</style><p>Text</p>"
        text = _simple_html_to_text(html)
        assert "color" not in text
        assert "Text" in text

    def test_decodes_entities(self) -> None:
        html = "&amp; &lt; &gt; &quot; &#39;"
        text = _simple_html_to_text(html)
        assert "& < > " in text


# ---------------------------------------------------------------------------
# web_search — provider detection
# ---------------------------------------------------------------------------


class TestProviderDetection:
    def test_brave_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRAVE_API_KEY", "test-key")
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("SEARXNG_URL", raising=False)
        name, cred = _detect_provider()
        assert name == "brave"
        assert cred == "test-key"

    def test_tavily_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-key")
        monkeypatch.delenv("SEARXNG_URL", raising=False)
        name, _cred = _detect_provider()
        assert name == "tavily"

    def test_searxng_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.setenv("SEARXNG_URL", "http://localhost:8080")
        name, _cred = _detect_provider()
        assert name == "searxng"

    def test_none_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("SEARXNG_URL", raising=False)
        name, _cred = _detect_provider()
        assert name == ""


# ---------------------------------------------------------------------------
# web_search — execute with mock
# ---------------------------------------------------------------------------


class TestWebSearchExecute:
    async def test_no_provider_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("SEARXNG_URL", raising=False)
        result = await web_search_execute("tc-1", WebSearchParams(query="test"))
        text = result.content[0]["text"]
        assert "No search provider configured" in text

    async def test_brave_search_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRAVE_API_KEY", "fake-key")
        mock_results = [
            {"title": "Result 1", "url": "https://example.com", "description": "Snippet 1"}
        ]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"web": {"results": mock_results}}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("pi_web_access.providers.brave.httpx.AsyncClient", return_value=mock_client):
            result = await web_search_execute(
                "tc-1", WebSearchParams(query="test", provider="brave")
            )

        text = result.content[0]["text"]
        assert "Result 1" in text
        assert "example.com" in text

    async def test_unknown_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRAVE_API_KEY", "x")
        result = await web_search_execute(
            "tc-1", WebSearchParams(query="test", provider="unknown_provider")
        )
        text = result.content[0]["text"]
        assert "Unknown provider" in text


# ---------------------------------------------------------------------------
# fetch_url — execute with mock
# ---------------------------------------------------------------------------


class TestFetchUrlExecute:
    async def test_fetch_html_extract(self) -> None:
        html = "<html><body><p>Hello World</p></body></html>"
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = html
        mock_response.headers = {"content-type": "text/html; charset=utf-8"}
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("pi_web_access.fetch_url.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_url_execute("tc-1", FetchUrlParams(url="https://example.com"))

        text = result.content[0]["text"]
        assert "Hello World" in text
        assert result.details["statusCode"] == 200

    async def test_fetch_raw_no_extract(self) -> None:
        raw = '{"key": "value"}'
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = raw
        mock_response.headers = {"content-type": "application/json"}
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("pi_web_access.fetch_url.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_url_execute(
                "tc-1", FetchUrlParams(url="https://api.example.com/data", extract_text=False)
            )

        text = result.content[0]["text"]
        assert '"key"' in text


# ---------------------------------------------------------------------------
# ToolDefinition shapes
# ---------------------------------------------------------------------------


class TestToolDefinitions:
    def test_web_search_tool_definition(self) -> None:
        tool = create_web_search_tool()
        assert tool.name == "web_search"
        assert tool.prompt_snippet is not None

    def test_fetch_url_tool_definition(self) -> None:
        tool = create_fetch_url_tool()
        assert tool.name == "fetch_url"
        assert tool.prompt_snippet is not None


# ---------------------------------------------------------------------------
# activate() entry point
# ---------------------------------------------------------------------------


class TestActivate:
    def test_activate_registers_tools(self) -> None:
        from pi_web_access import activate

        from pi_agent_core.extensions import ExtensionAPI, ExtensionRegistry
        from pi_agent_core.extensions.types import ExtensionMeta

        reg = ExtensionRegistry()
        api = ExtensionAPI(registry=reg, meta=ExtensionMeta(name="pi-web-access"))
        activate(api)
        tools = reg.get_tools()
        assert "web_search" in tools
        assert "fetch_url" in tools
