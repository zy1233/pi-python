"""``<home>/chat.toml`` and ``<home>/chat/credentials.toml``.

Only the gateway reads these files; the agent never does. ``<home>`` is the agent's home
(``pi_home()``: ``PI_HOME``, else ``~/.pi-python``), so the gateway and the agents it starts
share sessions, trust decisions and ``agent.toml``.

The credentials are kept apart from ``local.env`` on purpose: the agent loads ``local.env`` into
its own environment when it starts, and the model can then read it with ``bash env``. A file of
credentials that anyone but the user can read is refused, as ssh does with a key.
"""

from __future__ import annotations

import os
import shlex
import stat
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pi_agent_core.home import pi_home

from .texts import LANGUAGES

CONFIG_FILE = "chat.toml"
CREDENTIALS_FILE = Path("chat") / "credentials.toml"

PLATFORM_TYPES = ("feishu",)
FEISHU_DOMAINS = ("feishu", "lark")
START_MODES = ("ask", "auto")
TOOL_OUTPUT = ("none",)
ENV_MODES = ("login-shell", "inherit")

# What each platform needs in credentials.toml.
REQUIRED_CREDENTIALS: dict[str, tuple[str, ...]] = {"feishu": ("app_id", "app_secret")}


class ConfigError(ValueError):
    """The configuration cannot be used. The message says what to change."""


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    path: Path


@dataclass(frozen=True)
class PlatformConfig:
    """One connection to a chat platform (``[[platforms]]``)."""

    id: str
    type: str
    # The tenant (Feishu: ``tenant_key``) whose events are taken. Empty: the tenant of the
    # owner, learned at pairing.
    tenant: str = ""
    domain: str = "feishu"
    # Pins the owner by id. Empty: the owner is whoever pairs (stored in state.sqlite).
    owner: str = ""


@dataclass(frozen=True)
class ChannelConfig:
    """A group chat bound to one project (``[[channels]]``)."""

    platform: str
    chat_id: str
    project: str
    users: frozenset[str] = frozenset()
    approvers: frozenset[str] = frozenset()


@dataclass(frozen=True)
class GatewayConfig:
    home: Path
    projects: tuple[ProjectConfig, ...]
    platforms: tuple[PlatformConfig, ...]
    channels: tuple[ChannelConfig, ...] = ()
    agent_command: tuple[str, ...] = ()
    agent_env: Mapping[str, str] = field(default_factory=dict)
    start_mode: str = "ask"
    allow_auto: bool = False
    approval_timeout: float = 30 * 60.0
    approvals_in_dm: bool = True
    idle_agent_seconds: float = 20 * 60.0
    max_active_turns: int = 3
    show_thinking: bool = False
    tool_output: str = "none"
    env_mode: str = "login-shell"
    language: str = "zh"
    update_interval: float = 1.0
    max_image_bytes: int = 10 * 1024 * 1024

    def project(self, name: str) -> ProjectConfig | None:
        return next((p for p in self.projects if p.name == name), None)

    def platform(self, platform_id: str) -> PlatformConfig | None:
        return next((p for p in self.platforms if p.id == platform_id), None)

    def channel(self, platform_id: str, chat_id: str) -> ChannelConfig | None:
        return next(
            (c for c in self.channels if c.platform == platform_id and c.chat_id == chat_id), None
        )


def config_path(home: Path | str | None = None) -> Path:
    return pi_home(home) / CONFIG_FILE


def credentials_path(home: Path | str | None = None) -> Path:
    return pi_home(home) / CREDENTIALS_FILE


def load_config(home: Path | str | None = None) -> GatewayConfig:
    """Read and check ``<home>/chat.toml``."""
    root = pi_home(home)
    path = root / CONFIG_FILE
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(
            f"{path} is missing. Run `zycode-chat setup`, or write it by hand (see the README)."
        ) from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from None
    return parse_config(data, home=root, source=str(path))


