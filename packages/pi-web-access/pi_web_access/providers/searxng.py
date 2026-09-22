"""SearXNG self-hosted provider."""

from __future__ import annotations

from typing import Any

import httpx

from pi_web_access.providers.brave import SearchResult


class SearXNGProvider:
    """SearXNG self-hosted instance (JSON API)."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        url = f"{self.base_url}/search"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                url,
                params={"q": query, "format": "json", "pageno": 1},
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
