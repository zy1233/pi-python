"""The agent's question about trusting a project, and the one answer the chat gives it.

The agent asks with ``session/request_permission`` on a synthetic tool call
(``pi_agent_cli/trust_prompt.py``): its id starts with ``trust-``, ``rawInput`` has ``project`` and
``resources``, and there are exactly two options, ``dont-trust-project`` (``reject_once``) and
``trust-project`` (``allow_always``). The VS Code extension recognizes the same shape
(``vscode/src/core/chat-controller.ts``).

Trusting a project runs its Python extensions on the machine (Invariant 9), and the question is
built to stay out of channels where a yes is one tap away (audit P7-02). A button in a chat on a
phone is such a channel (audit 2026-11-28, RM-11). So the gateway never shows it: it answers no,
and tells the user to trust the project on the machine. A request that only looks like the
question (the id prefix, but other options) is refused too.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from acp.schema import AllowedOutcome, DeniedOutcome, PermissionOption, RequestPermissionResponse

TRUST_ID_PREFIX = "trust-"
TRUST_REJECT_ID = "dont-trust-project"
TRUST_ACCEPT_ID = "trust-project"


def _raw_input(tool_call: Any) -> Any:
    return getattr(tool_call, "raw_input", None)


def looks_like_trust_request(tool_call: Any) -> bool:
    tool_call_id = getattr(tool_call, "tool_call_id", "") or ""
    raw = _raw_input(tool_call)
    return (
        tool_call_id.startswith(TRUST_ID_PREFIX)
        and isinstance(raw, dict)
        and "project" in raw
        and "resources" in raw
    )


def has_trust_options(options: Sequence[PermissionOption]) -> bool:
    if len(options) != 2:
        return False
    kinds = {option.option_id: option.kind for option in options}
    return (
        kinds.get(TRUST_REJECT_ID) == "reject_once" and kinds.get(TRUST_ACCEPT_ID) == "allow_always"
    )


def is_trust_like(tool_call: Any, options: Sequence[PermissionOption]) -> bool:
    """Anything that could be the trust question, fail closed: the id prefix or the yes option."""
    tool_call_id = getattr(tool_call, "tool_call_id", "") or ""
    return (
        looks_like_trust_request(tool_call)
        or tool_call_id.startswith(TRUST_ID_PREFIX)
        or any(option.option_id == TRUST_ACCEPT_ID for option in options)
    )


def refuse(options: Sequence[PermissionOption]) -> RequestPermissionResponse:
    """The refusal: ``dont-trust-project`` if offered, another reject option, else cancelled."""
    for preferred in (TRUST_REJECT_ID, None):
        for option in options:
            if option.kind.startswith("reject") and (
                preferred is None or option.option_id == preferred
            ):
                return RequestPermissionResponse(
                    outcome=AllowedOutcome(outcome="selected", option_id=option.option_id)
                )
    return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


def project_of(tool_call: Any) -> str:
    raw = _raw_input(tool_call)
    if isinstance(raw, dict) and isinstance(raw.get("project"), str):
        return raw["project"]
    return ""
