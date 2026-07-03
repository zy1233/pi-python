"""Agent loop — ported from packages/agent/src/agent-loop.ts."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from dataclasses import replace
from typing import Any

from pi_agent_core.event_stream import EventStream
from pi_agent_core.messages import (
    AssistantMessage,
    ToolResultMessage,
)
from pi_agent_core.types import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentContext,
    AgentEndEvent,
    AgentEvent,
    AgentLoopConfig,
    AgentLoopTurnUpdate,
    AgentMessage,
    AgentStartEvent,
    AgentToolCall,
    AgentToolResult,
    BeforeToolCallContext,
    BeforeToolCallResult,
    ContextBudget,
    LlmContext,
    MaxTurnsExceededError,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ShouldStopAfterTurnContext,
    StreamFn,
    StreamOptions,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from pi_agent_core.validation import validate_tool_arguments

logger = logging.getLogger("pi_agent_core.agent_loop")


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _emit(sink: Any, event: AgentEvent) -> None:
    result = sink(event)
    if inspect.isawaitable(result):
        await result


def _tag_emitter(emit: Any, run_id: str) -> Any:
    """Stamp correlation fields on every event (observability, audit #6).

    run_id groups all events of one run; turn_id counts turn_start events
    (pre-turn events such as agent_start carry turn_id=0).
    """
    turn = 0

    async def tagged(event: AgentEvent) -> None:
        nonlocal turn
        if event.type == "turn_start":
            turn += 1
        event.run_id = run_id
        event.turn_id = turn
        await _emit(emit, event)

    return tagged


def agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Any | None = None,
    stream_fn: StreamFn | None = None,
) -> EventStream[AgentEvent]:
    stream: EventStream[AgentEvent] = EventStream(
        is_terminal=lambda e: e.type == "agent_end",
        get_result=lambda e: e.messages,
    )

    async def _run() -> None:
        try:
            messages = await run_agent_loop(
                prompts, context, config, stream.push, signal, stream_fn
            )
            stream.end(messages)
        except Exception:
            logger.exception("agent_loop run failed")
            stream.end([])

    stream._task = asyncio.create_task(_run())
    return stream


def agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Any | None = None,
    stream_fn: StreamFn | None = None,
) -> EventStream[AgentEvent]:
    if not context.messages:
        raise ValueError("Cannot continue: no messages in context")
    last = context.messages[-1]
    if getattr(last, "role", None) == "assistant":
        raise ValueError("Cannot continue from message role: assistant")

    stream: EventStream[AgentEvent] = EventStream(
        is_terminal=lambda e: e.type == "agent_end",
        get_result=lambda e: e.messages,
    )

    async def _run() -> None:
        try:
            messages = await run_agent_loop_continue(
                context, config, stream.push, signal, stream_fn
            )
            stream.end(messages)
        except Exception:
            logger.exception("agent_loop_continue run failed")
            stream.end([])

    stream._task = asyncio.create_task(_run())
    return stream


async def run_agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    emit: Any,
    signal: Any | None = None,
    stream_fn: StreamFn | None = None,
) -> list[AgentMessage]:
    emit = _tag_emitter(emit, str(uuid.uuid4()))
    new_messages: list[AgentMessage] = list(prompts)
    current_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=[*context.messages, *prompts],
        tools=context.tools,
    )

    await _emit(emit, AgentStartEvent())
    await _emit(emit, TurnStartEvent())
    for prompt in prompts:
        await _emit(emit, MessageStartEvent(message=prompt))
        await _emit(emit, MessageEndEvent(message=prompt))

    await _run_loop(current_context, new_messages, config, signal, emit, stream_fn)
    return new_messages


async def run_agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    emit: Any,
    signal: Any | None = None,
    stream_fn: StreamFn | None = None,
) -> list[AgentMessage]:
    if not context.messages:
        raise ValueError("Cannot continue: no messages in context")
    last = context.messages[-1]
    if getattr(last, "role", None) == "assistant":
        raise ValueError("Cannot continue from message role: assistant")

    emit = _tag_emitter(emit, str(uuid.uuid4()))
    new_messages: list[AgentMessage] = []
    current_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=list(context.messages),
        tools=context.tools,
    )

    await _emit(emit, AgentStartEvent())
    await _emit(emit, TurnStartEvent())

    await _run_loop(current_context, new_messages, config, signal, emit, stream_fn)
    return new_messages


async def _finish_run(config: AgentLoopConfig, new_messages: list[AgentMessage], emit: Any) -> None:
    """on_agent_end hook (audit #5) then the terminal agent_end event."""
    if config.on_agent_end:
        await _maybe_await(config.on_agent_end(new_messages))
    await _emit(emit, AgentEndEvent(messages=new_messages))


async def _run_loop(
    initial_context: AgentContext,
    new_messages: list[AgentMessage],
    initial_config: AgentLoopConfig,
    signal: Any | None,
    emit: Any,
    stream_fn: StreamFn | None,
) -> None:
    current_context = initial_context
    config = initial_config
    first_turn = True
    turn_count = 0
    # Budget signal for before_llm_call: derived from the previous LLM call's
    # usage (audit C2 / #4 core half); None until the first response arrives.
    last_budget: ContextBudget | None = None
    pending_messages: list[AgentMessage] = []
    if config.get_steering_messages:
        pending_messages = list(await _maybe_await(config.get_steering_messages()) or [])

    while True:
        has_more_tool_calls = True

        while has_more_tool_calls or pending_messages:
            # Runaway protection: counts every LLM turn across follow-ups too.
            # Raising (instead of a graceful stop) mirrors OpenAI Agents SDK —
            # the Agent wrapper turns this into an error-stop assistant message.
            turn_count += 1
            if config.max_turns is not None and turn_count > config.max_turns:
                raise MaxTurnsExceededError(f"Agent loop exceeded max_turns={config.max_turns}")

            if not first_turn:
                await _emit(emit, TurnStartEvent())
            else:
                first_turn = False

            if pending_messages:
                for message in pending_messages:
                    await _emit(emit, MessageStartEvent(message=message))
                    await _emit(emit, MessageEndEvent(message=message))
                    current_context.messages.append(message)
                    new_messages.append(message)
                pending_messages = []

            if config.before_llm_call:
                replacement = await _maybe_await(
                    config.before_llm_call(current_context, last_budget)
                )
                if replacement is not None:
                    # Durable replacement: the compaction hook point. Subsequent
                    # turns build on the replaced context (new_messages keeps
                    # the full audit trail regardless).
                    current_context = replacement

            message = await _stream_assistant_response(
                current_context, config, signal, emit, stream_fn
            )
            new_messages.append(message)
            last_budget = ContextBudget.from_usage(message.usage, config.model)

            if message.stopReason in ("error", "aborted"):
                await _emit(emit, TurnEndEvent(message=message, tool_results=[]))
                await _finish_run(config, new_messages, emit)
                return

            if config.after_llm_call:
                # Guardrail tripwire: raising here aborts the run through the
                # standard failure path (Agent surfaces error_message).
                await _maybe_await(config.after_llm_call(current_context, message))

            tool_calls = [c for c in message.content if c.get("type") == "toolCall"]
            tool_results: list[ToolResultMessage] = []
            has_more_tool_calls = False

            if tool_calls:
                batch = await _execute_tool_calls(
                    current_context, message, tool_calls, config, signal, emit
                )
                tool_results = batch["messages"]
                has_more_tool_calls = not batch["terminate"]
                for result in tool_results:
                    current_context.messages.append(result)
                    new_messages.append(result)

            await _emit(emit, TurnEndEvent(message=message, tool_results=tool_results))

            next_ctx = ShouldStopAfterTurnContext(
                message=message,
                tool_results=tool_results,
                context=current_context,
                new_messages=new_messages,
            )
            if config.prepare_next_turn:
                snapshot = await _maybe_await(config.prepare_next_turn(next_ctx))
                if snapshot and isinstance(snapshot, AgentLoopTurnUpdate):
                    if snapshot.context:
                        current_context = snapshot.context
                    updates: dict[str, Any] = {}
                    if snapshot.model:
                        updates["model"] = snapshot.model
                    if snapshot.thinking_level is not None:
                        updates["thinking_level"] = snapshot.thinking_level
                    if updates:
                        # Copy instead of mutating the caller-owned config (pi spreads
                        # into a new object here as well).
                        config = replace(config, **updates)

            if config.should_stop_after_turn and await _maybe_await(
                config.should_stop_after_turn(next_ctx)
            ):
                await _finish_run(config, new_messages, emit)
                return

            pending_messages = []
            if config.get_steering_messages:
                pending_messages = list(await _maybe_await(config.get_steering_messages()) or [])

        follow_up: list[AgentMessage] = []
        if config.get_follow_up_messages:
            follow_up = list(await _maybe_await(config.get_follow_up_messages()) or [])
        if follow_up:
            pending_messages = follow_up
            continue
        break

    await _finish_run(config, new_messages, emit)


async def _stream_assistant_response(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Any | None,
    emit: Any,
    stream_fn: StreamFn | None,
) -> AssistantMessage:
    messages = context.messages
    if config.transform_context:
        messages = await _maybe_await(config.transform_context(messages, signal))

    llm_messages = await _maybe_await(config.convert_to_llm(messages))
    llm_context = LlmContext(
        system_prompt=context.system_prompt,
        messages=llm_messages,
        tools=context.tools,
    )

    if stream_fn is None:
        from pi_agent_core.adapters.langchain_stream import langchain_stream

        stream_function = langchain_stream
    else:
        stream_function = stream_fn

    resolved_api_key = None
    if config.get_api_key:
        resolved_api_key = await _maybe_await(config.get_api_key(config.model.provider))
    resolved_api_key = resolved_api_key or config.api_key

    options = StreamOptions(
        api_key=resolved_api_key,
        signal=signal or config.signal,
        reasoning=config.thinking_level,
        cost_calculator=config.cost_calculator,
        on_payload=config.on_payload,
        on_response=config.on_response,
        response_schema=config.response_schema,
    )
    if config.max_retries is not None:
        options.max_retries = config.max_retries
    response = await _maybe_await(stream_function(config.model, llm_context, options))

    partial_message: AssistantMessage | None = None
    added_partial = False

    async for event in response:
        if event.type == "start":
            partial_message = event.partial
            context.messages.append(partial_message)
            added_partial = True
            await _emit(emit, MessageStartEvent(message=partial_message.model_copy(deep=True)))
        elif event.type in (
            "text_start",
            "text_delta",
            "text_end",
            "thinking_start",
            "thinking_delta",
            "thinking_end",
            "toolcall_start",
            "toolcall_delta",
            "toolcall_end",
        ):
            if partial_message:
                partial_message = event.partial
                context.messages[-1] = partial_message
                await _emit(
                    emit,
                    MessageUpdateEvent(
                        assistant_message_event=event,
                        message=partial_message.model_copy(deep=True),
                    ),
                )
        elif event.type in ("done", "error"):
            final_message = event.partial
            if added_partial:
                context.messages[-1] = final_message
            else:
                context.messages.append(final_message)
            if not added_partial:
                await _emit(emit, MessageStartEvent(message=final_message.model_copy(deep=True)))
            await _emit(emit, MessageEndEvent(message=final_message))
            return final_message

    final_message = await response.message_result()
    if added_partial:
        context.messages[-1] = final_message
    else:
        context.messages.append(final_message)
        await _emit(emit, MessageStartEvent(message=final_message.model_copy(deep=True)))
    await _emit(emit, MessageEndEvent(message=final_message))
    return final_message


def _create_error_tool_result(message: str) -> AgentToolResult:
    return AgentToolResult(content=[{"type": "text", "text": message}], details={})


def _should_terminate_batch(finalized: list[dict]) -> bool:
    return len(finalized) > 0 and all(f["result"].terminate is True for f in finalized)


async def _execute_tool_calls(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_calls: list[AgentToolCall],
    config: AgentLoopConfig,
    signal: Any | None,
    emit: Any,
) -> dict:
    has_sequential = False
    tools = current_context.tools or []
    for tc in tool_calls:
        tool = next((t for t in tools if t.name == tc["name"]), None)
        if tool and getattr(tool, "execution_mode", None) == "sequential":
            has_sequential = True
            break

    if config.tool_execution == "sequential" or has_sequential:
        return await _execute_tool_calls_sequential(
            current_context, assistant_message, tool_calls, config, signal, emit
        )
    return await _execute_tool_calls_parallel(
        current_context, assistant_message, tool_calls, config, signal, emit
    )


async def _prepare_tool_call(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_call: AgentToolCall,
    config: AgentLoopConfig,
    signal: Any | None,
) -> dict:
    tools = current_context.tools or []
    tool = next((t for t in tools if t.name == tool_call["name"]), None)
    if not tool:
        return {
            "kind": "immediate",
            "result": _create_error_tool_result(f"Tool {tool_call['name']} not found"),
            "is_error": True,
        }

    try:
        prepared_call = tool_call
        if hasattr(tool, "prepare_arguments") and tool.prepare_arguments:
            prepared_args = tool.prepare_arguments(tool_call["arguments"])
            if prepared_args != tool_call["arguments"]:
                prepared_call = {**tool_call, "arguments": prepared_args}

        validated_args = validate_tool_arguments(tool, prepared_call)

        if config.before_tool_call:
            before = await _maybe_await(
                config.before_tool_call(
                    BeforeToolCallContext(
                        assistant_message=assistant_message,
                        tool_call=tool_call,
                        args=validated_args,
                        context=current_context,
                    ),
                    signal,
                )
            )
            if signal and getattr(signal, "aborted", False):
                return {
                    "kind": "immediate",
                    "result": _create_error_tool_result("Operation aborted"),
                    "is_error": True,
                }
            if isinstance(before, BeforeToolCallResult) and before.block:
                reason = before.reason or "Tool execution was blocked"
                return {
                    "kind": "immediate",
                    "result": _create_error_tool_result(reason),
                    "is_error": True,
                }

        if signal and getattr(signal, "aborted", False):
            return {
                "kind": "immediate",
                "result": _create_error_tool_result("Operation aborted"),
                "is_error": True,
            }

        return {"kind": "prepared", "tool_call": tool_call, "tool": tool, "args": validated_args}
    except Exception as e:
        return {
            "kind": "immediate",
            "result": _create_error_tool_result(str(e)),
            "is_error": True,
        }


async def _execute_prepared_tool_call(
    prepared: dict,
    signal: Any | None,
    emit: Any,
    timeout: float | None = None,
) -> dict:
    update_tasks: list[asyncio.Task] = []
    accepting_updates = True

    def on_update(partial: AgentToolResult) -> asyncio.Task | None:
        """Schedule the update event immediately (pi emits eagerly).

        Synchronous by contract (AgentToolUpdateCallback); returns the delivery
        task so legacy tools that `await on_update(...)` keep working.
        """
        if not accepting_updates:
            return None
        task = asyncio.ensure_future(
            _emit(
                emit,
                ToolExecutionUpdateEvent(
                    tool_call_id=prepared["tool_call"]["id"],
                    tool_name=prepared["tool_call"]["name"],
                    args=prepared["tool_call"]["arguments"],
                    partial_result=partial,
                ),
            )
        )
        update_tasks.append(task)
        return task

    try:
        exec_coro = prepared["tool"].execute(
            prepared["tool_call"]["id"],
            prepared["args"],
            signal,
            on_update,
        )
        if timeout is not None:
            # wait_for cancels the tool's task on expiry.
            result = await asyncio.wait_for(exec_coro, timeout=timeout)
        else:
            result = await exec_coro
        accepting_updates = False
        if update_tasks:
            await asyncio.gather(*update_tasks)
        return {"result": result, "is_error": False}
    except TimeoutError:
        # Caught separately: str(TimeoutError()) is empty, which would produce
        # a blank error tool result.
        accepting_updates = False
        if update_tasks:
            await asyncio.gather(*update_tasks)
        return {
            "result": _create_error_tool_result(f"Tool execution timed out after {timeout}s"),
            "is_error": True,
        }
    except Exception as e:
        accepting_updates = False
        if update_tasks:
            await asyncio.gather(*update_tasks)
        return {"result": _create_error_tool_result(str(e)), "is_error": True}
    finally:
        accepting_updates = False


async def _finalize_executed_tool_call(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    prepared: dict,
    executed: dict,
    config: AgentLoopConfig,
    signal: Any | None,
) -> dict:
    result = executed["result"]
    is_error = executed["is_error"]

    if config.after_tool_call:
        try:
            after = await _maybe_await(
                config.after_tool_call(
                    AfterToolCallContext(
                        assistant_message=assistant_message,
                        tool_call=prepared["tool_call"],
                        args=prepared["args"],
                        result=result,
                        is_error=is_error,
                        context=current_context,
                    ),
                    signal,
                )
            )
            if isinstance(after, AfterToolCallResult):
                # Three-way merge mirroring pi's `?? ` semantics: each field falls
                # back to the executed result independently.
                result = AgentToolResult(
                    content=after.content if after.content is not None else result.content,
                    details=after.details if after.details is not None else result.details,
                    terminate=after.terminate if after.terminate is not None else result.terminate,
                )
                if after.is_error is not None:
                    is_error = after.is_error
        except Exception as e:
            result = _create_error_tool_result(str(e))
            is_error = True

    return {"tool_call": prepared["tool_call"], "result": result, "is_error": is_error}


async def _emit_tool_execution_end(finalized: dict, emit: Any) -> None:
    await _emit(
        emit,
        ToolExecutionEndEvent(
            tool_call_id=finalized["tool_call"]["id"],
            tool_name=finalized["tool_call"]["name"],
            result=finalized["result"],
            is_error=finalized["is_error"],
        ),
    )


def _create_tool_result_message(finalized: dict) -> ToolResultMessage:
    return ToolResultMessage(
        toolCallId=finalized["tool_call"]["id"],
        toolName=finalized["tool_call"]["name"],
        content=finalized["result"].content,
        details=finalized["result"].details,
        isError=finalized["is_error"],
        timestamp=int(time.time() * 1000),
    )


async def _emit_tool_result_message(tool_result: ToolResultMessage, emit: Any) -> None:
    await _emit(emit, MessageStartEvent(message=tool_result))
    await _emit(emit, MessageEndEvent(message=tool_result))


async def _execute_tool_calls_sequential(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_calls: list[AgentToolCall],
    config: AgentLoopConfig,
    signal: Any | None,
    emit: Any,
) -> dict:
    finalized_calls: list[dict] = []
    messages: list[ToolResultMessage] = []

    for tool_call in tool_calls:
        await _emit(
            emit,
            ToolExecutionStartEvent(
                tool_call_id=tool_call["id"],
                tool_name=tool_call["name"],
                args=tool_call["arguments"],
            ),
        )

        preparation = await _prepare_tool_call(
            current_context, assistant_message, tool_call, config, signal
        )

        if preparation["kind"] == "immediate":
            finalized = {
                "tool_call": tool_call,
                "result": preparation["result"],
                "is_error": preparation["is_error"],
            }
        else:
            executed = await _execute_prepared_tool_call(
                preparation, signal, emit, config.tool_timeout
            )
            finalized = await _finalize_executed_tool_call(
                current_context, assistant_message, preparation, executed, config, signal
            )

        await _emit_tool_execution_end(finalized, emit)
        tool_result = _create_tool_result_message(finalized)
        await _emit_tool_result_message(tool_result, emit)
        finalized_calls.append(finalized)
        messages.append(tool_result)

        if signal and getattr(signal, "aborted", False):
            break

    return {"messages": messages, "terminate": _should_terminate_batch(finalized_calls)}


async def _execute_tool_calls_parallel(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_calls: list[AgentToolCall],
    config: AgentLoopConfig,
    signal: Any | None,
    emit: Any,
) -> dict:
    entries: list[dict | Any] = []

    for tool_call in tool_calls:
        await _emit(
            emit,
            ToolExecutionStartEvent(
                tool_call_id=tool_call["id"],
                tool_name=tool_call["name"],
                args=tool_call["arguments"],
            ),
        )

        preparation = await _prepare_tool_call(
            current_context, assistant_message, tool_call, config, signal
        )

        if preparation["kind"] == "immediate":
            finalized = {
                "tool_call": tool_call,
                "result": preparation["result"],
                "is_error": preparation["is_error"],
            }
            await _emit_tool_execution_end(finalized, emit)
            entries.append(finalized)
            if signal and getattr(signal, "aborted", False):
                break
            continue

        async def run_one(prep: dict = preparation) -> dict:
            executed = await _execute_prepared_tool_call(prep, signal, emit, config.tool_timeout)
            finalized = await _finalize_executed_tool_call(
                current_context, assistant_message, prep, executed, config, signal
            )
            await _emit_tool_execution_end(finalized, emit)
            return finalized

        entries.append(run_one)

        if signal and getattr(signal, "aborted", False):
            break

    # Start all pending executions concurrently (pi's Promise.all semantics):
    # tool_execution_end fires in completion order, results are kept in source order.
    started: list[dict | asyncio.Task] = [
        asyncio.create_task(entry()) if callable(entry) else entry for entry in entries
    ]
    ordered: list[dict] = []
    for entry in started:
        if isinstance(entry, asyncio.Task):
            ordered.append(await entry)
        else:
            ordered.append(entry)

    messages: list[ToolResultMessage] = []
    for finalized in ordered:
        tool_result = _create_tool_result_message(finalized)
        await _emit_tool_result_message(tool_result, emit)
        messages.append(tool_result)

    return {"messages": messages, "terminate": _should_terminate_batch(ordered)}
