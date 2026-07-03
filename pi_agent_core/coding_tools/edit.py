"""edit tool (port of pi ``edit.ts`` + ``edit-diff.ts``, minus TUI preview rendering).

Matching semantics (pi invariants):

- Every ``edits[].oldText`` must match a unique region of the *original* file
  (0 matches -> not-found error, >1 -> duplicate error); matched regions must
  not overlap; all edits are matched first, then applied at once (never
  incrementally).
- Exact match is tried first; if it fails, a fuzzy pass normalizes NFKC,
  per-line trailing whitespace, smart quotes, Unicode dashes and spaces. When
  any edit needs the fuzzy pass, replacements are computed in fuzzy space and
  overlaid line-by-line so untouched lines keep their original bytes.
- Encoding round-trip: strip BOM -> normalize CRLF->LF -> apply -> restore
  line endings and BOM (the model only ever sees LF text).
"""

from __future__ import annotations

import asyncio
import difflib
import errno
import itertools
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from pi_agent_core.coding_tools._base import CodingTool, raise_if_aborted
from pi_agent_core.coding_tools.mutation_queue import with_file_mutation_queue
from pi_agent_core.coding_tools.path_utils import resolve_to_cwd
from pi_agent_core.types import AgentTool, AgentToolResult

_DESCRIPTION = (
    "Edit a single file using exact text replacement. Every edits[].oldText must match a "
    "unique, non-overlapping region of the original file. If two changes affect the same "
    "block or nearby lines, merge them into one edit instead of emitting overlapping edits. "
    "Do not include large unchanged regions just to connect distant changes."
)


class EditReplacement(BaseModel):
    oldText: str = Field(
        description=(
            "Exact text for one targeted replacement. It must be unique in the original file "
            "and must not overlap with any other edits[].oldText in the same call."
        )
    )
    newText: str = Field(description="Replacement text for this targeted edit.")


class EditParams(BaseModel):
    path: str = Field(description="Path to the file to edit (relative or absolute)")
    edits: list[EditReplacement] = Field(
        description=(
            "One or more targeted replacements. Each edit is matched against the original "
            "file, not incrementally. Do not include overlapping or nested edits. If two "
            "changes touch the same block or nearby lines, merge them into one edit instead."
        )
    )


# --- line endings / BOM ---


def detect_line_ending(content: str) -> str:
    """``"\\r\\n"`` when the first newline in *content* is CRLF, else ``"\\n"``."""
    crlf_idx = content.find("\r\n")
    lf_idx = content.find("\n")
    if lf_idx == -1 or crlf_idx == -1:
        return "\n"
    return "\r\n" if crlf_idx < lf_idx else "\n"


def normalize_to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def restore_line_endings(text: str, ending: str) -> str:
    return text.replace("\n", "\r\n") if ending == "\r\n" else text


def strip_bom(content: str) -> tuple[str, str]:
    """Return ``(bom, text)``; the model never sees (or emits) an invisible BOM."""
    if content.startswith("\ufeff"):
        return "\ufeff", content[1:]
    return "", content


# --- fuzzy matching ---

_SMART_SINGLE_QUOTES = re.compile("[\u2018\u2019\u201a\u201b]")
_SMART_DOUBLE_QUOTES = re.compile("[\u201c\u201d\u201e\u201f]")
_UNICODE_DASHES = re.compile("[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]")
_UNICODE_SPACES = re.compile("[\u00a0\u2002-\u200a\u202f\u205f\u3000]")


