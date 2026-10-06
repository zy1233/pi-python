"""The question the user is asked about a project, and how their answer is read (audit P7-02).

It goes out as an ACP ``session/request_permission`` on a synthetic tool call, the one
interactive channel every client has. That channel is built for tool permissions, and clients
are allowed to answer it *for* the user (the spec lets a client auto-approve; the TUI's YOLO
mode picks the first ``allow_once`` option). Whether to run a repository's code is not a
decision to leave to a mode that was switched on for tool calls, so this question is shaped
to fall outside it:

- there is no ``allow_once`` option, only ``allow_always`` ("trust, and remember it") and
  ``reject_once`` ("don't trust"), so a client that auto-picks the first ``allow_once`` finds
  nothing and hands the question to a person;
- the refusal is listed first, so a client that picks the first option says no;
- only the exact id of the trust option, reported as selected, is a yes. The tool-permission
  reader (``permissions.outcome_allows``) treats every id that does not start with "reject" as
  a yes; that is wrong here, where an unrecognised answer must mean no.

What is being asked is not part of that call. The TUI draws a permission request's title and
options and nothing of its content, so the explanation (``trust_explanation``) goes out just
before, as an ordinary agent message every client shows. It quotes names the repository being
judged chose, so they are escaped and set in code spans: a file name must not be able to add
lines of its own to what the user reads before deciding.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from acp.schema import AllowedOutcome, PermissionOption, ToolCallUpdate

from pi_agent_cli.extension_trust import ProjectTrustDecision
from pi_agent_cli.trust_fingerprint import GatedResource

TRUST_OPTION_ID = "trust-project"
REJECT_OPTION_ID = "dont-trust-project"

# Reads after the TUI's "Allow {title}?".
_TITLE_NOUN = {"extensions": "extensions", "prompt": "prompt files", "skills": "skills"}
_WHAT_IT_DOES = {
    "extensions": "runs Python code as soon as the session opens",
    "prompt": "changes what the model is told",
    "skills": "adds instructions (and possibly scripts to run) to what the model is told",
}
_CLOSING = (
    'Trust it only if you trust the people who wrote these files. "Trust and remember" '
    "keeps the answer for this directory until one of these files changes; then you are "
    "asked again."
)
# A repository can hold thousands of extension files. Past this many characters of their
# names, the list is cut short (and says so).
_DETAIL_LIMIT = 300
_BACKTICKS = re.compile(r"`+")


def trust_options() -> list[PermissionOption]:
    return [
        PermissionOption(option_id=REJECT_OPTION_ID, name="Don't trust", kind="reject_once"),
        PermissionOption(option_id=TRUST_OPTION_ID, name="Trust and remember", kind="allow_always"),
    ]


def trust_tool_call(project: str | Path, decision: ProjectTrustDecision) -> ToolCallUpdate:
    return ToolCallUpdate(
        tool_call_id=f"trust-{uuid.uuid4().hex}",
        title=_title(decision),
        kind="other",
        status="pending",
        # No content (see the module docstring): the explanation is a message of its own, and
        # content as well would repeat it in a client that shows both. Deliberately none of the
        # raw_input keys the TUI derives a meaning from either (command, file_path...).
        raw_input={
            "project": str(project),
            "resources": [resource.describe() for resource in decision.resources],
            "changed": decision.reason == "changed",
        },
    )


def trust_explanation(project: str | Path, decision: ProjectTrustDecision) -> str:
    """What the user is told before the question: where the project is, what it ships, what
    each of those can do, and what "remember" means. Markdown, in paragraphs."""
    if decision.reason == "changed":
        opening = (
            "This project was trusted before, but its files have changed since you last trusted it."
        )
    else:
        opening = "This project wants to load resources of its own."
    bullets = "\n".join(f"- {_bullet(resource)}" for resource in decision.resources)
    return "\n\n".join([opening, f"Project: {_code(str(project))}", bullets, _CLOSING])


def trust_outcome_grants(outcome: Any) -> bool:
    """Did the user choose to trust the project? Only the trust option, selected, says yes."""
    if isinstance(outcome, AllowedOutcome):
        return outcome.outcome == "selected" and outcome.option_id == TRUST_OPTION_ID
    if isinstance(outcome, dict):
        option_id = outcome.get("optionId", outcome.get("option_id"))
        return outcome.get("outcome") == "selected" and option_id == TRUST_OPTION_ID
    return False


def _title(decision: ProjectTrustDecision) -> str:
    kinds: list[str] = []
    for resource in decision.resources:
        noun = _TITLE_NOUN[resource.kind]
        if noun not in kinds:
            kinds.append(noun)
    if len(kinds) > 1:
        listed = f"{', '.join(kinds[:-1])} and {kinds[-1]}"
    else:
        listed = kinds[0] if kinds else "resources"
    return f"loading this project's {listed}"


def _bullet(resource: GatedResource) -> str:
    text = f"{resource.kind}: {_code(resource.label)}"
    if resource.detail:
        text += f" ({_code(resource.detail, limit=_DETAIL_LIMIT)})"
    return f"{text} - {_WHAT_IT_DOES[resource.kind]}"


def _code(text: str, *, limit: int | None = None) -> str:
    """*text* as an inline code span, whatever it holds.

    What is not printable (a newline, an escape sequence, a direction override) is written
    out as an escape (``\\n``, ``\\x1b``), so it can neither break the line nor act on the
    display. The fence is one backtick longer than any run inside, so the text cannot close
    it, and a text that starts or ends with a backtick is padded to keep the fence apart.
    """
    shown = "".join(
        char if char.isprintable() else char.encode("unicode_escape").decode("ascii")
        for char in text
    )
    if limit is not None and len(shown) > limit:
        shown = shown[:limit] + "…"
    longest = max((len(run) for run in _BACKTICKS.findall(shown)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if shown.startswith("`") or shown.endswith("`") else ""
    return f"{fence}{pad}{shown}{pad}{fence}"
