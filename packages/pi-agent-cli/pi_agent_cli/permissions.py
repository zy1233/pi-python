"""Map tool_call hooks to ACP session/request_permission.

In ``ask`` mode a call is put to the user unless the tool says it is harmless. What a tool
says about itself is its ``annotations`` (MCP-style hints, see ``ToolAnnotations``), which
the harness puts on the ``tool_call`` event. There is no list of tool names to keep up to
date: a tool nobody thought of, an extension's say, is asked about until it declares itself.

``workflow`` declares nothing, so starting one is a decision of its own; it fans work out to
sub-agents that can run ``bash`` / ``edit`` / ``write`` themselves. Their tool calls are
asked about individually (the harness bridge's ``tool_call_gate``), not covered by allowing
the workflow.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from acp.schema import AllowedOutcome, DeniedOutcome, PermissionOption, ToolCallUpdate

from pi_agent_cli.config import PermissionMode
from pi_agent_cli.events import tool_kind

PERMISSION_OPTIONS = [
    PermissionOption(option_id="allow-once", name="Allow once", kind="allow_once"),
    PermissionOption(option_id="reject-once", name="Reject", kind="reject_once"),
]


def _declares_itself_harmless(annotations: Any) -> bool:
    """Whether a tool's own hints put it outside the prompt.

    Two things qualify: ``readOnlyHint: true``, or ``destructiveHint: false`` together with
    ``openWorldHint: false`` (a write that stays in the agent's own books, such as the goal
    tools' notes in the session). Each hint has to be a real boolean: ``"true"`` and ``1``
    are not claims to act on. A hint that is missing is not read in the tool's favour (the
    MCP defaults are destructive and open world), and a tool that declares nothing, or
    something that is not a mapping, is asked about.

    The hints are the tool author's word. Built-in and shipped tools are ours; an extension
    is code the user chose to load, and could do worse than mislabel a tool.
    """
    if not isinstance(annotations, Mapping):
        return False
    if annotations.get("readOnlyHint") is True:
        return True
    return annotations.get("destructiveHint") is False and annotations.get("openWorldHint") is False


def needs_permission(mode: PermissionMode, annotations: Mapping[str, Any] | None = None) -> bool:
    """Whether a tool call has to be put to the user.

    ``auto`` and ``always-approve`` never ask. In ``ask`` mode everything is asked about
    except a tool that declares itself harmless (*annotations*, from the ``tool_call``
    event); a tool the session does not know has no annotations and is asked about.
    """
    if mode in {"auto", "always-approve"}:
        return False
    return not _declares_itself_harmless(annotations)


def _prompt_title(tool_name: str, origin: dict[str, Any] | None) -> str:
    """The tool name, plus where a workflow sub-agent's call runs.

    The prompt shows the tool's own input, which for a relative path says nothing about
    the directory it lands in, and a workflow script can point a sub-agent anywhere.
    Only facts from the runtime go into the title: the sub-agent's ``label`` is text the
    script chose, so it stays out.
    """
    if not origin or origin.get("kind") != "subagent":
        return tool_name
    cwd = origin.get("cwd")
    where = f" in {cwd}" if cwd else ""
    return f"{tool_name} (workflow sub-agent{where})"


def permission_tool_call(
    tool_call_id: str,
    tool_name: str,
    raw_input: dict[str, Any],
    origin: dict[str, Any] | None = None,
) -> ToolCallUpdate:
    return ToolCallUpdate(
        tool_call_id=tool_call_id,
        title=_prompt_title(tool_name, origin),
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