def normalize_for_fuzzy_match(text: str) -> str:
    """NFKC + strip per-line trailing whitespace + smart quotes/dashes/spaces -> ASCII."""
    text = unicodedata.normalize("NFKC", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _SMART_SINGLE_QUOTES.sub("'", text)
    text = _SMART_DOUBLE_QUOTES.sub('"', text)
    text = _UNICODE_DASHES.sub("-", text)
    return _UNICODE_SPACES.sub(" ", text)


@dataclass
class _FuzzyMatch:
    found: bool
    index: int
    match_length: int
    used_fuzzy_match: bool


def _fuzzy_find_text(content: str, old_text: str) -> _FuzzyMatch:
    """Exact ``find`` first; fall back to searching in fuzzy-normalized space."""
    exact_index = content.find(old_text)
    if exact_index != -1:
        return _FuzzyMatch(True, exact_index, len(old_text), False)

    fuzzy_content = normalize_for_fuzzy_match(content)
    fuzzy_old_text = normalize_for_fuzzy_match(old_text)
    fuzzy_index = fuzzy_content.find(fuzzy_old_text)
    if fuzzy_index == -1:
        return _FuzzyMatch(False, -1, 0, False)
    return _FuzzyMatch(True, fuzzy_index, len(fuzzy_old_text), True)


def _count_occurrences(content: str, old_text: str) -> int:
    """Occurrence count in fuzzy space (pi counts fuzzily even for exact matches)."""
    fuzzy_content = normalize_for_fuzzy_match(content)
    fuzzy_old_text = normalize_for_fuzzy_match(old_text)
    if not fuzzy_old_text:  # JS ``split("")`` degenerate case
        return max(len(fuzzy_content) - 1, 0)
    return len(fuzzy_content.split(fuzzy_old_text)) - 1


# --- replacement application ---


@dataclass
class _MatchedEdit:
    edit_index: int
    match_index: int
    match_length: int
    new_text: str


def _split_lines_with_endings(content: str) -> list[str]:
    return re.findall(r"[^\n]*\n|[^\n]+", content)


def _get_line_spans(content: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    offset = 0
    for line in _split_lines_with_endings(content):
        spans.append((offset, offset + len(line)))
        offset += len(line)
    return spans


def _get_replacement_line_range(
    spans: list[tuple[int, int]], replacement: _MatchedEdit
) -> tuple[int, int]:
    """(start_line, end_line_exclusive) of the lines a replacement touches."""
    replacement_start = replacement.match_index
    replacement_end = replacement.match_index + replacement.match_length

    start_line = -1
    for i, (start, end) in enumerate(spans):
        if start <= replacement_start < end:
            start_line = i
            break
    if start_line == -1:
        raise ValueError("Replacement range is outside the base content.")

    end_line = start_line
    while end_line < len(spans) and spans[end_line][1] < replacement_end:
        end_line += 1
    if end_line >= len(spans):
        raise ValueError("Replacement range is outside the base content.")
    return start_line, end_line + 1


def _apply_replacements(content: str, replacements: list[_MatchedEdit], offset: int = 0) -> str:
    """Apply ascending-sorted replacements in reverse so offsets stay stable."""
    result = content
    for replacement in reversed(replacements):
        index = replacement.match_index - offset
        result = result[:index] + replacement.new_text + result[index + replacement.match_length :]
    return result


def _apply_replacements_preserving_unchanged_lines(
    original_content: str, base_content: str, replacements: list[_MatchedEdit]
) -> str:
    """Overlay fuzzy-space replacements onto the original, line block by line block.

    Replacements were matched against *base_content* (a fuzzy-normalized view of
    *original_content*). Each replacement is widened to the lines it touches;
    those lines are rewritten from the normalized base while every other line
    keeps its original bytes.
    """
    original_lines = _split_lines_with_endings(original_content)
    base_spans = _get_line_spans(base_content)
    if len(original_lines) != len(base_spans):
        raise ValueError(
            "Cannot preserve unchanged lines because the base content has a different line count."
        )

    groups: list[list[Any]] = []  # [start_line, end_line, replacements]
    for replacement in sorted(replacements, key=lambda r: r.match_index):
        start_line, end_line = _get_replacement_line_range(base_spans, replacement)
        if groups and start_line < groups[-1][1]:
            groups[-1][1] = max(groups[-1][1], end_line)
            groups[-1][2].append(replacement)
        else:
            groups.append([start_line, end_line, [replacement]])

    parts: list[str] = []
    line_index = 0
    for start_line, end_line, group_replacements in groups:
        parts.append("".join(original_lines[line_index:start_line]))
        group_start = base_spans[start_line][0]
        group_end = base_spans[end_line - 1][1]
        parts.append(
            _apply_replacements(
                base_content[group_start:group_end], group_replacements, group_start
            )
        )
        line_index = end_line
    parts.append("".join(original_lines[line_index:]))
    return "".join(parts)


# --- error messages (pi wording) ---


def _empty_old_text_error(path: str, edit_index: int, total_edits: int) -> ValueError:
    if total_edits == 1:
        return ValueError(f"oldText must not be empty in {path}.")
    return ValueError(f"edits[{edit_index}].oldText must not be empty in {path}.")


def _not_found_error(path: str, edit_index: int, total_edits: int) -> ValueError:
    if total_edits == 1:
        return ValueError(
            f"Could not find the exact text in {path}. The old text must match exactly "
            "including all whitespace and newlines."
        )
    return ValueError(
        f"Could not find edits[{edit_index}] in {path}. The oldText must match exactly "
        "including all whitespace and newlines."
    )


def _duplicate_error(path: str, edit_index: int, total_edits: int, occurrences: int) -> ValueError:
    if total_edits == 1:
        return ValueError(
            f"Found {occurrences} occurrences of the text in {path}. The text must be "
            "unique. Please provide more context to make it unique."
        )
    return ValueError(
        f"Found {occurrences} occurrences of edits[{edit_index}] in {path}. Each oldText "
        "must be unique. Please provide more context to make it unique."
    )


def _no_change_error(path: str, total_edits: int) -> ValueError:
    if total_edits == 1:
        return ValueError(
            f"No changes made to {path}. The replacement produced identical content. This "
            "might indicate an issue with special characters or the text not existing as "
            "expected."
        )
    return ValueError(f"No changes made to {path}. The replacements produced identical content.")


# --- core algorithm ---


def apply_edits_to_normalized_content(
    normalized_content: str, edits: list[tuple[str, str]], path: str
) -> tuple[str, str]:
    """Apply exact-text replacements to LF-normalized content.

    Returns ``(base_content, new_content)``. All edits are matched against the
    same original content, then applied in reverse offset order.
    """
    normalized_edits = [(normalize_to_lf(old), normalize_to_lf(new)) for old, new in edits]
    total_edits = len(normalized_edits)

    for i, (old_text, _) in enumerate(normalized_edits):
        if not old_text:
            raise _empty_old_text_error(path, i, total_edits)

    initial_matches = [
        _fuzzy_find_text(normalized_content, old_text) for old_text, _ in normalized_edits
    ]
    used_fuzzy_match = any(match.used_fuzzy_match for match in initial_matches)
    replacement_base = (
        normalize_for_fuzzy_match(normalized_content) if used_fuzzy_match else normalized_content
    )

    matched_edits: list[_MatchedEdit] = []
    for i, (old_text, new_text) in enumerate(normalized_edits):
        match = _fuzzy_find_text(replacement_base, old_text)
        if not match.found:
            raise _not_found_error(path, i, total_edits)

        occurrences = _count_occurrences(replacement_base, old_text)
        if occurrences > 1:
            raise _duplicate_error(path, i, total_edits, occurrences)

        matched_edits.append(_MatchedEdit(i, match.index, match.match_length, new_text))

    matched_edits.sort(key=lambda m: m.match_index)
    for previous, current in itertools.pairwise(matched_edits):
        if previous.match_index + previous.match_length > current.match_index:
            raise ValueError(
                f"edits[{previous.edit_index}] and edits[{current.edit_index}] overlap in "
                f"{path}. Merge them into one edit or target disjoint regions."
            )

    new_content = (
        _apply_replacements_preserving_unchanged_lines(
            normalized_content, replacement_base, matched_edits
        )
        if used_fuzzy_match
        else _apply_replacements(replacement_base, matched_edits)
    )

    if normalized_content == new_content:
        raise _no_change_error(path, total_edits)
    return normalized_content, new_content


# --- diff details ---


def generate_unified_patch(
    path: str, old_content: str, new_content: str, context_lines: int = 4
) -> str:
    diff_lines = difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=path,
        tofile=path,
        n=context_lines,
    )
    return "".join(line if line.endswith("\n") else line + "\n" for line in diff_lines)


def _first_changed_line(old_content: str, new_content: str) -> int | None:
    """1-based line number of the first change in the *new* file."""
    matcher = difflib.SequenceMatcher(
        None,
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        autojunk=False,
    )
    for tag, _i1, _i2, j1, _j2 in matcher.get_opcodes():
        if tag != "equal":
            return j1 + 1
    return None


# --- argument tolerance ---


def prepare_edit_arguments(args: Any) -> Any:
    """pi's tolerance for model quirks (Opus/GLM argument shapes).

    - ``edits`` sent as a JSON string -> parsed into an array.
    - Legacy top-level ``oldText``/``newText`` -> appended into ``edits``.
    """
    if not isinstance(args, dict):
        return args
    prepared = dict(args)

    if isinstance(prepared.get("edits"), str):
        try:
            parsed = json.loads(prepared["edits"])
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            prepared["edits"] = parsed

    old_text = prepared.get("oldText")
    new_text = prepared.get("newText")
    if not isinstance(old_text, str) or not isinstance(new_text, str):
        return prepared

    edits = list(prepared["edits"]) if isinstance(prepared.get("edits"), list) else []
    edits.append({"oldText": old_text, "newText": new_text})
    prepared.pop("oldText", None)
    prepared.pop("newText", None)
    prepared["edits"] = edits
    return prepared


# --- tool ---


def _check_access(absolute_path: str) -> None:
    """Raise ``OSError`` (with errno) unless the file exists and is read/writable."""
    if not os.path.exists(absolute_path):
        raise FileNotFoundError(errno.ENOENT, "no such file or directory", absolute_path)
    if not os.access(absolute_path, os.R_OK | os.W_OK):
        raise PermissionError(errno.EACCES, "permission denied", absolute_path)


def _access_error_reason(error: OSError) -> str:
    code = errno.errorcode.get(error.errno) if error.errno is not None else None
    return f"Error code: {code}" if code else str(error)


def _read_bytes(absolute_path: str) -> bytes:
    with open(absolute_path, "rb") as f:
        return f.read()


def _write_text(absolute_path: str, content: str) -> None:
    # newline="" keeps restored CRLF/LF byte-for-byte (no platform translation).
    with open(absolute_path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def create_edit_tool(cwd: str) -> AgentTool:
    """Build an edit tool bound to *cwd*."""

    async def execute(
        _tool_call_id: str,
        params: EditParams,
        signal: Any | None = None,
        _on_update: Any | None = None,
    ) -> AgentToolResult:
        if not params.edits:
            raise ValueError(
                "Edit tool input is invalid. edits must contain at least one replacement."
            )
        absolute_path = resolve_to_cwd(params.path, cwd)
        edits = [(edit.oldText, edit.newText) for edit in params.edits]

        async def run() -> AgentToolResult:
            # Abort is observed after each await (never from a callback), so the
            # mutation lock is held until the in-flight filesystem op settles.
            raise_if_aborted(signal)
            try:
                await asyncio.to_thread(_check_access, absolute_path)
            except OSError as error:
                raise_if_aborted(signal)
                raise ValueError(
                    f"Could not edit file: {params.path}. {_access_error_reason(error)}."
                ) from error
            raise_if_aborted(signal)

            data = await asyncio.to_thread(_read_bytes, absolute_path)
            raise_if_aborted(signal)

            bom, content = strip_bom(data.decode("utf-8", errors="replace"))
            original_ending = detect_line_ending(content)
            normalized_content = normalize_to_lf(content)
            base_content, new_content = apply_edits_to_normalized_content(
                normalized_content, edits, params.path
            )
            raise_if_aborted(signal)

            final_content = bom + restore_line_endings(new_content, original_ending)
            await asyncio.to_thread(_write_text, absolute_path, final_content)
            raise_if_aborted(signal)

            patch = generate_unified_patch(params.path, base_content, new_content)
            details: dict[str, Any] = {"diff": patch, "patch": patch}
            first_changed = _first_changed_line(base_content, new_content)
            if first_changed is not None:
                details["firstChangedLine"] = first_changed
            return AgentToolResult(
                content=[
                    {
                        "type": "text",
                        "text": f"Successfully replaced {len(edits)} block(s) in {params.path}.",
                    }
                ],
                details=details,
            )

        return await with_file_mutation_queue(absolute_path, run)

    return CodingTool(
        name="edit",
        description=_DESCRIPTION,
        label="edit",
        parameters=EditParams,
        execute_fn=execute,
        prepare_arguments=prepare_edit_arguments,
        prompt_snippet=(
            "Make precise file edits with exact text replacement, including multiple "
            "disjoint edits in one call"
        ),
        prompt_guidelines=[
            "Use edit for precise changes (edits[].oldText must match exactly)",
            "When changing multiple separate locations in one file, use one edit call with "
            "multiple entries in edits[] instead of multiple edit calls",
            "Each edits[].oldText is matched against the original file, not after earlier "
            "edits are applied. Do not emit overlapping or nested edits. Merge nearby "
            "changes into one edit.",
            "Keep edits[].oldText as small as possible while still being unique in the "
            "file. Do not pad with large unchanged regions.",
        ],
    )
