"""pi-web-access — Web search and URL fetching extension for pi-python.

Provides two tools:
  - ``web_search`` — Search the web via Brave, Tavily, or SearXNG
  - ``fetch_url`` — Fetch and extract text from a URL

Install: ``pip install pi-web-access-py``

Configuration (environment variables):
  - ``BRAVE_API_KEY``  — Brave Search API key
  - ``TAVILY_API_KEY`` — Tavily Search API key
  - ``SEARXNG_URL``    — SearXNG instance URL
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pi_agent_core.extensions import ExtensionAPI


def activate(pi: ExtensionAPI) -> None:
    """Extension entry point — called by the ExtensionLoader."""
    from pi_web_access.fetch_url import create_fetch_url_tool
    from pi_web_access.web_search import create_web_search_tool

    pi.register_tool(create_web_search_tool())
    pi.register_tool(create_fetch_url_tool())