def parse_config(
    data: Mapping[str, Any], *, home: Path, source: str = "chat.toml"
) -> GatewayConfig:
    gateway = _table(data, "gateway", source)
    projects = tuple(_project(item, source) for item in _array(data, "projects", source))
    platforms = tuple(_platform(item, source) for item in _array(data, "platforms", source))
    channels = tuple(_channel(item, source) for item in _array(data, "channels", source))

    start_mode = _str(gateway, "start_mode", "ask", source)
    if start_mode == "always-approve":
        raise ConfigError(
            f'{source}: start_mode = "always-approve" is refused. Nobody is at the machine '
            'when a chat runs the agent, which is when the questions matter. Use "ask" or '
            '"auto".'
        )
    _choice(start_mode, START_MODES, "start_mode", source)
    allow_auto = _bool(gateway, "allow_auto", False, source)
    if start_mode == "auto" and not allow_auto:
        raise ConfigError(f'{source}: start_mode = "auto" needs allow_auto = true.')

    tool_output = _str(gateway, "tool_output", "none", source)
    if tool_output not in TOOL_OUTPUT:
        raise ConfigError(
            f"{source}: tool_output = {tool_output!r} is not supported yet; this version knows "
            'only "none" (tool output is never posted to the chat).'
        )
    env_mode = _choice(_str(gateway, "env", "login-shell", source), ENV_MODES, "env", source)
    language = _choice(_str(gateway, "language", "zh", source), LANGUAGES, "language", source)

    timeout_minutes = _number(gateway, "approval_timeout_minutes", 30, source)
    if not 1 <= timeout_minutes <= 24 * 60:
        raise ConfigError(f"{source}: approval_timeout_minutes must be from 1 to 1440.")
    idle_minutes = _number(gateway, "idle_agent_minutes", 20, source)
    if idle_minutes < 1:
        raise ConfigError(f"{source}: idle_agent_minutes must be at least 1.")
    max_turns = int(_number(gateway, "max_active_turns", 3, source))
    if max_turns < 1:
        raise ConfigError(f"{source}: max_active_turns must be at least 1.")
    interval = _number(gateway, "update_interval_seconds", 1.0, source)
    if not 0.2 <= interval <= 10:
        raise ConfigError(f"{source}: update_interval_seconds must be from 0.2 to 10.")

    config = GatewayConfig(
        home=home,
        projects=projects,
        platforms=platforms,
        channels=channels,
        agent_command=_command(gateway.get("agent_command"), source),
        agent_env=_env_table(gateway.get("agent_env", {}), source),
        start_mode=start_mode,
        allow_auto=allow_auto,
        approval_timeout=timeout_minutes * 60.0,
        approvals_in_dm=_bool(gateway, "approvals_in_dm", True, source),
        idle_agent_seconds=idle_minutes * 60.0,
        max_active_turns=max_turns,
        show_thinking=_bool(gateway, "show_thinking", False, source),
        tool_output=tool_output,
        env_mode=env_mode,
        language=language,
        update_interval=interval,
    )
    _check_references(config, source)
    return config


