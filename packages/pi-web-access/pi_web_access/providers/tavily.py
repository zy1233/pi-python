"""Tavily Search API provider."""

from __future__ import annotations

from typing import Any

import httpx

from pi_web_access.providers.brave import SearchResult


class TavilyProvider:
    """Tavily Search API (https://api.tavily.com)."""

    BASE_URL = "https://api.tavily.com/search"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                self.BASE_URL,
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "max_results": min(max_results, 20),
                    "include_answer": False,
                },
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()

        results: list[SearchResult] = []
        for item in (data.get("results") or [])[:max_results]:
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                )
            )
        return results
