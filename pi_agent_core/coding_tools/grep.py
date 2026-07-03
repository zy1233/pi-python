"""grep tool (port of pi ``grep.ts``: ripgrep first, pure-Python fallback).

pi's ``ensureTool`` auto-download is not ported (spec §7): when ``rg`` is not
on PATH the tool falls back to a stdlib walker that prunes
``DEFAULT_IGNORE_DIRS``, sniffs out binary files (NUL byte), and matches with
``re`` line by line. The fallback does not honor .gitignore — a declared
divergence (spec §4.5).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from pi_agent_core.coding_tools._base import CodingTool, raise_if_aborted
from pi_agent_core.coding_tools.path_utils import (
    DEFAULT_IGNORE_DIRS,
    compile_glob,
    resolve_to_cwd,
)
from pi_agent_core.coding_tools.truncate import (
    DEFAULT_MAX_BYTES,
    GREP_MAX_LINE_LENGTH,
    format_size,
    truncate_head,
    truncate_line,
)
from pi_agent_core.types import AgentTool, AgentToolResult

DEFAULT_GREP_LIMIT = 100

# rg --json packs each matched line into one JSON line; allow very long ones.
_RG_STREAM_LIMIT = 8 * 1024 * 1024

_DESCRIPTION = (
    "Search file contents for a pattern. Returns matching lines with file paths and line "
    f"numbers. Respects .gitignore. Output is truncated to {DEFAULT_GREP_LIMIT} matches or "
    f"{DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first). Long lines are truncated to "
    f"{GREP_MAX_LINE_LENGTH} chars."
)


class GrepParams(BaseModel):
    pattern: str = Field(description="Search pattern (regex or literal string)")
    path: str | None = Field(
        default=None, description="Directory or file to search (default: current directory)"
    )
    glob: str | None = Field(
        default=None, description="Filter files by glob pattern, e.g. '*.ts' or '**/*.spec.ts'"
    )
    ignoreCase: bool | None = Field(
        default=None, description="Case-insensitive search (default: false)"
    )
    literal: bool | None = Field(
        default=None,
        description="Treat pattern as literal string instead of regex (default: false)",
    )
    context: int | None = Field(
        default=None,
        description="Number of lines to show before and after each match (default: 0)",
    )
    limit: int | None = Field(
        default=None, description="Maximum number of matches to return (default: 100)"
    )


@dataclass
class _Match:
    file_path: str
    line_number: int
    line_text: str | None


def _is_aborted(signal: Any | None) -> bool:
    return signal is not None and getattr(signal, "aborted", False)


# --- ripgrep path ---


async def _run_rg(
    rg_path: str,
    params: GrepParams,
    search_path: str,
    effective_limit: int,
    signal: Any | None,
) -> tuple[list[_Match], bool]:
    args = [rg_path, "--json", "--line-number", "--color=never", "--hidden"]
    if params.ignoreCase:
        args.append("--ignore-case")
    if params.literal:
        args.append("--fixed-strings")
    if params.glob:
        args.extend(["--glob", params.glob])
    args.extend(["--", params.pattern, search_path])

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_RG_STREAM_LIMIT,
        )
    except OSError as error:
        raise ValueError(f"Failed to run ripgrep: {error}") from error

    assert proc.stdout is not None and proc.stderr is not None
    stderr_task = asyncio.ensure_future(proc.stderr.read())
    matches: list[_Match] = []
    match_count = 0
    match_limit_reached = False
    killed_due_to_limit = False

    try:
        while True:
            if _is_aborted(signal):
                raise RuntimeError("Operation aborted")
            raw_line = await proc.stdout.readline()
            if not raw_line:
                break
            line = raw_line.strip()
            if not line or match_count >= effective_limit:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") != "match":
                continue
            match_count += 1
            data = event.get("data") or {}
            file_path = (data.get("path") or {}).get("text")
            line_number = data.get("line_number")
            line_text = (data.get("lines") or {}).get("text")
            if file_path and isinstance(line_number, int):
                matches.append(_Match(file_path, line_number, line_text))
            if match_count >= effective_limit:
                match_limit_reached = True
                killed_due_to_limit = True
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                break
        stderr_data = await stderr_task
        return_code = await proc.wait()
    except BaseException:
        stderr_task.cancel()
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise

    if _is_aborted(signal):
        raise RuntimeError("Operation aborted")
    if not killed_due_to_limit and return_code not in (0, 1):
        message = stderr_data.decode("utf-8", errors="replace").strip()
        raise ValueError(message or f"ripgrep exited with code {return_code}")
    return matches, match_limit_reached


# --- pure-Python fallback ---


def _iter_candidate_files(search_path: str, is_directory: bool):
    if not is_directory:
        yield search_path
        return
    for root, dirnames, filenames in os.walk(search_path):
        dirnames[:] = sorted(d for d in dirnames if d not in DEFAULT_IGNORE_DIRS)
        for name in sorted(filenames):
            yield os.path.join(root, name)


def _search_fallback(
    params: GrepParams,
    search_path: str,
    is_directory: bool,
    effective_limit: int,
    is_aborted,
) -> tuple[list[_Match], bool]:
    flags = re.IGNORECASE if params.ignoreCase else 0
    pattern = re.escape(params.pattern) if params.literal else params.pattern
    regex = re.compile(pattern, flags)
    glob_filter = compile_glob(params.glob) if params.glob else None

    matches: list[_Match] = []
    for file_path in _iter_candidate_files(search_path, is_directory):
        if is_aborted():
            raise RuntimeError("Operation aborted")
        if glob_filter is not None:
            glob_regex, matches_path = glob_filter
            if is_directory and matches_path:
                candidate = os.path.relpath(file_path, search_path).replace(os.sep, "/")
            else:
                candidate = os.path.basename(file_path)
            if not glob_regex.fullmatch(candidate):
                continue
        try:
            with open(file_path, "rb") as f:
                data = f.read()
        except OSError:
            continue
        if b"\x00" in data[:8192]:  # binary sniff
            continue
        text = data.decode("utf-8", errors="replace")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        for line_number, line in enumerate(normalized.split("\n"), start=1):
            if regex.search(line):
                matches.append(_Match(file_path, line_number, line))
                if len(matches) >= effective_limit:
                    return matches, True
    return matches, False


# --- shared output formatting ---


def _format_matches(
    matches: list[_Match],
    search_path: str,
    is_directory: bool,
    context_value: int,
) -> tuple[list[str], bool]:
    """Render pi's ``path:line: text`` rows (context rows use ``-`` separators)."""
    lines_truncated = False
    output_lines: list[str] = []
    file_cache: dict[str, list[str]] = {}

    def format_path(file_path: str) -> str:
        if is_directory:
            relative = os.path.relpath(file_path, search_path)
            if relative not in ("", ".") and not relative.startswith(".."):
                return relative.replace(os.sep, "/").replace("\\", "/")
        return os.path.basename(file_path)

    def get_file_lines(file_path: str) -> list[str]:
        lines = file_cache.get(file_path)
        if lines is None:
            try:
                with open(file_path, "rb") as f:
                    content = f.read().decode("utf-8", errors="replace")
                lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            except OSError:
                lines = []
            file_cache[file_path] = lines
        return lines

    for match in matches:
        relative_path = format_path(match.file_path)
        if context_value == 0 and match.line_text is not None:
            sanitized = match.line_text.replace("\r\n", "\n").replace("\r", "")
            sanitized = sanitized.removesuffix("\n")
            truncated_text, was_truncated = truncate_line(sanitized)
            if was_truncated:
                lines_truncated = True
            output_lines.append(f"{relative_path}:{match.line_number}: {truncated_text}")
            continue

        lines = get_file_lines(match.file_path)
        if not lines:
            output_lines.append(f"{relative_path}:{match.line_number}: (unable to read file)")
            continue
        start = max(1, match.line_number - context_value) if context_value else match.line_number
        end = (
            min(len(lines), match.line_number + context_value)
            if context_value
            else match.line_number
        )
        for current in range(start, end + 1):
            line_text = lines[current - 1] if current - 1 < len(lines) else ""
            truncated_text, was_truncated = truncate_line(line_text.replace("\r", ""))
            if was_truncated:
                lines_truncated = True
            if current == match.line_number:
                output_lines.append(f"{relative_path}:{current}: {truncated_text}")
            else:
                output_lines.append(f"{relative_path}-{current}- {truncated_text}")

    return output_lines, lines_truncated


