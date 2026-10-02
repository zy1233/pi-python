"""Live provider rows for the Phase 6 production matrix.

A row is skipped when its API key environment variable is empty. Model id and
base URL can be overridden without editing this table.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderRow:
    id: str
    provider: str
    model_id: str
    api_key_env: str
    capabilities: tuple[str, ...]
    base_url: str | None = None
    base_url_env: str | None = None
    require_thinking: bool = False


def _env(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


MATRIX: tuple[ProviderRow, ...] = (
    ProviderRow(
        id="openai",
        provider="openai",
        model_id="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        capabilities=("text", "tools", "usage", "abort"),
    ),
    ProviderRow(
        id="anthropic",
        provider="anthropic",
        model_id="claude-haiku-4-5",
        api_key_env="ANTHROPIC_API_KEY",
        capabilities=("text", "tools", "usage", "thinking", "abort"),
    ),
    ProviderRow(
        id="deepseek",
        provider="deepseek",
        model_id="deepseek-v4-flash",
        api_key_env="DEEPSEEK_API_KEY",
        capabilities=("text", "tools", "usage", "thinking", "thinking_tools", "abort"),
        require_thinking=True,
    ),
    ProviderRow(
        id="siliconflow",
        provider="deepseek",
        model_id="deepseek-ai/DeepSeek-V4-Flash",
        api_key_env="REAL_LLM_API_KEY",
        capabilities=("text", "tools", "usage", "thinking", "abort"),
        base_url="https://api.siliconflow.cn/v1",
        base_url_env="REAL_LLM_BASE_URL",
        require_thinking=True,
    ),
)


def resolve_row(row: ProviderRow) -> tuple[str, str, str | None]:
    """Return ``(api_key, model_id, base_url)`` after environment overrides."""
    api_key = os.environ.get(row.api_key_env, "").strip()
    model_id = _env(f"MATRIX_{row.id.upper()}_MODEL", row.model_id)
    if row.base_url_env:
        base_url = os.environ.get(row.base_url_env, "").strip() or row.base_url
    else:
        base_url = os.environ.get(f"MATRIX_{row.id.upper()}_BASE_URL", "").strip() or row.base_url
    return api_key, model_id, base_url
