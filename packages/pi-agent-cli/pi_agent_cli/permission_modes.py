"""The permission mode on the ACP wire.

A session has one mode: ``ask``, ``auto`` or ``always-approve``. A client sets and reads it in
these ways:

* The config option ``mode`` (category ``mode``, values ``ask`` and ``auto``): the standard way.
  ``session/set_config_option`` changes it.
* ``modes`` in the response that opens a session, and ``session/set_mode``: for ACP v1 clients
  that show no config options.
* Two items of the TUI pager. They are kept as they are (the design calls them legacy):
  ``_meta.autoMode`` and ``_meta.yoloMode`` in ``session/new``, ``session/load`` and
  ``session/resume``, and the notification ``pi/yolo_mode_changed``. The notification has no
  session id, so it moves every open session.

``always-approve`` is not a choice that a client can make with the standard items (design
decision D6): it is a mode when ``agent.toml`` or one of the legacy items says so. A session in
that mode shows it as a third value of the option, so that the value is one of the listed ones.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from acp.schema import (
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SessionMode,
    SessionModeState,
)

from pi_agent_cli.config import PermissionMode

MODE_CONFIG_ID = "mode"

# What a client may select with the standard items.
SELECTABLE_MODES: tuple[PermissionMode, ...] = ("ask", "auto")

_NAMES: dict[PermissionMode, str] = {
    "ask": "Ask",
    "auto": "Auto",
    "always-approve": "Always approve",
}
_DESCRIPTIONS: dict[PermissionMode, str] = {
    "ask": "Ask before every action that is not read-only.",
    "auto": "Run safe actions. Ask about the rest.",
    "always-approve": "Run every action with no question. Set in agent.toml.",
}
_VALID: frozenset[str] = frozenset(_NAMES)


def _listed(current: PermissionMode) -> list[PermissionMode]:
    modes = list(SELECTABLE_MODES)
    if current not in modes:
        modes.append(current)
    return modes


def mode_config_option(current: PermissionMode) -> SessionConfigOptionSelect:
    """The ``mode`` option of a session in mode *current*."""
    return SessionConfigOptionSelect(
        id=MODE_CONFIG_ID,
        name="Mode",
        description="Which tool calls run with no question.",
        category="mode",
        type="select",
        current_value=current,
        options=[
            SessionConfigSelectOption(
                value=mode, name=_NAMES[mode], description=_DESCRIPTIONS[mode]
            )
            for mode in _listed(current)
        ],
    )


def session_mode_state(current: PermissionMode) -> SessionModeState:
    """The ``modes`` field of a session response, for clients that read it."""
    return SessionModeState(
        current_mode_id=current,
        available_modes=[
            SessionMode(id=mode, name=_NAMES[mode], description=_DESCRIPTIONS[mode])
            for mode in _listed(current)
        ],
    )


def selectable_mode(value: object) -> PermissionMode | None:
    """The mode that a client may select with *value*, or ``None`` (any other value)."""
    if isinstance(value, str) and value in SELECTABLE_MODES:
        return cast(PermissionMode, value)
    return None


def mode_from_session_meta(meta: Mapping[str, Any]) -> PermissionMode | None:
    """The mode that the pager asks for in the ``_meta`` of a session request.

    ``yoloMode: true`` is ``always-approve`` and ``autoMode: true`` is ``auto``; when both are
    true, ``yoloMode`` wins, as it does in the pager. A flag that is false or missing says
    nothing, so the start value (``agent.toml``) stays: the pager sends both flags as false for
    its ``Normal`` mode, and that must not turn a user's own ``always-approve`` into ``ask``.
    """
    if meta.get("yoloMode") is True:
        return "always-approve"
    if meta.get("autoMode") is True:
        return "auto"
    return None


def mode_from_notification(method: str, params: Mapping[str, Any] | None) -> PermissionMode | None:
    """The mode that a ``pi/yolo_mode_changed`` notification carries, or ``None``.

    The TUI sends its changes of the setting as extension notifications. A known suffix of the
    method is enough, and a value that is not one of the three modes is ignored.
    """
    suffix = method.rsplit("/", 1)[-1].strip().lower()
    if suffix not in {"yolo_mode_changed", "permission_mode_changed"}:
        return None
    payload = params or {}
    raw = payload.get("permission_mode")
    if raw is None:
        raw = payload.get("permissionMode")
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().lower()
    if normalized not in _VALID:
        return None
    return cast(PermissionMode, normalized)