def create_grep_tool(cwd: str, *, use_fallback: bool = False) -> AgentTool:
    """Build a grep tool bound to *cwd*.

    ``use_fallback=True`` forces the pure-Python path (used by tests; also the
    automatic behavior whenever ``rg`` is not on PATH).
    """

    async def execute(
        _tool_call_id: str,
        params: GrepParams,
        signal: Any | None = None,
        _on_update: Any | None = None,
    ) -> AgentToolResult:
        raise_if_aborted(signal)
        search_path = resolve_to_cwd(params.path or ".", cwd)
        if not await asyncio.to_thread(os.path.exists, search_path):
            raise ValueError(f"Path not found: {search_path}")
        is_directory = await asyncio.to_thread(os.path.isdir, search_path)

        context_value = params.context if params.context and params.context > 0 else 0
        effective_limit = max(1, params.limit if params.limit is not None else DEFAULT_GREP_LIMIT)

        rg_path = None if use_fallback else shutil.which("rg")
        if rg_path:
            matches, match_limit_reached = await _run_rg(
                rg_path, params, search_path, effective_limit, signal
            )
        else:
            matches, match_limit_reached = await asyncio.to_thread(
                _search_fallback,
                params,
                search_path,
                is_directory,
                effective_limit,
                lambda: _is_aborted(signal),
            )
        raise_if_aborted(signal)

        if not matches:
            return AgentToolResult(
                content=[{"type": "text", "text": "No matches found"}], details=None
            )

        output_lines, lines_truncated = await asyncio.to_thread(
            _format_matches, matches, search_path, is_directory, context_value
        )

        raw_output = "\n".join(output_lines)
        # Byte cap only: the match limit already capped the row count.
        truncation = truncate_head(raw_output, max_lines=sys.maxsize)
        output = truncation.content
        details: dict[str, Any] = {}
        notices: list[str] = []
        if match_limit_reached:
            notices.append(
                f"{effective_limit} matches limit reached. "
                f"Use limit={effective_limit * 2} for more, or refine pattern"
            )
            details["matchLimitReached"] = effective_limit
        if truncation.truncated:
            notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
            details["truncation"] = truncation.to_dict()
        if lines_truncated:
            notices.append(
                f"Some lines truncated to {GREP_MAX_LINE_LENGTH} chars. "
                "Use read tool to see full lines"
            )
            details["linesTruncated"] = True
        if notices:
            output += "\n\n[" + ". ".join(notices) + "]"

        return AgentToolResult(content=[{"type": "text", "text": output}], details=details or None)

    return CodingTool(
        name="grep",
        description=_DESCRIPTION,
        label="grep",
        parameters=GrepParams,
        execute_fn=execute,
        prompt_snippet="Search file contents for patterns (respects .gitignore)",
    )
