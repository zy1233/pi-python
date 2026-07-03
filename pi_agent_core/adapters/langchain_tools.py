"""LangChain ``BaseTool`` -> ``AgentTool`` adapter (P6 spec §6, audit #8).

pi has no counterpart (its TS ecosystem uses extensions); this adapter is the
Python-specific bridge that lets the LangChain tool ecosystem — including MCP
tools from ``langchain-mcp-adapters`` — run inside pi's event protocol with
zero glue code.

Mapping rules:

- ``parameters`` comes from ``tool.tool_call_schema`` (injected args such as
  ``InjectedToolCallId`` are already excluded), falling back to
  ``args_schema``, then to an empty dict schema.
- Execution invokes ``tool.ainvoke`` with a ToolCall-shaped input so the
  result is always a ``ToolMessage``: ``content_and_artifact`` artifacts are
  preserved (a plain-dict invocation would drop them) and injected
  tool-call-id parameters are filled automatically.
- Results normalize to pi content blocks: strings become one text block;
  LangChain content-block lists map text/image blocks (base64 data URLs and
  standard image blocks become ``ImageContent``), anything else falls back to
  ``str()``. Artifacts are stored under ``details["artifact"]``.
- Exceptions (including ``ToolException``) bubble unchanged; the agent loop
  converts them to ``is_error=True`` tool results. Tools configured with
  ``handle_tool_error`` return an error-status ``ToolMessage`` instead — the
  adapter re-raises those so loop semantics stay uniform.
- ``signal``/``on_update`` are not forwarded (``BaseTool`` has no such
  channels); aborts are handled by the loop's ``tool_timeout`` and batch
  settlement. The adapter only does a cheap pre-flight abort check.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from pi_agent_core.messages import ImageContent, TextContent
from pi_agent_core.types import (
    AgentTool,
    AgentToolResult,
    AgentToolUpdateCallback,
    ToolExecutionMode,
)

_DATA_URL_RE = re.compile(r"data:([^;,]+);base64,(.*)", re.DOTALL)


def _extract_parameters(tool: BaseTool) -> type[BaseModel] | dict[str, Any]:
    try:
        schema: Any = tool.tool_call_schema
    except Exception:
        schema = getattr(tool, "args_schema", None)
    if isinstance(schema, dict):
        return schema
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema
    # Minimal (truthy) object schema: an empty dict would make the stream
    # adapter skip binding the tool entirely.
    return {"type": "object", "properties": {}}


def _normalize_block(item: Any) -> TextContent | ImageContent:
    if isinstance(item, str):
        return {"type": "text", "text": item}
    if isinstance(item, dict):
        block_type = item.get("type")
        if block_type == "text" and isinstance(item.get("text"), str):
            return {"type": "text", "text": item["text"]}
        if block_type == "image_url":
            url = item.get("image_url")
            if isinstance(url, dict):
                url = url.get("url")
            if isinstance(url, str):
                match = _DATA_URL_RE.match(url)
                if match:
                    return {"type": "image", "data": match.group(2), "mimeType": match.group(1)}
        if block_type == "image":
            data = item.get("base64") or item.get("data")
            mime = item.get("mime_type") or item.get("mimeType") or "image/png"
            if isinstance(data, str) and data:
                return {"type": "image", "data": data, "mimeType": mime}
    return {"type": "text", "text": str(item)}


def _normalize_content(content: Any) -> list[TextContent | ImageContent]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        if not content:
            return [{"type": "text", "text": ""}]
        return [_normalize_block(item) for item in content]
    return [{"type": "text", "text": str(content)}]


def _text_of(blocks: list[TextContent | ImageContent]) -> str:
    return " ".join(b["text"] for b in blocks if b.get("type") == "text").strip()


@dataclass
class _LangChainAgentTool:
    """``AgentTool`` protocol wrapper around a LangChain ``BaseTool``."""

    lc_tool: BaseTool
    name: str
    description: str
    label: str
    parameters: type[BaseModel] | dict[str, Any]
    execution_mode: ToolExecutionMode | None = None
    prepare_arguments: Callable[[Any], Any] | None = None

    async def execute(
        self,
        tool_call_id: str,
        params: Any,
        signal: Any | None = None,
        on_update: AgentToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        if signal is not None and getattr(signal, "aborted", False):
            raise RuntimeError("Operation aborted")

        if isinstance(params, BaseModel):
            # exclude_unset reproduces exactly the arguments the model sent;
            # the LangChain tool fills its own defaults on invocation.
            args = params.model_dump(exclude_unset=True)
        elif isinstance(params, dict):
            args = params
        else:
            args = {}

        message = await self.lc_tool.ainvoke(
            {"type": "tool_call", "name": self.lc_tool.name, "args": args, "id": tool_call_id}
        )

        if isinstance(message, ToolMessage):
            content = _normalize_content(message.content)
            if message.status == "error":
                raise RuntimeError(_text_of(content) or f"Tool {self.name} failed")
            artifact = message.artifact
        else:  # pragma: no cover - custom ainvoke overrides returning raw output
            content = _normalize_content(message)
            artifact = None

        details = {"artifact": artifact} if artifact is not None else None
        return AgentToolResult(content=content, details=details)


def from_langchain_tool(tool: BaseTool) -> AgentTool:
    """Wrap a LangChain ``BaseTool`` as a pi ``AgentTool``."""
    return _LangChainAgentTool(
        lc_tool=tool,
        name=tool.name,
        description=tool.description,
        label=tool.name,
        parameters=_extract_parameters(tool),
    )


def from_langchain_tools(tools: Sequence[BaseTool]) -> list[AgentTool]:
    """Wrap a sequence of LangChain tools as pi ``AgentTool``s."""
    return [from_langchain_tool(tool) for tool in tools]
