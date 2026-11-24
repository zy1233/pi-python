"""Settings for ``pi-codemode`` (passed via ``AgentHarness.set_extension_settings``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

CodemodeMode = Literal["hybrid", "only"]

DEFAULT_TIMEOUT_SECONDS = 120.0
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 600.0
DEFAULT_MAX_TOOL_CALLS = 100
MIN_MAX_TOOL_CALLS = 1
MAX_MAX_TOOL_CALLS = 500
DEFAULT_MAX_OUTPUT_KB = 64
MIN_MAX_OUTPUT_KB = 4
MAX_MAX_OUTPUT_KB = 512


@dataclass(frozen=True)
class CodemodeSettings:
    enabled: bool = True
    mode: CodemodeMode = "hybrid"
    defer_mcp: bool = True
    require_approval: bool = True
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    max_output_kb: int = DEFAULT_MAX_OUTPUT_KB

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> CodemodeSettings:
        if not isinstance(raw, Mapping):
            return cls()
        enabled = raw["enabled"] if isinstance(raw.get("enabled"), bool) else True
        mode_raw = raw.get("mode")
        mode: CodemodeMode = mode_raw if mode_raw in ("hybrid", "only") else "hybrid"
        defer_mcp = raw["defer_mcp"] if isinstance(raw.get("defer_mcp"), bool) else True
        require_approval = (
            raw["require_approval"] if isinstance(raw.get("require_approval"), bool) else True
        )
        timeout_raw = raw.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if (
            isinstance(timeout_raw, (int, float))
            and not isinstance(timeout_raw, bool)
            and MIN_TIMEOUT_SECONDS <= float(timeout_raw) <= MAX_TIMEOUT_SECONDS
        ):
            timeout_seconds = float(timeout_raw)
        else:
            timeout_seconds = DEFAULT_TIMEOUT_SECONDS

        calls_raw = raw.get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS)
        if (
            isinstance(calls_raw, int)
            and not isinstance(calls_raw, bool)
            and MIN_MAX_TOOL_CALLS <= calls_raw <= MAX_MAX_TOOL_CALLS
        ):
            max_tool_calls = calls_raw
        else:
            max_tool_calls = DEFAULT_MAX_TOOL_CALLS

        kb_raw = raw.get("max_output_kb", DEFAULT_MAX_OUTPUT_KB)
        if (
            isinstance(kb_raw, int)
            and not isinstance(kb_raw, bool)
            and MIN_MAX_OUTPUT_KB <= kb_raw <= MAX_MAX_OUTPUT_KB
        ):
            max_output_kb = kb_raw
        else:
            max_output_kb = DEFAULT_MAX_OUTPUT_KB

        return cls(
            enabled=enabled,
            mode=mode,
            defer_mcp=defer_mcp,
            require_approval=require_approval,
            timeout_seconds=timeout_seconds,
            max_tool_calls=max_tool_calls,
            max_output_kb=max_output_kb,
        )
