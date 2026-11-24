"""The ``tool_search`` tool definition for discovering and activating deferred tools."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from pi_agent_core.extensions.types import ToolDefinition
from pi_agent_core.types import AgentToolResult
from pi_codemode.signatures import FORBIDDEN_INNER_TOOLS, render_python_signature

if TYPE_CHECKING:
    from pi_agent_core.extensions import ExtensionAPI

TOOL_SEARCH_PROMPT_SNIPPET = (
    "Search deferred or registered tools by keyword or /regex/ and optionally load them "
    "into the active tool list"
)


class ToolSearchParams(BaseModel):
    query: str = Field(
        default="*",
        description=(
            "Search query: space-separated keywords (AND match), `/regex/`, or `*` to list all "
            "deferred tools."
        ),
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of matching tools to return.",
    )
    activate: bool = Field(
        default=True,
        description=(
            "When true, promote matching deferred tools into the session's active tools so they "
            "can be called directly on subsequent turns."
        ),
    )


def create_tool_search_tool(pi: ExtensionAPI) -> ToolDefinition:
    """Create the ``tool_search`` ``ToolDefinition``."""

    async def execute(
        tool_call_id: str,
        params: ToolSearchParams,
        signal: Any | None = None,
        on_update: Any | None = None,
    ) -> AgentToolResult:
        tools = [t for t in pi.get_all_tools() if t.name not in FORBIDDEN_INNER_TOOLS]
        q = params.query.strip()
        pattern: re.Pattern[str] | None = None
        if len(q) >= 2 and q.startswith("/") and q.endswith("/"):
            try:
                pattern = re.compile(q[1:-1], re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"Invalid regular expression {q!r}: {exc}") from exc
        tokens = [] if (not q or q == "*" or pattern is not None) else q.lower().split()

        matches = []
        for info in tools:
            haystack = f"{info.name} {info.description or ''}"
            if not q or q == "*":
                ok = bool(info.deferred)
            elif pattern is not None:
                ok = bool(pattern.search(haystack))
            else:
                low = haystack.lower()
                ok = all(tok in low for tok in tokens)
            if ok:
                matches.append(info)
            if len(matches) >= params.limit:
                break

        activated: list[str] = []
        if params.activate and matches:
            to_activate = [m.name for m in matches if not m.active]
            if to_activate:
                activated = pi.activate_tools(to_activate)

        if not matches:
            text = f"No tools matched query {params.query!r}."
        else:
            lines = [f"Matched {len(matches)} tool(s):"]
            for m in matches:
                sig = render_python_signature(
                    m.name,
                    m.parameters,
                    read_only=bool(m.annotations and m.annotations.get("readOnlyHint") is True),
                )
                status = "active" if (m.active or m.name in activated) else "deferred"
                desc = " ".join((m.description or "").split())
                if len(desc) > 140:
                    desc = desc[:137].rstrip() + "..."
                lines.append(f"- `{m.name}` [{status}]: {sig} — {desc}")
            if activated:
                lines.append(f"\nActivated for direct calling: {', '.join(activated)}")
            text = "\n".join(lines)

        return AgentToolResult(
            content=[{"type": "text", "text": text}],
            details={
                "matched": [m.name for m in matches],
                "activated": activated,
            },
        )

    return ToolDefinition(
        name="tool_search",
        description=(
            "Discover deferred or registered tools (such as MCP tools) by keyword or /regex/, "
            "optionally activating them for direct tool calling."
        ),
        parameters=ToolSearchParams,
        execute=execute,
        label="Tool Search",
        prompt_snippet=TOOL_SEARCH_PROMPT_SNIPPET,
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    )
