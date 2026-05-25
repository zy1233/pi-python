"""LangChain stream adapter — StreamFn replacement for pi-ai streamSimple."""

from __future__ import annotations

import json
import time
from typing import Any

from pi_agent_core.adapters.langchain_convert import agent_tool_to_lc_schema, convert_to_langchain
from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.messages import (
    AssistantMessage,
    ThinkingContent,
    ToolCallContent,
    Usage,
)
from pi_agent_core.transform import transform_messages
from pi_agent_core.types import (
    DoneEvent,
    ErrorEvent,
    LlmContext,
    Model,
    StartEvent,
    StreamOptions,
    TextDeltaEvent,
    ThinkingLevel,
    ToolCallDeltaEvent,
)

_ANTHROPIC_BUDGET: dict[ThinkingLevel, int | None] = {
    "off": None,
    "minimal": 1024,
    "low": 4096,
    "medium": 10000,
    "high": 20000,
    "xhigh": 40000,
}

_OPENAI_EFFORT: dict[ThinkingLevel, str | None] = {
    "off": None,
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
}


def _apply_reasoning_params(
    kwargs: dict[str, Any],
    model: Model,
    level: ThinkingLevel | None,
) -> dict[str, Any]:
    if not level or level == "off":
        return kwargs

    provider = model.provider.lower()
    model_kwargs = dict(kwargs.get("model_kwargs") or {})

    if provider == "anthropic":
        budget = _ANTHROPIC_BUDGET.get(level)
        if budget is not None:
            model_kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
    elif provider == "openai":
        effort = _OPENAI_EFFORT.get(level)
        if effort is not None:
            model_kwargs["reasoning_effort"] = effort

    if model_kwargs:
        kwargs = {**kwargs, "model_kwargs": model_kwargs}
    return kwargs


