"""fetch_url tool — fetch and read the contents of a URL."""

from __future__ import annotations

import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from pi_agent_core.coding_tools.truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, truncate_head
from pi_agent_core.types import AgentToolResult


class FetchUrlParams(BaseModel):
    url: str = Field(description="The URL to fetch")
    extract_text: bool = Field(
        default=True,
        description="Extract readable text from HTML (strip tags). Set false for raw content.",
    )
    max_length: int | None = Field(
        default=None,
        description="Maximum number of lines to return (default: 2000)",
    )


_TAG_RE = re.compile(r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>", re.DOTALL | re.I)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\n{3,}")


def _simple_html_to_text(html: str) -> str:
    """Lightweight HTML-to-text (no external deps)."""
    text = _TAG_RE.sub("", html)
    text = _HTML_TAG_RE.sub("", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    text = _WS_RE.sub("\n\n", text)
    return text.strip()


def _extract_text(html: str) -> str:
    """Try trafilatura first, fall back to simple tag stripping."""
    try:
        import trafilatura

        result = trafilatura.extract(html, include_comments=False, include_tables=True)
        if result:
            return result
    except ImportError:
        pass
    return _simple_html_to_text(html)


async def fetch_url_execute(
    tool_call_id: str,
    params: Any,
    signal: Any = None,
    on_update: Any = None,
) -> AgentToolResult:
    try:
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": "pi-python/0.1 (web-access extension)"},
        ) as client:
            resp = await client.get(params.url)
            resp.raise_for_status()
            raw = resp.text

        if params.extract_text and "html" in resp.headers.get("content-type", "").lower():
            text = _extract_text(raw)
        else:
            text = raw

        max_lines = params.max_length or DEFAULT_MAX_LINES
        result = truncate_head(text, max_lines=max_lines, max_bytes=DEFAULT_MAX_BYTES)

        notice = ""
        if result.truncated:
            notice = (
                f"\n[Showing {result.outputLines} of {result.totalLines} lines. "
                f"Content truncated at {max_lines} lines.]"
            )

        return AgentToolResult(
            content=[{"type": "text", "text": result.content + notice}],
            details={
                "url": params.url,
                "statusCode": resp.status_code,
                "contentType": resp.headers.get("content-type", ""),
                "truncated": result.truncated,
            },
        )
    except httpx.HTTPStatusError as e:
        return AgentToolResult(
            content=[
                {"type": "text", "text": f"HTTP {e.response.status_code} fetching {params.url}"}
            ]
        )
    except Exception as e:
        return AgentToolResult(
            content=[{"type": "text", "text": f"Failed to fetch {params.url}: {e}"}]
        )


def create_fetch_url_tool() -> Any:
    """Return a ToolDefinition for the fetch_url tool."""
    from pi_agent_core.extensions.types import ToolDefinition

    return ToolDefinition(
        name="fetch_url",
        description="Fetch and read the contents of a URL. Extracts readable text from HTML pages.",
        parameters=FetchUrlParams,
        execute=fetch_url_execute,
        label="Fetch URL",
        prompt_snippet="Fetch and read the contents of a URL",
        prompt_guidelines=[
            "Use fetch_url to read the full content of a web page when you have a specific URL.",
            "Combine with web_search: search first, then fetch relevant URLs for details.",
        ],
    )
