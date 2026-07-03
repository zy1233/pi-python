"""read tool (port of pi ``read.ts``, minus TUI rendering and image resizing)."""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from pydantic import BaseModel, Field

from pi_agent_core.coding_tools._base import CodingTool, raise_if_aborted
from pi_agent_core.coding_tools.path_utils import detect_image_mime, resolve_to_cwd
from pi_agent_core.coding_tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    format_size,
    truncate_head,
)
from pi_agent_core.messages import ImageContent, TextContent
from pi_agent_core.types import AgentTool, AgentToolResult

_DESCRIPTION = (
    "Read the contents of a file. Supports text files and images (jpg, png, gif, webp, bmp). "
    "Images are sent as attachments. For text files, output is truncated to "
    f"{DEFAULT_MAX_LINES} lines or {DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first). "
    "Use offset/limit for large files. When you need the full file, continue with offset "
    "until complete."
)


class ReadParams(BaseModel):
    path: str = Field(description="Path to the file to read (relative or absolute)")
    offset: int | None = Field(
        default=None, description="Line number to start reading from (1-indexed)"
    )
    limit: int | None = Field(default=None, description="Maximum number of lines to read")


def _read_bytes(absolute_path: str) -> bytes:
    with open(absolute_path, "rb") as f:
        return f.read()


def _read_text_output(text: str, params: ReadParams) -> tuple[str, dict[str, Any] | None]:
    """Select offset/limit lines, truncate, and build pi's continuation notices."""
    all_lines = text.split("\n")  # raw split: a trailing newline counts one more line (pi parity)
    total_file_lines = len(all_lines)

    start_line = max(0, params.offset - 1) if params.offset else 0
    start_line_display = start_line + 1
    if start_line >= len(all_lines):
        raise ValueError(
            f"Offset {params.offset} is beyond end of file ({len(all_lines)} lines total)"
        )

    user_limited_lines: int | None = None
    if params.limit is not None:
        end_line = min(start_line + params.limit, len(all_lines))
        selected = "\n".join(all_lines[start_line:end_line])
        user_limited_lines = end_line - start_line
    else:
        selected = "\n".join(all_lines[start_line:])

    truncation = truncate_head(selected)

    if truncation.first_line_exceeds_limit:
        # The single line cannot be shown at all; point the model at a bash fallback.
        first_line_size = format_size(len(all_lines[start_line].encode("utf-8")))
        output = (
            f"[Line {start_line_display} is {first_line_size}, exceeds "
            f"{format_size(DEFAULT_MAX_BYTES)} limit. "
            f"Use bash: sed -n '{start_line_display}p' {params.path} "
            f"| head -c {DEFAULT_MAX_BYTES}]"
        )
        return output, {"truncation": truncation.to_dict()}

    if truncation.truncated:
        end_line_display = start_line_display + truncation.output_lines - 1
        next_offset = end_line_display + 1
        if truncation.truncated_by == "lines":
            notice = (
                f"[Showing lines {start_line_display}-{end_line_display} of "
                f"{total_file_lines}. Use offset={next_offset} to continue.]"
            )
        else:
            notice = (
                f"[Showing lines {start_line_display}-{end_line_display} of "
                f"{total_file_lines} ({format_size(DEFAULT_MAX_BYTES)} limit). "
                f"Use offset={next_offset} to continue.]"
            )
        return f"{truncation.content}\n\n{notice}", {"truncation": truncation.to_dict()}

    if user_limited_lines is not None and start_line + user_limited_lines < len(all_lines):
        # The user-specified limit stopped early but the file has more content.
        remaining = len(all_lines) - (start_line + user_limited_lines)
        next_offset = start_line + user_limited_lines + 1
        notice = f"[{remaining} more lines in file. Use offset={next_offset} to continue.]"
        return f"{truncation.content}\n\n{notice}", None

    return truncation.content, None


def create_read_tool(cwd: str) -> AgentTool:
    """Build a read tool bound to *cwd*."""

    async def execute(
        _tool_call_id: str,
        params: ReadParams,
        signal: Any | None = None,
        _on_update: Any | None = None,
    ) -> AgentToolResult:
        raise_if_aborted(signal)
        absolute_path = resolve_to_cwd(params.path, cwd)
        data = await asyncio.to_thread(_read_bytes, absolute_path)
        raise_if_aborted(signal)

        mime_type = detect_image_mime(data)
        if mime_type:
            image: ImageContent = {
                "type": "image",
                "data": base64.b64encode(data).decode("ascii"),
                "mimeType": mime_type,
            }
            note: TextContent = {"type": "text", "text": f"Read image file [{mime_type}]"}
            # Non-vision models: image stripping happens in the convert layer (C1).
            return AgentToolResult(content=[note, image], details=None)

        output, details = _read_text_output(data.decode("utf-8", errors="replace"), params)
        return AgentToolResult(content=[{"type": "text", "text": output}], details=details)

    return CodingTool(
        name="read",
        description=_DESCRIPTION,
        label="read",
        parameters=ReadParams,
        execute_fn=execute,
        prompt_snippet="Read file contents",
        prompt_guidelines=["Use read to examine files instead of cat or sed."],
    )
