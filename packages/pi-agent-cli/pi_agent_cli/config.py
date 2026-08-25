"""`~/.pi-python/config.toml` (override with PI_HOME)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pi_agent_core.types import ThinkingLevel

PermissionMode = Literal["ask", "auto", "always-approve"]

_VALID_PERMISSION: set[str] = {"ask", "auto", "always-approve"}
_VALID_THINKING: set[str] = {"off", "minimal", "low", "medium", "high", "xhigh"}


def pi_home(override: Path | str | None = None) -> Path:
    if override is not None:
        return Path(override).expanduser()
    raw = os.environ.get("PI_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".pi-python"


@dataclass(frozen=True)
class CliConfig:
    permission: PermissionMode = "ask"
    provider: str = "mock"
    model_id: str = "mock"
    base_url: str | None = None
    thinking_level: ThinkingLevel = "off"
    max_turns: int | None = None
    api_key_env: str | None = None


def load_config(home: Path | str | None = None) -> CliConfig:
    path = pi_home(home) / "config.toml"
    if not path.is_file():
        return CliConfig()
    import tomllib

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return _from_toml(data)


def _from_toml(data: dict[str, Any]) -> CliConfig:
    model = data.get("model") if isinstance(data.get("model"), dict) else {}
    permission = data.get("permission", "ask")
    if permission not in _VALID_PERMISSION:
        permission = "ask"
    thinking = data.get("thinking_level", "off")
    if thinking not in _VALID_THINKING:
        thinking = "off"
    max_turns = data.get("max_turns")
    if max_turns is not None:
        max_turns = int(max_turns)
    return CliConfig(
        permission=permission,  # type: ignore[arg-type]
        provider=str(model.get("provider") or data.get("provider") or "mock"),
        model_id=str(model.get("id") or model.get("model_id") or data.get("model_id") or "mock"),
        base_url=model.get("base_url") or data.get("base_url"),
        thinking_level=thinking,  # type: ignore[arg-type]
        max_turns=max_turns,
        api_key_env=model.get("api_key_env") or data.get("api_key_env"),
    )
