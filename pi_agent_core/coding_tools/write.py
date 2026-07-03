"""write tool (port of pi ``write.ts``, minus TUI rendering)."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from pydantic import BaseModel, Field

from pi_agent_core.coding_tools._base import CodingTool, raise_if_aborted
from pi_agent_core.coding_tools.mutation_queue import with_file_mutation_queue
from pi_agent_core.coding_tools.path_utils import resolve_to_cwd
from pi_agent_core.types import AgentTool, AgentToolResult

_DESCRIPTION = (
    "Write content to a file. Creates the file if it doesn't exist, overwrites if it does. "
    "Automatically creates parent directories."
)


class WriteParams(BaseModel):
    path: str = Field(description="Path to the file to write (relative or absolute)")
    content: str = Field(description="Content to write to the file")


def _write_text(absolute_path: str, content: str) -> None:
    # newline="" keeps "\n" as-is on Windows (no CRLF translation), matching
    # pi's byte-for-byte write; also what edit's round-trip relies on.
    with open(absolute_path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def create_write_tool(cwd: str) -> AgentTool:
    """Build a write tool bound to *cwd*."""

    async def execute(
        _tool_call_id: str,
        params: WriteParams,
        signal: Any | None = None,
        _on_update: Any | None = None,
    ) -> AgentToolResult:
        absolute_path = resolve_to_cwd(params.path, cwd)

        async def run() -> AgentToolResult:
            # Abort is observed between operations (never mid-write), keeping
            # the mutation lock held until the current filesystem op settles.
            raise_if_aborted(signal)
            await asyncio.to_thread(os.makedirs, os.path.dirname(absolute_path), exist_ok=True)
            raise_if_aborted(signal)
            await asyncio.to_thread(_write_text, absolute_path, params.content)
            raise_if_aborted(signal)
            n_bytes = len(params.content.encode("utf-8"))
            return AgentToolResult(
                content=[
                    {"type": "text", "text": f"Successfully wrote {n_bytes} bytes to {params.path}"}
                ],
                details=None,
            )

        return await with_file_mutation_queue(absolute_path, run)

    return CodingTool(
        name="write",
        description=_DESCRIPTION,
        label="write",
        parameters=WriteParams,
        execute_fn=execute,
        prompt_snippet="Create or overwrite files",
        prompt_guidelines=["Use write only for new files or complete rewrites."],
    )