def _get_attr(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _extract_usage(chunk: Any, provider: str) -> Usage:
    meta = _get_attr(chunk, "usage_metadata")
    if not meta:
        return Usage()

    input_tokens = int(_get_attr(meta, "input_tokens") or 0)
    output_tokens = int(_get_attr(meta, "output_tokens") or 0)
    total_tokens = int(_get_attr(meta, "total_tokens") or 0)

    input_details = _get_attr(meta, "input_token_details") or {}
    output_details = _get_attr(meta, "output_token_details") or {}

    cache_read = 0
    cache_write = 0
    reasoning_tokens = 0

    provider_lower = provider.lower()
    if provider_lower == "openai":
        cache_read = int(_get_attr(input_details, "cache_read") or 0)
        cache_write = int(_get_attr(input_details, "cache_creation") or 0)
        reasoning_tokens = int(_get_attr(output_details, "reasoning") or 0)
    elif provider_lower == "anthropic":
        cache_read = int(_get_attr(meta, "cache_read_input_tokens") or 0)
        cache_write = int(_get_attr(meta, "cache_creation_input_tokens") or 0)

    if not reasoning_tokens:
        reasoning_tokens = int(_get_attr(output_details, "reasoning") or 0)

    return Usage(
        input=input_tokens,
        output=output_tokens,
        cacheRead=cache_read,
        cacheWrite=cache_write,
        totalTokens=total_tokens,
        reasoningTokens=reasoning_tokens,
    )


def _apply_cost(usage: Usage, model: Model, options: StreamOptions) -> Usage:
    if not options.cost_calculator:
        return usage
    cost = options.cost_calculator(usage, model)
    return usage.model_copy(update={"cost": cost})


def _extract_thinking_delta(content: Any) -> str:
    if not content:
        return ""
    if isinstance(content, str):
        return ""
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                text = block.get("thinking") or block.get("text") or ""
                if text:
                    parts.append(text)
        return "".join(parts)
    return ""


def resolve_chat_model(
    model: Model,
    api_key: str | None = None,
    reasoning: ThinkingLevel | None = None,
) -> Any:
    """Resolve LangChain chat model from pi Model config."""
    kwargs: dict[str, Any] = {"model": model.model_id}
    if api_key:
        kwargs["api_key"] = api_key
    kwargs = _apply_reasoning_params(kwargs, model, reasoning)

    provider = model.provider.lower()
    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as e:
            raise ImportError(
                "Install langchain-openai: pip install 'pi-agent-core[openai]'"
            ) from e
        return ChatOpenAI(**kwargs)
    if provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as e:
            raise ImportError(
                "Install langchain-anthropic: pip install 'pi-agent-core[anthropic]'"
            ) from e
        return ChatAnthropic(**kwargs)

    try:
        from langchain.chat_models import init_chat_model

        return init_chat_model(model.model_id, model_provider=provider, **kwargs)
    except Exception as e:
        raise ValueError(f"Unsupported provider: {provider}") from e


def _empty_assistant(model: Model) -> AssistantMessage:
    return AssistantMessage(
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.model_id,
        usage=Usage(),
        stopReason="stop",
        timestamp=int(time.time() * 1000),
    )


async def langchain_stream(
    model: Model,
    context: LlmContext,
    options: StreamOptions | None = None,
) -> AssistantMessageEventStream:
    options = options or StreamOptions()
    stream = AssistantMessageEventStream()

    async def _produce() -> None:
        partial = _empty_assistant(model)
        last_chunk: Any = None
        try:
            chat = resolve_chat_model(model, options.api_key, options.reasoning)
            transformed = transform_messages(context.messages, model)
            lc_messages = convert_to_langchain(transformed, context.system_prompt)

            tools = context.tools or []
            if tools:
                schemas = []
                for t in tools:
                    schema = agent_tool_to_lc_schema(t)
                    if schema:
                        schemas.append(
                            {
                                "type": "function",
                                "function": {
                                    "name": t.name,
                                    "description": t.description,
                                    "parameters": schema,
                                },
                            }
                        )
                if schemas:
                    chat = chat.bind_tools(schemas)

            text_index = 0
            tool_calls_acc: dict[int, dict] = {}
            full_text = ""
            full_thinking = ""

            stream.push(StartEvent(partial=partial.model_copy(deep=True)))
            stream.set_final_message(partial)

            async for chunk in chat.astream(lc_messages):
                last_chunk = chunk
                if options.signal and getattr(options.signal, "aborted", False):
                    partial.stopReason = "aborted"
                    partial.errorMessage = "Operation aborted"
                    stream.push(
                        ErrorEvent(
                            partial=partial,
                            reason="aborted",
                            error_message="Operation aborted",
                        )
                    )
                    stream.set_final_message(partial)
                    stream.end()
                    return

                thinking_delta = _extract_thinking_delta(chunk.content)
                if thinking_delta:
                    full_thinking += thinking_delta
                    blocks: list = []
                    if full_thinking:
                        tc: ThinkingContent = {"type": "thinking", "thinking": full_thinking}
                        blocks.append(tc)
                    if full_text:
                        blocks.append({"type": "text", "text": full_text})
                    partial.content = blocks
                    stream.set_final_message(partial)

                content = chunk.content
                if isinstance(content, str) and content:
                    full_text += content
                    blocks = []
                    if full_thinking:
                        blocks.append({"type": "thinking", "thinking": full_thinking})
                    blocks.append({"type": "text", "text": full_text})
                    partial.content = blocks
                    stream.push(
                        TextDeltaEvent(
                            partial=partial.model_copy(deep=True),
                            delta=content,
                            content_index=text_index,
                        )
                    )
                    stream.set_final_message(partial)

                if hasattr(chunk, "tool_call_chunks") and chunk.tool_call_chunks:
                    for tcc in chunk.tool_call_chunks:
                        idx = tcc.get("index", 0)
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {"id": "", "name": "", "args": ""}
                        if tcc.get("id"):
                            tool_calls_acc[idx]["id"] = tcc["id"]
                        if tcc.get("name"):
                            tool_calls_acc[idx]["name"] = tcc["name"]
                        if tcc.get("args"):
                            tool_calls_acc[idx]["args"] += tcc["args"]
                            stream.push(
                                ToolCallDeltaEvent(
                                    partial=partial.model_copy(deep=True),
                                    delta=tcc["args"],
                                    content_index=idx + 1,
                                    tool_call_index=idx,
                                )
                            )

            content_blocks: list = []
            if full_thinking:
                content_blocks.append({"type": "thinking", "thinking": full_thinking})
            if full_text:
                content_blocks.append({"type": "text", "text": full_text})

            stop_reason = "stop"
            for idx in sorted(tool_calls_acc.keys()):
                acc = tool_calls_acc[idx]
                args_raw = acc.get("args") or "{}"
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except json.JSONDecodeError:
                    args = {}
                tc_block: ToolCallContent = {
                    "type": "toolCall",
                    "id": acc.get("id") or f"call_{idx}",
                    "name": acc.get("name") or "unknown",
                    "arguments": args if isinstance(args, dict) else {},
                }
                content_blocks.append(tc_block)
                stop_reason = "toolUse"

            partial.content = content_blocks
            partial.stopReason = stop_reason  # type: ignore[assignment]

            if last_chunk is not None:
                usage = _extract_usage(last_chunk, model.provider)
                partial.usage = _apply_cost(usage, model, options)

            stream.push(
                DoneEvent(
                    partial=partial.model_copy(deep=True),
                    reason="toolUse" if stop_reason == "toolUse" else "stop",
                )
            )
            stream.set_final_message(partial)
            stream.end()

        except Exception as e:
            partial.stopReason = "error"
            partial.errorMessage = str(e)
            stream.push(
                ErrorEvent(
                    partial=partial.model_copy(deep=True),
                    reason="error",
                    error_message=str(e),
                )
            )
            stream.set_final_message(partial)
            stream.end()

    import asyncio

    stream._task = asyncio.create_task(_produce())
    await asyncio.sleep(0)
    return stream
