"""Brave Search API provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class BraveProvider:
    """Brave Search API (https://api.search.brave.com)."""

    BASE_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                self.BASE_URL,
                params={"q": query, "count": min(max_results, 20)},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": self.api_key,
                },
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()

        results: list[SearchResult] = []
        for item in (data.get("web", {}).get("results") or [])[:max_results]:
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", ""),
                )
            )
        return results
