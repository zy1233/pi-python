"""`~/.pi-python/agent.toml` (Python ACP agent). Override with PI_HOME.

Do not put Python agent settings in ``config.toml`` — the Rust TUI parses that
file as grok-shell config and will fail on keys like ``permission = \"ask\"``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
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


def load_local_env(home: Path | str | None = None) -> None:
    """Load ``~/.pi-python/local.env`` (KEY=value) without overwriting existing env."""
    path = pi_home(home) / "local.env"
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def agent_config_path(home: Path | str | None = None) -> Path:
    return pi_home(home) / "agent.toml"


def legacy_config_path(home: Path | str | None = None) -> Path:
    """Legacy path; breaks the Rust TUI if it contains Python-only keys."""
    return pi_home(home) / "config.toml"


def expand_config_path(raw: str, *, cwd: str | Path) -> str:
    """Expand ~ and resolve relative skill/config paths against cwd."""
    expanded = os.path.expanduser(raw)
    path = Path(expanded)
    if not path.is_absolute():
        path = Path(cwd) / path
    return str(path.resolve())


@dataclass(frozen=True)
class ModelChoice:
    """One selectable model (the ``/model`` picker; ACP session config option ``model``).

    In ``[[models]]`` entries unset fields inherit from the default ``[model]`` table, but
    ``base_url`` / ``api_key_env`` / ``supports_images`` are only inherited when ``provider``
    is unset or equals the default provider (credentials never leak across providers).
    """

    id: str
    name: str | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    supports_images: bool | None = None


@dataclass(frozen=True)
class CliConfig:
    permission: PermissionMode = "ask"
    provider: str = "mock"
    model_id: str = "mock"
    base_url: str | None = None
    thinking_level: ThinkingLevel = "off"
    max_turns: int | None = None
    api_key_env: str | None = None
    supports_images: bool | None = None
    skills_dirs: tuple[str, ...] = ()
    agent_command: str | None = None
    no_context_files: bool = False
    custom_system_prompt: str | None = None
    custom_system_prompt_file: str | None = None
    append_system_prompt: str | None = None
    append_system_prompt_file: str | None = None
    git_enabled: bool = True
    git_timeout_seconds: float = 2.0
    git_max_status_lines: int = 40
    models: tuple[ModelChoice, ...] = ()

    def default_choice(self) -> ModelChoice:
        """The ``[model]`` table as a fully-resolved choice."""
        return ModelChoice(
            id=self.model_id,
            provider=self.provider,
            base_url=self.base_url,
            api_key_env=self.api_key_env,
            supports_images=self.supports_images,
        )

    def model_choices(self) -> tuple[ModelChoice, ...]:
        """Selectable models: the default first, then ``[[models]]`` (resolved, unique by id)."""
        out = [self.default_choice()]
        seen = {self.model_id}
        for item in self.models:
            if item.id in seen:
                continue
            seen.add(item.id)
            same_provider = item.provider is None or item.provider == self.provider
            out.append(
                ModelChoice(
                    id=item.id,
                    name=item.name,
                    provider=item.provider or self.provider,
                    base_url=item.base_url
                    if item.base_url is not None or not same_provider
                    else self.base_url,
                    api_key_env=item.api_key_env
                    if item.api_key_env is not None or not same_provider
                    else self.api_key_env,
                    supports_images=item.supports_images
                    if item.supports_images is not None or not same_provider
                    else self.supports_images,
                )
            )
        return tuple(out)


def load_config(home: Path | str | None = None) -> CliConfig:
    import tomllib

    agent_path = agent_config_path(home)
    if agent_path.is_file():
        data = tomllib.loads(agent_path.read_text(encoding="utf-8"))
        return _from_toml(data)
    legacy = legacy_config_path(home)
    if legacy.is_file():
        data = tomllib.loads(legacy.read_text(encoding="utf-8"))
        return _from_toml(data)
    return CliConfig()


def api_key_getter(env_name: str | None) -> Callable[[str], str | None] | None:
    """``get_api_key`` hook reading ``env_name`` from the environment (``None`` when unset)."""
    if not env_name:
        return None

    def get_api_key(_provider: str) -> str | None:
        return os.environ.get(env_name) or None

    return get_api_key


def make_get_api_key(
    config: CliConfig,
) -> Callable[[str], str | None] | None:
    return api_key_getter(config.api_key_env)


def _from_toml(data: dict[str, Any]) -> CliConfig:
    model = data.get("model") if isinstance(data.get("model"), dict) else {}
    skills = data.get("skills") if isinstance(data.get("skills"), dict) else {}
    agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
    prompt = data.get("prompt") if isinstance(data.get("prompt"), dict) else {}
    git = data.get("git") if isinstance(data.get("git"), dict) else {}

    permission = data.get("permission", "ask")
    if permission not in _VALID_PERMISSION:
        permission = "ask"
    thinking = data.get("thinking_level", "off")
    if thinking not in _VALID_THINKING:
        thinking = "off"
    max_turns = data.get("max_turns")
    if max_turns is not None:
        max_turns = int(max_turns)

    raw_paths = skills.get("paths") or skills.get("dirs") or []
    skills_dirs: tuple[str, ...] = ()
    if isinstance(raw_paths, list):
        skills_dirs = tuple(str(item) for item in raw_paths if str(item).strip())

    supports_images_raw = model.get("supports_images")
    if supports_images_raw is None:
        supports_images_raw = data.get("supports_images")
    supports_images = bool(supports_images_raw) if supports_images_raw is not None else None

    agent_command = agent.get("command")
    if agent_command is not None:
        agent_command = str(agent_command).strip() or None

    def _optional_str(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    models: list[ModelChoice] = []
    raw_models = data.get("models")
    if isinstance(raw_models, list):
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            model_id = _optional_str(item.get("id") or item.get("model_id"))
            if model_id is None:
                continue
            images = item.get("supports_images")
            models.append(
                ModelChoice(
                    id=model_id,
                    name=_optional_str(item.get("name")),
                    provider=_optional_str(item.get("provider")),
                    base_url=_optional_str(item.get("base_url")),
                    api_key_env=_optional_str(item.get("api_key_env")),
                    supports_images=bool(images) if images is not None else None,
                )
            )

    return CliConfig(
        permission=permission,  # type: ignore[arg-type]
        provider=str(model.get("provider") or data.get("provider") or "mock"),
        model_id=str(model.get("id") or model.get("model_id") or data.get("model_id") or "mock"),
        base_url=model.get("base_url") or data.get("base_url"),
        thinking_level=thinking,  # type: ignore[arg-type]
        max_turns=max_turns,
        api_key_env=model.get("api_key_env") or data.get("api_key_env"),
        supports_images=supports_images,
        skills_dirs=skills_dirs,
        agent_command=agent_command,
        no_context_files=bool(prompt.get("no_context_files", False)),
        custom_system_prompt=_optional_str(prompt.get("custom_system_prompt")),
        custom_system_prompt_file=_optional_str(prompt.get("custom_system_prompt_file")),
        append_system_prompt=_optional_str(prompt.get("append_system_prompt")),
        append_system_prompt_file=_optional_str(prompt.get("append_system_prompt_file")),
        git_enabled=_as_bool(git.get("enabled"), True),
        git_timeout_seconds=_as_float(git.get("timeout_seconds"), 2.0),
        git_max_status_lines=_as_int(git.get("max_status_lines"), 40),
        models=tuple(models),
    )


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_float(value: object, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
