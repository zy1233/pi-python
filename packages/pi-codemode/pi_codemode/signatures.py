"""Tool identifier normalization, Python signature stubs, and deferred namespace grouping."""

from __future__ import annotations

import keyword
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pi_agent_core.extensions.types import ToolInfo

FORBIDDEN_INNER_TOOLS: frozenset[str] = frozenset({"codemode", "workflow", "tool_search"})
_NON_IDENT_RE = re.compile(r"[^a-zA-Z0-9_]")


def is_harmless_annotations(annotations: Mapping[str, Any] | None) -> bool:
    """True when MCP-style annotations declare read-only or local non-destructive notes."""
    if not isinstance(annotations, Mapping):
        return False
    if annotations.get("readOnlyHint") is True:
        return True
    return annotations.get("destructiveHint") is False and annotations.get("openWorldHint") is False


def normalize_tool_identifier(name: str, taken: set[str]) -> str:
    """Turn *name* into a unique valid Python identifier for ``tools.<ident>``."""
    base = _NON_IDENT_RE.sub("_", name)
    if not base or base[0].isdigit():
        base = f"_{base}"
    if keyword.iskeyword(base):
        base = f"{base}_"
    candidate = base
    suffix = 2
    while candidate in taken:
        candidate = f"{base}_{suffix}"
        suffix += 1
    taken.add(candidate)
    return candidate


def _schema_type(schema: Mapping[str, Any]) -> str:
    if not isinstance(schema, Mapping):
        return "Any"
    if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
        vals = ", ".join(repr(v) for v in schema["enum"][:5])
        if len(schema["enum"]) > 5:
            vals += ", ..."
        return f"Literal[{vals}]"
    if "anyOf" in schema and isinstance(schema["anyOf"], list):
        parts: list[str] = []
        for sub in schema["anyOf"]:
            if isinstance(sub, Mapping):
                if sub.get("type") == "null":
                    if "None" not in parts:
                        parts.append("None")
                else:
                    t = _schema_type(sub)
                    if t not in parts:
                        parts.append(t)
        return " | ".join(parts) if parts else "Any"
    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        mapped = [_schema_type({"type": item}) if item != "null" else "None" for item in raw_type]
        return " | ".join(dict.fromkeys(mapped)) or "Any"
    mapping = {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
        "object": "dict[str, Any]",
        "null": "None",
    }
    if raw_type == "array":
        items = schema.get("items")
        inner = _schema_type(items) if isinstance(items, Mapping) else "Any"
        return f"list[{inner}]"
    if isinstance(raw_type, str):
        return mapping.get(raw_type, "Any")
    return "Any"


def render_python_signature(
    call_name: str, parameters: Mapping[str, Any] | None, *, read_only: bool = False
) -> str:
    """Render a compact one-line async Python signature stub from a JSON Schema."""
    props = parameters.get("properties") if isinstance(parameters, Mapping) else None
    required_raw = parameters.get("required") if isinstance(parameters, Mapping) else None
    required = set(required_raw) if isinstance(required_raw, list) else set()
    params_parts: list[str] = []
    if isinstance(props, Mapping) and props:
        ordered = [k for k in props if k in required] + [k for k in props if k not in required]
        for key in ordered:
            prop_schema = props[key]
            ptype = _schema_type(prop_schema if isinstance(prop_schema, Mapping) else {})
            if key in required:
                params_parts.append(f"{key}: {ptype}")
            else:
                if "None" not in ptype.split(" | "):
                    ptype = f"{ptype} | None"
                params_parts.append(f"{key}: {ptype} = None")
    sig_args = f"*, {', '.join(params_parts)}" if params_parts else ""
    tag = " [readOnly]" if read_only else ""
    return f"async def {call_name}({sig_args}) -> ToolText{tag}"


def build_tools_catalog(tools: Sequence[ToolInfo]) -> dict[str, dict[str, Any]]:
    """Build the ``ALL_TOOLS`` metadata dictionary exposed to codemode scripts."""
    taken: set[str] = set(FORBIDDEN_INNER_TOOLS)
    catalog: dict[str, dict[str, Any]] = {}
    for info in tools:
        if info.name in FORBIDDEN_INNER_TOOLS:
            continue
        call_name = normalize_tool_identifier(info.name, taken)
        read_only = is_harmless_annotations(info.annotations)
        params = dict(info.parameters) if isinstance(info.parameters, Mapping) else {}
        sig = render_python_signature(call_name, params, read_only=read_only)
        desc = " ".join((info.description or "").split())
        if len(desc) > 280:
            desc = desc[:277].rstrip() + "..."
        catalog[info.name] = {
            "call_name": call_name,
            "description": desc,
            "signature": sig,
            "parameters": params,
            "deferred": bool(info.deferred),
            "active": bool(info.active),
            "read_only": read_only,
        }
    return catalog


def namespace_for_tool(name: str) -> str:
    """Group deferred tool names (e.g. ``mcp__github__list_issues`` -> ``mcp__github``)."""
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) >= 2 and parts[1]:
            return f"mcp__{parts[1]}"
    if "__" in name:
        prefix = name.split("__", 1)[0]
        if prefix:
            return prefix
    return "other"


def group_deferred_namespaces(names: Sequence[str]) -> list[tuple[str, int]]:
    """Count deferred tools by namespace in stable sorted order."""
    counts = Counter(namespace_for_tool(name) for name in names)
    return sorted(counts.items())


def render_codemode_description(tools: Sequence[ToolInfo]) -> str:
    """Build a concise ``codemode`` tool description with direct stubs & deferred counts."""
    catalog = build_tools_catalog(tools)
    direct_lines: list[str] = []
    deferred_names: list[str] = []
    for name, entry in catalog.items():
        if entry["deferred"] and not entry["active"]:
            deferred_names.append(name)
        else:
            direct_lines.append(f"- {entry['signature']}")

    lines = [
        "Execute an async Python script inside an isolated sandbox to orchestrate multiple "
        "tools, filter large outputs locally, and return only the distilled result.",
        "Pass raw Python code without markdown fences (supports top-level `await`, `return`, "
        "or a trailing expression).",
        "",
        "Sandbox globals:",
        "- `await tools.<name>(**kwargs)` or `await tools.<name>(args_dict)` — call a tool",
        "- `await call_tool(raw_name, args_dict=None, **kwargs)` — call by exact tool name",
        "- `ALL_TOOLS`, `search_tools(query, limit=20)`, `describe_tool(name)` — inspect tools",
        "- `await parallel(*coros, concurrency=8)` — bounded concurrent execution",
        "- `text(...)`, `image(...)`, `print(...)` — emit text, images, or <console_output>",
        "- Tool errors raise `ToolError` / `ToolPermissionError` (catchable in script)",
    ]
    if direct_lines:
        lines.extend(["", "Direct tools on `tools`:", *direct_lines])
    if deferred_names:
        groups = group_deferred_namespaces(deferred_names)
        lines.append("")
        lines.append("Deferred tools (inspect via `search_tools(query)` / `describe_tool(name)`):")
        for ns, count in groups:
            noun = "tool" if count == 1 else "tools"
            prefix = f"{ns}__*" if ns != "other" else "other"
            lines.append(f"- {prefix} ({count} {noun})")
    return "\n".join(lines)
