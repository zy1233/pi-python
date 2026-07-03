"""ls tool (port of pi ``ls.ts``, minus TUI rendering)."""

from __future__ import annotations

import asyncio
import os
import stat as stat_module
import sys
from typing import Any

from pydantic import BaseModel, Field

from pi_agent_core.coding_tools._base import CodingTool, raise_if_aborted
from pi_agent_core.coding_tools.path_utils import resolve_to_cwd
from pi_agent_core.coding_tools.truncate import (
    DEFAULT_MAX_BYTES,
    format_size,
    truncate_head,
)
from pi_agent_core.types import AgentTool, AgentToolResult

DEFAULT_LS_LIMIT = 500

_DESCRIPTION = (
    "List directory contents. Returns entries sorted alphabetically, with '/' suffix for "
    f"directories. Includes dotfiles. Output is truncated to {DEFAULT_LS_LIMIT} entries or "
    f"{DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first)."
)


class LsParams(BaseModel):
    path: str | None = Field(
        default=None, description="Directory to list (default: current directory)"
    )
    limit: int | None = Field(
        default=None, description="Maximum number of entries to return (default: 500)"
    )


def _scan_directory(dir_path: str, limit: int) -> tuple[list[str], bool]:
    """List entries (case-insensitive sort, '/' suffix for dirs, unstat-able skipped)."""
    entries = sorted(os.listdir(dir_path), key=str.lower)
    results: list[str] = []
    limit_reached = False
    for entry in entries:
        if len(results) >= limit:
            limit_reached = True
            break
        try:
            entry_stat = os.stat(os.path.join(dir_path, entry))
        except OSError:
            continue
        suffix = "/" if stat_module.S_ISDIR(entry_stat.st_mode) else ""
        results.append(entry + suffix)
    return results, limit_reached


def create_ls_tool(cwd: str) -> AgentTool:
    """Build an ls tool bound to *cwd*."""

    async def execute(
        _tool_call_id: str,
        params: LsParams,
        signal: Any | None = None,
        _on_update: Any | None = None,
    ) -> AgentToolResult:
        raise_if_aborted(signal)
        dir_path = resolve_to_cwd(params.path or ".", cwd)
        effective_limit = params.limit if params.limit is not None else DEFAULT_LS_LIMIT

        if not await asyncio.to_thread(os.path.exists, dir_path):
            raise ValueError(f"Path not found: {dir_path}")
        if not await asyncio.to_thread(os.path.isdir, dir_path):
            raise ValueError(f"Not a directory: {dir_path}")
        try:
            results, limit_reached = await asyncio.to_thread(
                _scan_directory, dir_path, effective_limit
            )
        except OSError as e:
            raise ValueError(f"Cannot read directory: {e}") from e
        raise_if_aborted(signal)

        if not results:
            return AgentToolResult(
                content=[{"type": "text", "text": "(empty directory)"}], details=None
            )

        raw_output = "\n".join(results)
        # Byte cap only: the entry limit already capped the row count.
        truncation = truncate_head(raw_output, max_lines=sys.maxsize)
        output = truncation.content
        details: dict[str, Any] = {}
        notices: list[str] = []
        if limit_reached:
            notices.append(
                f"{effective_limit} entries limit reached. Use limit={effective_limit * 2} for more"
            )
            details["entryLimitReached"] = effective_limit
        if truncation.truncated:
            notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
            details["truncation"] = truncation.to_dict()
        if notices:
            output += "\n\n[" + ". ".join(notices) + "]"

        return AgentToolResult(content=[{"type": "text", "text": output}], details=details or None)

    return CodingTool(
        name="ls",
        description=_DESCRIPTION,
        label="ls",
        parameters=LsParams,
        execute_fn=execute,
        prompt_snippet="List directory contents",
    )
