"""LangChain stream adapter — StreamFn replacement for pi-ai streamSimple."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import random
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
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingLevel,
    ThinkingStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)

logger = logging.getLogger("pi_agent_core.langchain_stream")

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

# Anthropic requires max_tokens to exceed the thinking budget; reserve room for
# the visible answer on top of the budget.
_THINKING_OUTPUT_HEADROOM = 8192

# Transient failures worth retrying: timeouts, rate limits, server errors,
# Anthropic overloaded (529).
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504, 529}

# Connection-level exceptions (openai/anthropic SDK and httpx) matched by name
# so the optional provider packages are not imported here.
_RETRYABLE_EXC_NAMES = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "ReadError",
        "RemoteProtocolError",
        "TimeoutException",
    }
)


class _Aborted(Exception):
    """Internal marker: the stream was aborted via options.signal."""


def _is_retryable_error(error: BaseException) -> bool:
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status in _RETRYABLE_STATUS
    return type(error).__name__ in _RETRYABLE_EXC_NAMES


def _retry_after_seconds(error: BaseException) -> float | None:
    """Read a numeric Retry-After header off an SDK error, if present."""
    headers = getattr(getattr(error, "response", None), "headers", None)
    if headers is None or not hasattr(headers, "get"):
        return None
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return None


def _retry_delay(error: BaseException, attempt: int, base: float, cap: float) -> float:
    """Server-provided Retry-After wins; otherwise exponential backoff with jitter."""
    retry_after = _retry_after_seconds(error)
    if retry_after is not None:
        return min(retry_after, cap)
    delay = min(base * (2**attempt), cap)
    return min(delay + random.uniform(0, delay * 0.25), cap)


def _apply_reasoning_params(
    kwargs: dict[str, Any],
    model: Model,
    level: ThinkingLevel | None,
) -> dict[str, Any]:
    """Inject provider reasoning params as top-level constructor arguments.

    Both gates must be open: `model.reasoning` declares the capability (models
    without it reject these params at the API), and `level` is the per-request
    switch. This also keeps request params consistent with transform_messages,
    which strips thinking history for models with reasoning=False.
    """
    if not level or level == "off" or not model.reasoning:
        return kwargs

    provider = model.provider.lower()
    if provider == "anthropic":
        budget = _ANTHROPIC_BUDGET.get(level)
        if budget is not None:
            kwargs = {**kwargs, "thinking": {"type": "enabled", "budget_tokens": budget}}
            kwargs.setdefault("max_tokens", budget + _THINKING_OUTPUT_HEADROOM)
    elif provider == "openai":
        effort = _OPENAI_EFFORT.get(level)
        if effort is not None:
            kwargs = {**kwargs, "reasoning_effort": effort}
    return kwargs


def _get_attr(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _merge_usage_meta(acc: dict[str, Any], meta: Any) -> None:
    """Accumulate a chunk's usage_metadata into `acc`.

    Mirrors LangChain's `add_usage` (`full += chunk`) semantics: some providers
    split usage across chunks (e.g. input tokens on the first, output tokens on
    the last), so summing across all chunks is the only shape-agnostic approach.
    """
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        acc[key] = acc.get(key, 0) + int(_get_attr(meta, key) or 0)
    for details_key in ("input_token_details", "output_token_details"):
        details = _get_attr(meta, details_key)
        if not details:
            continue
        if not isinstance(details, dict):
            details = getattr(details, "__dict__", {})
        acc_details: dict[str, int] = acc.setdefault(details_key, {})
        for k, v in details.items():
            if isinstance(v, int):
                acc_details[k] = acc_details.get(k, 0) + v


def _usage_from_meta(meta: dict[str, Any]) -> Usage:
    """Build Usage from accumulated LangChain-standardized usage metadata.

    Cache and reasoning tokens live in input_token_details/output_token_details
    for every provider (LangChain normalizes them there — including Anthropic's
    cache_read_input_tokens/cache_creation_input_tokens).
    """
    input_details = meta.get("input_token_details") or {}
    output_details = meta.get("output_token_details") or {}
    return Usage(
        input=int(meta.get("input_tokens") or 0),
        output=int(meta.get("output_tokens") or 0),
        cacheRead=int(input_details.get("cache_read") or 0),
        cacheWrite=int(input_details.get("cache_creation") or 0),
        totalTokens=int(meta.get("total_tokens") or 0),
        reasoningTokens=int(output_details.get("reasoning") or 0),
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
                text = block.get("thinking") or ""
                if text:
                    parts.append(text)
        return "".join(parts)
    return ""


def _extract_thinking_signature(content: Any) -> str:
    """Collect thinking-block signatures (Anthropic sends one signature_delta)."""
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "thinking":
            sig = block.get("signature") or ""
            if sig:
                parts.append(sig)
    return "".join(parts)


def _extract_text_delta(content: Any) -> str:
    """Extract text from a chunk's content.

    Providers stream either a plain string (e.g. ChatOpenAI chat completions) or a
    list of content blocks (e.g. ChatAnthropic whenever tools/thinking are enabled,
    OpenAI responses API). Both shapes must be handled or assistant text is lost.
    """
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text") or ""
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
        # Chat Completions does not stream usage stats unless asked to.
        kwargs.setdefault("stream_usage", True)
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
    except ImportError as e:
        raise ImportError(
            f"Provider '{provider}' needs init_chat_model from the 'langchain' package: "
            "pip install langchain"
        ) from e

    # init_chat_model takes the model id positionally; drop it from kwargs to
    # avoid "got multiple values for argument 'model'".
    init_kwargs = {k: v for k, v in kwargs.items() if k != "model"}
    return init_chat_model(model.model_id, model_provider=provider, **init_kwargs)


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
        signal = options.signal
        usage_meta_acc: dict[str, Any] = {}
        try:
            chat = resolve_chat_model(model, options.api_key, options.reasoning)
            transformed = transform_messages(context.messages, model)
            lc_messages = convert_to_langchain(transformed, context.system_prompt, model)

            tools = context.tools or []
            schemas: list[dict] = []
            if tools:
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

            if signal is not None and getattr(signal, "aborted", False):
                raise _Aborted()

            if options.on_payload:
                hook_result = options.on_payload(
                    {
                        "provider": model.provider,
                        "model": model.model_id,
                        "system_prompt": context.system_prompt,
                        "messages": lc_messages,
                        "tools": schemas,
                        "reasoning": options.reasoning,
                    }
                )
                if inspect.isawaitable(hook_result):
                    await hook_result

            text_index = 0
            tool_calls_acc: dict[int, dict] = {}
            full_text = ""
            full_thinking = ""
            thinking_signature = ""
            text_started = False
            thinking_started = False
            thinking_ended = False

            stream.push(StartEvent(partial=partial.model_copy(deep=True)))
            stream.set_final_message(partial)

            def current_blocks() -> list:
                blocks: list = []
                if full_thinking:
                    tb: ThinkingContent = {"type": "thinking", "thinking": full_thinking}
                    if thinking_signature:
                        tb["signature"] = thinking_signature
                    blocks.append(tb)
                if full_text:
                    blocks.append({"type": "text", "text": full_text})
                return blocks

            def end_thinking_if_open() -> None:
                nonlocal thinking_ended
                if thinking_started and not thinking_ended:
                    thinking_ended = True
                    stream.push(
                        ThinkingEndEvent(
                            partial=partial.model_copy(deep=True),
                            content=full_thinking,
                            content_index=0,
                        )
                    )

            def handle_chunk(chunk: Any) -> None:
                nonlocal full_text, full_thinking, thinking_signature
                nonlocal text_started, thinking_started

                meta = _get_attr(chunk, "usage_metadata")
                if meta:
                    _merge_usage_meta(usage_meta_acc, meta)

                thinking_delta = _extract_thinking_delta(chunk.content)
                sig = _extract_thinking_signature(chunk.content)
                if sig:
                    thinking_signature += sig
                if thinking_delta:
                    if not thinking_started:
                        thinking_started = True
                        stream.push(
                            ThinkingStartEvent(
                                partial=partial.model_copy(deep=True), content_index=0
                            )
                        )
                    full_thinking += thinking_delta
                    partial.content = current_blocks()
                    stream.push(
                        ThinkingDeltaEvent(
                            partial=partial.model_copy(deep=True),
                            delta=thinking_delta,
                            content_index=0,
                        )
                    )
                    stream.set_final_message(partial)

                text_delta = _extract_text_delta(chunk.content)
                if text_delta:
                    if not text_started:
                        # Thinking streams ahead of text; close it before text opens.
                        end_thinking_if_open()
                        text_started = True
                        stream.push(
                            TextStartEvent(
                                partial=partial.model_copy(deep=True),
                                content_index=text_index,
                            )
                        )
                    full_text += text_delta
                    partial.content = current_blocks()
                    stream.push(
                        TextDeltaEvent(
                            partial=partial.model_copy(deep=True),
                            delta=text_delta,
                            content_index=text_index,
                        )
                    )
                    stream.set_final_message(partial)

                if hasattr(chunk, "tool_call_chunks") and chunk.tool_call_chunks:
                    for tcc in chunk.tool_call_chunks:
                        idx = tcc.get("index", 0)
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {"id": "", "name": "", "args": ""}
                            stream.push(
                                ToolCallStartEvent(
                                    partial=partial.model_copy(deep=True),
                                    content_index=idx + 1,
                                    tool_call_index=idx,
                                )
                            )
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

            max_retries = max(options.max_retries, 0)

            async def consume() -> None:
                # Retry transient failures, but only while no chunk has been
                # received: once deltas were emitted downstream they cannot be
                # rolled back, so mid-stream failures surface as error events
                # (mirrors pi's streamSimple retry scope).
                attempt = 0
                while True:
                    got_chunk = False
                    try:
                        async for chunk in chat.astream(lc_messages):
                            got_chunk = True
                            # Poll-based abort for signals without wait_aborted support.
                            if signal is not None and getattr(signal, "aborted", False):
                                raise _Aborted()
                            handle_chunk(chunk)
                        return
                    except _Aborted:
                        raise
                    except Exception as e:
                        if got_chunk or attempt >= max_retries or not _is_retryable_error(e):
                            raise
                        delay = _retry_delay(
                            e, attempt, options.retry_base_delay, options.retry_max_delay
                        )
                        attempt += 1
                        logger.warning(
                            "LLM stream failed before first token "
                            "(attempt %d/%d, retrying in %.1fs): %s",
                            attempt,
                            max_retries,
                            delay,
                            e,
                        )
                        await asyncio.sleep(delay)

            wait_aborted = getattr(signal, "wait_aborted", None) if signal is not None else None
            if wait_aborted is None:
                await consume()
            else:
                # Race the stream against abort so cancellation also fires while
                # waiting for the first token, and actually cancels the request.
                consume_task = asyncio.ensure_future(consume())
                abort_task = asyncio.ensure_future(wait_aborted())
                done, _ = await asyncio.wait(
                    {consume_task, abort_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if consume_task in done:
                    abort_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await abort_task
                    consume_task.result()
                else:
                    consume_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await consume_task
                    raise _Aborted()

            # Close still-open granular segments (thinking-only responses, text
            # that ran to the end of the stream).
            end_thinking_if_open()
            if text_started:
                stream.push(
                    TextEndEvent(
                        partial=partial.model_copy(deep=True),
                        content=full_text,
                        content_index=text_index,
                    )
                )

            content_blocks: list = current_blocks()

            stop_reason = "stop"
            for idx in sorted(tool_calls_acc.keys()):
                acc = tool_calls_acc[idx]
                args_raw = acc.get("args") or "{}"
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except json.JSONDecodeError:
                    logger.warning(
                        "Failed to parse tool call arguments for %s: %.200r",
                        acc.get("name") or "unknown",
                        args_raw,
                    )
                    args = {}
                tc_block: ToolCallContent = {
                    "type": "toolCall",
                    "id": acc.get("id") or f"call_{idx}",
                    "name": acc.get("name") or "unknown",
                    "arguments": args if isinstance(args, dict) else {},
                }
                content_blocks.append(tc_block)
                stop_reason = "toolUse"
                stream.push(
                    ToolCallEndEvent(
                        partial=partial.model_copy(deep=True),
                        tool_call=tc_block,
                        content_index=idx + 1,
                        tool_call_index=idx,
                    )
                )

            partial.content = content_blocks
            partial.stopReason = stop_reason  # type: ignore[assignment]

            if usage_meta_acc:
                partial.usage = _apply_cost(_usage_from_meta(usage_meta_acc), model, options)

            if options.on_response:
                hook_result = options.on_response(partial.model_copy(deep=True))
                if inspect.isawaitable(hook_result):
                    await hook_result

            stream.push(
                DoneEvent(
                    partial=partial.model_copy(deep=True),
                    reason="toolUse" if stop_reason == "toolUse" else "stop",
                )
            )
            stream.set_final_message(partial)
            stream.end()

        except _Aborted:
            partial.stopReason = "aborted"
            partial.errorMessage = "Operation aborted"
            stream.push(
                ErrorEvent(
                    partial=partial.model_copy(deep=True),
                    reason="aborted",
                    error_message="Operation aborted",
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

    stream._task = asyncio.create_task(_produce())
    await asyncio.sleep(0)
    return stream
