"""web_search tool — search the web for real-time information."""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field

from pi_agent_core.types import AgentToolResult


class WebSearchParams(BaseModel):
    query: str = Field(description="The search query")
    provider: str | None = Field(
        default=None,
        description="Search provider: 'brave', 'tavily', or 'searxng'. Auto-detected if omitted.",
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of results to return",
    )


def _detect_provider() -> tuple[str, str]:
    """Auto-detect search provider from environment variables.

    Returns (provider_name, credential).
    """
    brave_key = os.environ.get("BRAVE_API_KEY")
    if brave_key:
        return "brave", brave_key

    tavily_key = os.environ.get("TAVILY_API_KEY")
    if tavily_key:
        return "tavily", tavily_key

    searxng_url = os.environ.get("SEARXNG_URL")
    if searxng_url:
        return "searxng", searxng_url

    return "", ""


async def web_search_execute(
    tool_call_id: str,
    params: Any,
    signal: Any = None,
    on_update: Any = None,
) -> AgentToolResult:
    provider_name = params.provider
    credential = ""

    if provider_name:
        if provider_name == "brave":
            credential = os.environ.get("BRAVE_API_KEY", "")
        elif provider_name == "tavily":
            credential = os.environ.get("TAVILY_API_KEY", "")
        elif provider_name == "searxng":
            credential = os.environ.get("SEARXNG_URL", "")
        else:
            return AgentToolResult(
                content=[{"type": "text", "text": f"Unknown provider: {provider_name}"}]
            )
    else:
        provider_name, credential = _detect_provider()

    if not provider_name or not credential:
        return AgentToolResult(
            content=[
                {
                    "type": "text",
                    "text": (
                        "No search provider configured. Set one of these environment variables:\n"
                        "  BRAVE_API_KEY   — Brave Search API key\n"
                        "  TAVILY_API_KEY  — Tavily Search API key\n"
                        "  SEARXNG_URL     — SearXNG instance URL (e.g. http://localhost:8080)"
                    ),
                }
            ]
        )

    try:
        if provider_name == "brave":
            from pi_web_access.providers.brave import BraveProvider

            provider = BraveProvider(credential)
        elif provider_name == "tavily":
            from pi_web_access.providers.tavily import TavilyProvider

            provider = TavilyProvider(credential)
        elif provider_name == "searxng":
            from pi_web_access.providers.searxng import SearXNGProvider

            provider = SearXNGProvider(credential)
        else:
            return AgentToolResult(
                content=[{"type": "text", "text": f"Unknown provider: {provider_name}"}]
            )

        results = await provider.search(params.query, params.max_results)

        if not results:
            return AgentToolResult(
                content=[{"type": "text", "text": f"No results found for: {params.query}"}]
            )

        lines = [f"Search results for: {params.query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.title}")
            lines.append(f"   {r.url}")
            if r.snippet:
                lines.append(f"   {r.snippet}")
            lines.append("")

        return AgentToolResult(
            content=[{"type": "text", "text": "\n".join(lines)}],
            details={"provider": provider_name, "resultCount": len(results)},
        )
    except Exception as e:
        return AgentToolResult(
            content=[{"type": "text", "text": f"Search failed ({provider_name}): {e}"}]
        )


def create_web_search_tool() -> dict[str, Any]:
    """Return a ToolDefinition-compatible dict for the web_search tool."""
    from pi_agent_core.extensions.types import ToolDefinition

    return ToolDefinition(
        name="web_search",
        description="Search the web for real-time information using Brave, Tavily, or SearXNG",
        parameters=WebSearchParams,
        execute=web_search_execute,
        label="Web Search",
        prompt_snippet="Search the web for real-time information",
        prompt_guidelines=[
            "Use web_search when the user asks about current events, recent news, "
            "or information that may not be in your training data.",
            "Prefer web_search over guessing when you are unsure about facts.",
        ],
    )
