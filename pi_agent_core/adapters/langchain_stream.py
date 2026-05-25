"""LangChain stream adapter — StreamFn replacement for pi-ai streamSimple."""

from __future__ import annotations

import json
import time
from typing import Any

from pi_agent_core.adapters.langchain_convert import agent_tool_to_lc_schema, convert_to_langchain
from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.messages import AssistantMessage, ToolCallContent, Usage
from pi_agent_core.types import (
    DoneEvent,
    ErrorEvent,
    LlmContext,
    Model,
    StartEvent,
    StreamOptions,
    TextDeltaEvent,
    ToolCallDeltaEvent,
)


def resolve_chat_model(model: Model, api_key: str | None = None) -> Any:
    """Resolve LangChain chat model from pi Model config."""
    kwargs: dict[str, Any] = {"model": model.model_id}
    if api_key:
        kwargs["api_key"] = api_key

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
        try:
            chat = resolve_chat_model(model, options.api_key)
            lc_messages = convert_to_langchain(context.messages, context.system_prompt)

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

            stream.push(StartEvent(partial=partial.model_copy(deep=True)))
            stream.set_final_message(partial)

            async for chunk in chat.astream(lc_messages):
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

                content = chunk.content
                if isinstance(content, str) and content:
                    full_text += content
                    partial.content = [{"type": "text", "text": full_text}]
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

            # Finalize tool calls in content
            content_blocks: list = []
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
                tc: ToolCallContent = {
                    "type": "toolCall",
                    "id": acc.get("id") or f"call_{idx}",
                    "name": acc.get("name") or "unknown",
                    "arguments": args if isinstance(args, dict) else {},
                }
                content_blocks.append(tc)
                stop_reason = "toolUse"

            partial.content = content_blocks
            partial.stopReason = stop_reason  # type: ignore[assignment]

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
