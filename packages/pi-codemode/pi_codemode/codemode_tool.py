"""The ``codemode`` tool definition and execution wrapper."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from pi_agent_core.extensions.types import ToolDefinition
from pi_agent_core.types import AgentToolResult, ToolAnnotations
from pi_codemode.runtime import CodemodeRuntime
from pi_codemode.settings import CodemodeSettings
from pi_codemode.signatures import render_codemode_description

if TYPE_CHECKING:
    from pi_agent_core.extensions import ExtensionAPI

CODEMODE_PROMPT_SNIPPET = (
    "Execute an async Python script in an isolated sandbox to orchestrate multiple tool "
    "calls, filter outputs locally, and discover deferred tools"
)

CODEMODE_PROMPT_GUIDELINE = (
    "Prefer `codemode` when an operation needs multiple read/search/edit/MCP steps or large "
    "intermediate output filtering so only the final distilled result enters context; "
    "always prefer `read`, `grep`, `find`, `ls` or `bash` directly for trivial single-step checks."
)


class CodemodeParams(BaseModel):
    code: str = Field(
        description=(
            "Async Python script body (no markdown fences). Supports top-level `await`, "
            "`return`, or a trailing expression. Call session tools via `await tools.<name>(...)` "
            "or `await call_tool(name, ...)`, and inspect deferred tools with `search_tools(...)`."
        )
    )
    timeout_ms: int | None = Field(
        default=None,
        ge=100,
        le=600_000,
        description="Optional wall-clock timeout override in milliseconds.",
    )


def create_codemode_tool(
    pi: ExtensionAPI,
    runtime: CodemodeRuntime,
    get_settings: Callable[[], CodemodeSettings],
    *,
    description: str | None = None,
) -> ToolDefinition:
    """Create the ``codemode`` ``ToolDefinition``."""
    settings = get_settings()
    annotations: ToolAnnotations | None = (
        None if settings.require_approval else {"destructiveHint": False, "openWorldHint": False}
    )

    async def execute(
        tool_call_id: str,
        params: CodemodeParams,
        signal: Any | None = None,
        on_update: Any | None = None,
    ) -> AgentToolResult:
        return await runtime.execute(
            tool_call_id,
            params.code,
            timeout_ms=params.timeout_ms,
            signal=signal,
            on_update=on_update,
        )

    return ToolDefinition(
        name="codemode",
        description=description
        or (
            "Execute an async Python script inside an isolated sandbox to orchestrate multiple "
            "tools (`await tools.<name>(...)`, `ALL_TOOLS`, `search_tools()`, `describe_tool()`, "
            "`parallel()`, `text()`, `image()`, `print()`)."
        ),
        parameters=CodemodeParams,
        execute=execute,
        label="Codemode",
        prompt_snippet=CODEMODE_PROMPT_SNIPPET,
        prompt_guidelines=[CODEMODE_PROMPT_GUIDELINE],
        annotations=annotations,
    )


def build_codemode_description(pi: ExtensionAPI) -> str:
    """Render live ``codemode`` description from the current harness tools."""
    return render_codemode_description(pi.get_all_tools())
