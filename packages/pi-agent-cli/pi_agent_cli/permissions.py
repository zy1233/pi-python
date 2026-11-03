"""Map tool_call hooks to ACP session/request_permission.

In ``ask`` mode a call is put to the user unless the tool says it is harmless. What a tool
says about itself is its ``annotations`` (MCP-style hints, see ``ToolAnnotations``), which
the harness puts on the ``tool_call`` event. There is no list of tool names to keep up to
date: a tool nobody thought of, an extension's say, is asked about until it declares itself.

``auto`` mode starts from the same test: a tool that declares itself harmless is never asked
about. For the other calls the Auto rules decide (``auto_mode``); the calls that they cannot
allow are put to the user in the same way, with the reason in the title of the question.
``always-approve`` asks about nothing.

``workflow`` declares nothing, so starting one is a decision of its own; it fans work out to
sub-agents that can run ``bash`` / ``edit`` / ``write`` themselves. Their tool calls are
asked about individually (the harness bridge's ``tool_call_gate``), not covered by allowing
the workflow.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from acp.schema import AllowedOutcome, DeniedOutcome, PermissionOption, ToolCallUpdate

from pi_agent_cli.auto_reasons import Reason, sentence
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
    """Whether a tool call can be one that the user is asked about.

    ``always-approve`` never asks. In ``ask`` mode everything is asked about except a tool that
    declares itself harmless (*annotations*, from the ``tool_call`` event); a tool the session
    does not know has no annotations and is asked about. ``auto`` starts from the same test:
    ``True`` means that Auto has to decide the call (it may allow it), ``False`` that it is
    allowed with no decision. A tool that declares itself harmless is never asked about in
    ``auto`` either, so Auto never asks more than ``ask`` does.
    """
    if mode == "always-approve":
        return False
    return not _declares_itself_harmless(annotations)


def _prompt_title(
    tool_name: str, origin: dict[str, Any] | None, auto_reason: Reason | None = None
) -> str:
    """The tool name, plus why Auto asks and where a workflow sub-agent's call runs.

    The prompt shows the tool's own input, which for a relative path says nothing about
    the directory it lands in, and a workflow script can point a sub-agent anywhere.
    Only facts from the runtime go into the title: the sub-agent's ``label`` is text the
    script chose, so it stays out. The reason of an Auto question is one of the fixed
    sentences of ``auto_reasons``: no command text, no file name and no model text.
    """
    notes: list[str] = []
    if auto_reason is not None:
        notes.append(f"Auto: {sentence(auto_reason)}")
    if origin and origin.get("kind") == "subagent":
        cwd = origin.get("cwd")
        notes.append(f"workflow sub-agent{f' in {cwd}' if cwd else ''}")
    if not notes:
        return tool_name
    return f"{tool_name} ({'; '.join(notes)})"


def permission_tool_call(
    tool_call_id: str,
    tool_name: str,
    raw_input: dict[str, Any],
    origin: dict[str, Any] | None = None,
    auto_reason: Reason | None = None,
) -> ToolCallUpdate:
    return ToolCallUpdate(
        tool_call_id=tool_call_id,
        title=_prompt_title(tool_name, origin, auto_reason),
        kind=tool_kind(tool_name),  # type: ignore[arg-type]
        status="pending",
        raw_input=raw_input,
    )


def outcome_allows(outcome: AllowedOutcome | DeniedOutcome | Any) -> bool:
    """Whether the user said yes: a selected option whose id is not a ``reject`` one.

    This is not a strict reader like ``trust_prompt.trust_outcome_grants``: the pager adds
    options of its own to a question (``enable-always-approve``) and answers with their ids,
    which are yes. An answer that does not say ``selected`` (a cancelled question), has no
    option id, or has a shape that is not known is no (invariant 9b): an odd answer never
    runs a call.
    """
    if isinstance(outcome, Mapping):
        kind = outcome.get("outcome")
        option_id = outcome.get("optionId", outcome.get("option_id"))
    else:
        kind = getattr(outcome, "outcome", None)
        option_id = getattr(outcome, "option_id", None)
    if kind != "selected" or not isinstance(option_id, str):
        return False
    option_id = option_id.strip().lower()
    return bool(option_id) and not option_id.startswith("reject")
