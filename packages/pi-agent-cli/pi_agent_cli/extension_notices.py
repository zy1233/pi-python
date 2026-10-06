"""Telling the user that an extension did not load (audit P7-12).

The loader logs a failed extension and carries on with the rest, which is right for the
session and wrong for the user: an ACP client does not show the agent's stderr, so an
extension that raised at startup (a missing dependency, a typo) just seemed not to exist.
``AgentHarness.failed_extensions`` lists them; this module turns that list into the message
the ACP agent and the headless runner show. (Project extensions skipped because the project
is untrusted are a different message: ``extension_trust.untrusted_project_notice``.)
"""

from __future__ import annotations

from collections.abc import Sequence

from pi_agent_core.extensions import FailedExtension

# ``str(exc)`` can be a whole traceback or a screenful of installer output; the log has that.
_MAX_ERROR_CHARS = 200


def failed_extensions_notice(failed: Sequence[FailedExtension]) -> str | None:
    """A message naming each extension that failed to load and why (``None``: none did)."""
    if not failed:
        return None
    count = len(failed)
    head = (
        "1 extension failed to load and was skipped"
        if count == 1
        else f"{count} extensions failed to load and were skipped"
    )
    lines = [f"{head}; the others are unaffected:"]
    for item in failed:
        reason = _one_line(item.error)
        lines.append(f"- {item.name} ({item.source})" + (f": {reason}" if reason else ""))
    lines.append("Details are in the agent's log (stderr).")
    return "\n".join(lines)


def _one_line(text: str) -> str:
    """The first line of *text*, cut to a readable length."""
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    if len(line) > _MAX_ERROR_CHARS:
        line = line[: _MAX_ERROR_CHARS - 3].rstrip() + "..."
    return line
