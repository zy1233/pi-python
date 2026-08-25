"""Map bash/edit/write tool_call hooks to ACP session/request_permission."""

from __future__ import annotations

from typing import Any

from acp.schema import AllowedOutcome, DeniedOutcome, PermissionOption, ToolCallUpdate

from pi_agent_cli.config import PermissionMode
from pi_agent_cli.events import tool_kind

PERMISSION_TOOLS = frozenset({"bash", "edit", "write"})

PERMISSION_OPTIONS = [
    PermissionOption(option_id="allow-once", name="Allow once", kind="allow_once"),
    PermissionOption(option_id="reject-once", name="Reject", kind="reject_once"),
]


def needs_permission(tool_name: str, mode: PermissionMode) -> bool:
    if mode in {"auto", "always-approve"}:
        return False
    return tool_name in PERMISSION_TOOLS


def permission_tool_call(
    tool_call_id: str, tool_name: str, raw_input: dict[str, Any]
) -> ToolCallUpdate:
    return ToolCallUpdate(
        tool_call_id=tool_call_id,
        title=tool_name,
        kind=tool_kind(tool_name),  # type: ignore[arg-type]
        status="pending",
        raw_input=raw_input,
    )


def outcome_allows(outcome: AllowedOutcome | DeniedOutcome | Any) -> bool:
    if isinstance(outcome, DeniedOutcome):
        return False
    if isinstance(outcome, AllowedOutcome):
        return not str(outcome.option_id).startswith("reject")
    if isinstance(outcome, dict):
        if outcome.get("outcome") == "cancelled":
            return False
        option_id = str(outcome.get("optionId") or outcome.get("option_id") or "")
        return not option_id.startswith("reject")
    option_id = str(getattr(outcome, "option_id", "") or "")
    kind = str(getattr(outcome, "outcome", "") or "")
    if kind == "cancelled":
        return False
    return not option_id.startswith("reject")