def load_credentials(
    config: GatewayConfig, *, check_permissions: bool = True
) -> dict[str, dict[str, str]]:
    """The credentials of each configured platform, by platform id."""
    path = config.home / CREDENTIALS_FILE
    if not path.is_file():
        raise ConfigError(f"{path} is missing. Run `zycode-chat setup` to write it.")
    if check_permissions:
        check_private_file(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from None
    out: dict[str, dict[str, str]] = {}
    for platform in config.platforms:
        table = data.get(platform.id)
        if not isinstance(table, dict):
            raise ConfigError(f"{path}: there is no [{platform.id}] table.")
        values = {str(k): v for k, v in table.items() if isinstance(v, str)}
        missing = [key for key in REQUIRED_CREDENTIALS[platform.type] if not values.get(key)]
        if missing:
            raise ConfigError(f"{path}: [{platform.id}] needs {', '.join(missing)}.")
        out[platform.id] = values
    return out


def check_private_file(path: Path) -> None:
    """Refuse a file that a group or other users can read or write (POSIX only)."""
    if sys.platform == "win32":
        return  # ACLs, not mode bits; documented as not checked
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(
            f"{path} can be read by other users (mode {stat.S_IMODE(mode):o}). "
            f"Run `chmod 600 {shlex.quote(str(path))}`."
        )


def write_private_file(path: Path, text: str) -> None:
    """Write *text* to *path* so that only the user can read it (mode 0600 from the start)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    if sys.platform != "win32":
        os.chmod(path, 0o600)


def all_secret_values(credentials: Mapping[str, Mapping[str, str]]) -> list[str]:
    """Every credential value, for the environment filter and the redaction of output."""
    return [value for table in credentials.values() for value in table.values() if value]


# -- parsing helpers ----------------------------------------------------------------------------


def _table(data: Mapping[str, Any], key: str, source: str) -> Mapping[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{source}: [{key}] must be a table.")
    return value


def _array(data: Mapping[str, Any], key: str, source: str) -> list[Mapping[str, Any]]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ConfigError(f"{source}: [[{key}]] must be a list of tables.")
    return value


def _str(table: Mapping[str, Any], key: str, default: str, source: str) -> str:
    value = table.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(f"{source}: {key} must be a string.")
    return value.strip()


def _bool(table: Mapping[str, Any], key: str, default: bool, source: str) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{source}: {key} must be true or false.")
    return value


def _number(table: Mapping[str, Any], key: str, default: float, source: str) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{source}: {key} must be a number.")
    return float(value)


def _choice(value: str, choices: tuple[str, ...], key: str, source: str) -> str:
    if value not in choices:
        raise ConfigError(f"{source}: {key} = {value!r}; choose one of {', '.join(choices)}.")
    return value


def _strings(value: Any, key: str, source: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{source}: {key} must be a list of strings.")
    return frozenset(v.strip() for v in value if v.strip())


def _project(item: Mapping[str, Any], source: str) -> ProjectConfig:
    name = _str(item, "name", "", source)
    raw = _str(item, "path", "", source)
    if not name or not raw:
        raise ConfigError(f"{source}: each [[projects]] needs a name and a path.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ConfigError(
            f"{source}: the path of project {name!r} must be absolute (it is {raw!r}). "
            "The chat can only pick a project by name, never by path."
        )
    if not path.is_dir():
        raise ConfigError(f"{source}: the path of project {name!r} is not a directory: {path}")
    return ProjectConfig(name=name, path=path.resolve())


def _platform(item: Mapping[str, Any], source: str) -> PlatformConfig:
    platform_id = _str(item, "id", "", source)
    kind = _str(item, "type", "", source)
    if not platform_id or not kind:
        raise ConfigError(f"{source}: each [[platforms]] needs an id and a type.")
    _choice(kind, PLATFORM_TYPES, f"type of platform {platform_id!r}", source)
    domain = _choice(
        _str(item, "domain", "feishu", source),
        FEISHU_DOMAINS,
        f"domain of platform {platform_id!r}",
        source,
    )
    return PlatformConfig(
        id=platform_id,
        type=kind,
        tenant=_str(item, "tenant", "", source),
        domain=domain,
        owner=_str(item, "owner", "", source),
    )


def _channel(item: Mapping[str, Any], source: str) -> ChannelConfig:
    platform = _str(item, "platform", "", source)
    chat_id = _str(item, "chat_id", "", source)
    project = _str(item, "project", "", source)
    if not platform or not chat_id or not project:
        raise ConfigError(f"{source}: each [[channels]] needs platform, chat_id and project.")
    return ChannelConfig(
        platform=platform,
        chat_id=chat_id,
        project=project,
        users=_strings(item.get("users"), "users", source),
        approvers=_strings(item.get("approvers"), "approvers", source),
    )


def _command(value: Any, source: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, list) and value and all(isinstance(v, str) and v for v in value):
        return tuple(value)
    raise ConfigError(f"{source}: agent_command must be a program or a list of strings.")


def _env_table(value: Any, source: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise ConfigError(f"{source}: [gateway.agent_env] must map names to strings.")
    return dict(value)


def _check_references(config: GatewayConfig, source: str) -> None:
    if not config.projects:
        raise ConfigError(f"{source}: list at least one [[projects]].")
    if not config.platforms:
        raise ConfigError(f"{source}: list at least one [[platforms]].")
    names = [p.name for p in config.projects]
    if len(names) != len(set(names)):
        raise ConfigError(f"{source}: two projects have the same name.")
    ids = [p.id for p in config.platforms]
    if len(ids) != len(set(ids)):
        raise ConfigError(f"{source}: two platforms have the same id.")
    for channel in config.channels:
        if config.platform(channel.platform) is None:
            raise ConfigError(
                f"{source}: channel {channel.chat_id}: no platform {channel.platform!r}."
            )
        if config.project(channel.project) is None:
            raise ConfigError(
                f"{source}: channel {channel.chat_id}: no project {channel.project!r}."
            )
