"""AgentHarness runtime (Phase 3 H2)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pi_agent_core.agent_loop import run_agent_loop
from pi_agent_core.harness.messages import harness_convert_to_llm
from pi_agent_core.harness.session.session import Session
from pi_agent_core.harness.types import (
    AgentHarnessError,
    AgentHarnessEvent,
    AgentHarnessResources,
    AgentHarnessStreamOptions,
    AgentHarnessStreamOptionsPatch,
    BeforeAgentStartEvent,
    BeforeProviderPayloadEvent,
    BeforeProviderRequestEvent,
    ContextEvent,
    ModelUpdateEvent,
    QueueUpdateEvent,
    ResourcesUpdateEvent,
    SavePointEvent,
    SettledEvent,
    ThinkingLevelUpdateEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolsUpdateEvent,
    normalize_harness_error,
)
from pi_agent_core.messages import AssistantMessage, ImageContent, Usage, UserMessage
from pi_agent_core.types import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentContext,
    AgentEvent,
    AgentLoopConfig,
    AgentLoopTurnUpdate,
    AgentMessage,
    AgentTool,
    BeforeToolCallContext,
    BeforeToolCallResult,
    MessageEndEvent,
    Model,
    QueueMode,
    ShouldStopAfterTurnContext,
    StreamFn,
    StreamOptions,
    ThinkingLevel,
    TurnEndEvent,
)


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _create_user_message(text: str, images: list[ImageContent] | None = None) -> UserMessage:
    content: str | list = text
    if images:
        content = [{"type": "text", "text": text}, *images]
    return UserMessage(content=content)


def _failure_message(model: Model, error: Exception, aborted: bool) -> AssistantMessage:
    return AssistantMessage(
        content=[{"type": "text", "text": ""}],
        api=model.api,
        provider=model.provider,
        model=model.model_id,
        usage=Usage(),
        stopReason="aborted" if aborted else "error",
        errorMessage=str(error),
    )


class _AbortSignal:
    def __init__(self) -> None:
        self.aborted = False
        self._event = asyncio.Event()

    async def wait_aborted(self) -> None:
        await self._event.wait()

    def _abort(self) -> None:
        self.aborted = True
        self._event.set()


class _AbortController:
    def __init__(self) -> None:
        self.signal = _AbortSignal()

    def abort(self) -> None:
        self.signal._abort()


@dataclass
class _TurnState:
    messages: list[AgentMessage]
    resources: AgentHarnessResources
    stream_options: AgentHarnessStreamOptions
    session_id: str
    system_prompt: str
    model: Model
    thinking_level: ThinkingLevel
    tools: list[AgentTool]
    active_tools: list[AgentTool]


PendingWrite = dict[str, Any]


class AgentHarness:
    def __init__(
        self,
        *,
        session: Session,
        model: Model,
        stream_fn: StreamFn,
        env: Any | None = None,
        get_api_key: Callable[[str], str | None | Awaitable[str | None]] | None = None,
        tools: list[AgentTool] | None = None,
        resources: AgentHarnessResources | dict[str, Any] | None = None,
        system_prompt: str | Callable[[dict[str, Any]], str | Awaitable[str]] | None = None,
        stream_options: AgentHarnessStreamOptions | dict[str, Any] | None = None,
        thinking_level: ThinkingLevel = "off",
        active_tool_names: list[str] | None = None,
        steering_mode: QueueMode = "one-at-a-time",
        follow_up_mode: QueueMode = "one-at-a-time",
        max_turns: int | None = None,
        tool_timeout: float | None = None,
    ) -> None:
        self.env = env
        self.session = session
        self.model = model
        self.stream_fn = stream_fn
        self.get_api_key = get_api_key
        self.system_prompt = system_prompt
        self.thinking_level = thinking_level
        self.stream_options = (
            stream_options
            if isinstance(stream_options, AgentHarnessStreamOptions)
            else AgentHarnessStreamOptions.model_validate(stream_options or {})
        )
        self.resources = (
            resources
            if isinstance(resources, AgentHarnessResources)
            else AgentHarnessResources.model_validate(resources or {})
        )
        self.max_turns = max_turns
        self.tool_timeout = tool_timeout
        self.phase: Literal["idle", "turn", "compaction", "branch_summary", "retry"] = "idle"
        self._tools = {tool.name: tool for tool in tools or []}
        self._validate_unique(list(self._tools), "Duplicate tool name(s)")
        self.active_tool_names = active_tool_names or list(self._tools)
        self._validate_tool_names(self.active_tool_names)
        self.steering_mode = steering_mode
        self.follow_up_mode = follow_up_mode
        self.steer_queue: list[AgentMessage] = []
        self.follow_up_queue: list[AgentMessage] = []
        self.next_turn_queue: list[AgentMessage] = []
        self.pending_session_writes: list[PendingWrite] = []
        self._subscribers: list[Callable[[AgentHarnessEvent, Any | None], Any]] = []
        self._hooks: dict[str, list[Callable[[Any], Any]]] = {}
        self._run_abort_controller: _AbortController | None = None
        self._run_promise: asyncio.Task | None = None

    def _validate_unique(self, names: list[str], message: str) -> None:
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise AgentHarnessError("invalid_argument", f"{message}: {', '.join(duplicates)}")

    def _validate_tool_names(
        self, tool_names: list[str], tools: dict[str, AgentTool] | None = None
    ) -> None:
        tools = tools or self._tools
        self._validate_unique(tool_names, "Duplicate active tool name(s)")
        missing = [name for name in tool_names if name not in tools]
        if missing:
            raise AgentHarnessError("invalid_argument", f"Unknown tool(s): {', '.join(missing)}")

    def subscribe(
        self, listener: Callable[[AgentHarnessEvent, Any | None], Any]
    ) -> Callable[[], None]:
        self._subscribers.append(listener)

        def unsubscribe() -> None:
            if listener in self._subscribers:
                self._subscribers.remove(listener)

        return unsubscribe

    def on(self, event_type: str, handler: Callable[[Any], Any]) -> Callable[[], None]:
        self._hooks.setdefault(event_type, []).append(handler)

        def unsubscribe() -> None:
            handlers = self._hooks.get(event_type)
            if handlers and handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    async def _emit_any(self, event: AgentHarnessEvent, signal: Any | None = None) -> None:
        for subscriber in list(self._subscribers):
            await _maybe_await(subscriber(event, signal))

    async def _emit_hook(self, event: Any) -> Any:
        result = None
        for handler in list(self._hooks.get(event.type, [])):
            candidate = await _maybe_await(handler(event))
            if candidate is not None:
                result = candidate
        return result

    async def _emit_queue_update(self) -> None:
        await self._emit_any(
            QueueUpdateEvent(
                steer=list(self.steer_queue),
                followUp=list(self.follow_up_queue),
                nextTurn=list(self.next_turn_queue),
            )
        )

    async def _create_turn_state(self) -> _TurnState:
        context = await self.session.build_context()
        metadata = await self.session.get_metadata()
        active_tools = [self._tools[name] for name in self.active_tool_names if name in self._tools]
        system_prompt = "You are a helpful assistant."
        if isinstance(self.system_prompt, str):
            system_prompt = self.system_prompt
        elif self.system_prompt:
            system_prompt = await _maybe_await(
                self.system_prompt(
                    {
                        "env": self.env,
                        "session": self.session,
                        "model": self.model,
                        "thinking_level": self.thinking_level,
                        "active_tools": active_tools,
                        "resources": self.resources,
                    }
                )
            )
        return _TurnState(
            messages=list(context.messages),
            resources=self.resources.model_copy(deep=True),
            stream_options=self.stream_options.model_copy(deep=True),
            session_id=metadata.id,
            system_prompt=system_prompt,
            model=self.model,
            thinking_level=self.thinking_level,
            tools=list(self._tools.values()),
            active_tools=active_tools,
        )

    def _create_context(
        self, turn_state: _TurnState, system_prompt: str | None = None
    ) -> AgentContext:
        return AgentContext(
            system_prompt=system_prompt if system_prompt is not None else turn_state.system_prompt,
            messages=list(turn_state.messages),
            tools=list(turn_state.active_tools),
        )

    async def _drain_queue(self, queue: list[AgentMessage], mode: QueueMode) -> list[AgentMessage]:
        count = len(queue) if mode == "all" else min(1, len(queue))
        messages = queue[:count]
        del queue[:count]
        if messages:
            try:
                await self._emit_queue_update()
            except Exception:
                queue[:0] = messages
                raise
        return messages

    async def _flush_pending_session_writes(self) -> None:
        while self.pending_session_writes:
            write = self.pending_session_writes.pop(0)
            kind = write["type"]
            if kind == "message":
                await self.session.append_message(write["message"])
            elif kind == "model_change":
                await self.session.append_model_change(write["provider"], write["model_id"])
            elif kind == "thinking_level_change":
                await self.session.append_thinking_level_change(write["thinking_level"])
            elif kind == "active_tools_change":
                await self.session.append_active_tools_change(write["active_tool_names"])

    async def _handle_agent_event(self, event: AgentEvent, signal: Any | None = None) -> None:
        if event.type == "message_end":
            assert isinstance(event, MessageEndEvent)
            await self.session.append_message(event.message)
            await self._emit_any(event, signal)
            return
        if event.type == "turn_end":
            assert isinstance(event, TurnEndEvent)
            event_error: Exception | None = None
            try:
                await self._emit_any(event, signal)
            except Exception as e:
                event_error = e
            had_pending = bool(self.pending_session_writes)
            await self._flush_pending_session_writes()
            if event_error:
                raise event_error
            await self._emit_any(SavePointEvent(hadPendingMutations=had_pending), signal)
            return
        if event.type == "agent_end":
            await self._flush_pending_session_writes()
            self.phase = "idle"
            await self._emit_any(event, signal)
            await self._emit_any(SettledEvent(nextTurnCount=len(self.next_turn_queue)), signal)
            return
        await self._emit_any(event, signal)

    async def _emit_run_failure(
        self,
        model: Model,
        error: Exception,
        aborted: bool,
        signal: Any,
    ) -> list[AgentMessage]:
        from pi_agent_core.types import (
            AgentEndEvent,
            MessageEndEvent,
            MessageStartEvent,
            TurnEndEvent,
        )

        failure = _failure_message(model, error, aborted)
        await self._handle_agent_event(MessageStartEvent(message=failure), signal)
        await self._handle_agent_event(MessageEndEvent(message=failure), signal)
        await self._handle_agent_event(TurnEndEvent(message=failure, tool_results=[]), signal)
        await self._handle_agent_event(AgentEndEvent(messages=[failure]), signal)
        return [failure]

    def _create_stream_fn(self, get_turn_state: Callable[[], _TurnState]) -> StreamFn:
        async def wrapped(model: Model, context, options: StreamOptions | None = None):
            options = options or StreamOptions()
            turn_state = get_turn_state()
            request_options = turn_state.stream_options.model_copy(deep=True)
            hook_result = await self._emit_hook(
                BeforeProviderRequestEvent(
                    model=model,
                    sessionId=turn_state.session_id,
                    streamOptions=request_options,
                )
            )
            if hook_result:
                request_options = self._apply_stream_patch(request_options, hook_result)
            if request_options.maxRetries is not None:
                options.max_retries = request_options.maxRetries
            if request_options.maxRetryDelayMs is not None:
                options.retry_max_delay = request_options.maxRetryDelayMs / 1000
            options.session_id = turn_state.session_id
            return await _maybe_await(self.stream_fn(model, context, options))

        return wrapped

    def _apply_stream_patch(
        self, base: AgentHarnessStreamOptions, patch: Any
    ) -> AgentHarnessStreamOptions:
        if isinstance(patch, dict):
            patch = patch.get("streamOptions") or patch
        patch = (
            patch
            if isinstance(patch, AgentHarnessStreamOptionsPatch)
            else AgentHarnessStreamOptionsPatch.model_validate(patch)
        )
        result = base.model_copy(deep=True)
        if patch.timeoutMs is not None:
            result.timeoutMs = patch.timeoutMs
        if patch.maxRetries is not None:
            result.maxRetries = patch.maxRetries
        if patch.maxRetryDelayMs is not None:
            result.maxRetryDelayMs = patch.maxRetryDelayMs
        if patch.headers is not None:
            headers = dict(result.headers or {})
            for key, value in patch.headers.items():
                if value is None:
                    headers.pop(key, None)
                else:
                    headers[key] = value
            result.headers = headers or None
        if patch.metadata is not None:
            metadata = dict(result.metadata or {})
            for key, value in patch.metadata.items():
                if value is None:
                    metadata.pop(key, None)
                else:
                    metadata[key] = value
            result.metadata = metadata or None
        return result

    def _create_loop_config(
        self,
        get_turn_state: Callable[[], _TurnState],
        set_turn_state: Callable[[_TurnState], None],
    ) -> AgentLoopConfig:
        turn_state = get_turn_state()

        async def transform_context(messages: list[AgentMessage], signal: Any | None):
            result = await self._emit_hook(ContextEvent(messages=list(messages)))
            if isinstance(result, dict) and "messages" in result:
                return result["messages"]
            if result is not None and hasattr(result, "messages"):
                return result.messages
            return messages

        async def before_tool_call(ctx: BeforeToolCallContext, signal: Any | None):
            result = await self._emit_hook(
                ToolCallEvent(
                    toolCallId=ctx.tool_call["id"],
                    toolName=ctx.tool_call["name"],
                    input=dict(ctx.args or {}),
                )
            )
            if not result:
                return None
            if isinstance(result, dict):
                block = result.get("block")
                reason = result.get("reason")
            else:
                block = getattr(result, "block", None)
                reason = getattr(result, "reason", None)
            return BeforeToolCallResult(block=block, reason=reason)

        async def after_tool_call(ctx: AfterToolCallContext, signal: Any | None):
            result = await self._emit_hook(
                ToolResultEvent(
                    toolCallId=ctx.tool_call["id"],
                    toolName=ctx.tool_call["name"],
                    input=dict(ctx.args or {}),
                    content=ctx.result.content,
                    details=ctx.result.details,
                    isError=ctx.is_error,
                )
            )
            if not result:
                return None
            if isinstance(result, dict):
                get = result.get
            else:

                def get(k: str, default: Any = None) -> Any:
                    return getattr(result, k, default)

            return AfterToolCallResult(
                content=get("content"),
                details=get("details"),
                is_error=get("is_error", get("isError")),
                terminate=get("terminate"),
            )

        async def prepare_next_turn(ctx: ShouldStopAfterTurnContext):
            await self._flush_pending_session_writes()
            next_state = await self._create_turn_state()
            set_turn_state(next_state)
            return AgentLoopTurnUpdate(
                context=self._create_context(next_state),
                model=next_state.model,
                thinking_level=next_state.thinking_level,
            )

        async def on_payload(payload: dict[str, Any]):
            result = await self._emit_hook(
                BeforeProviderPayloadEvent(model=get_turn_state().model, payload=payload)
            )
            if isinstance(result, dict) and "payload" in result:
                return result["payload"]
            return payload

        async def on_response(message: AssistantMessage):
            from pi_agent_core.harness.types import AfterProviderResponseEvent

            await self._emit_any(AfterProviderResponseEvent(message=message))

        return AgentLoopConfig(
            model=turn_state.model,
            convert_to_llm=harness_convert_to_llm,
            transform_context=transform_context,
            get_api_key=self.get_api_key,
            prepare_next_turn=prepare_next_turn,
            get_steering_messages=lambda: self._drain_queue(self.steer_queue, self.steering_mode),
            get_follow_up_messages=lambda: self._drain_queue(
                self.follow_up_queue, self.follow_up_mode
            ),
            before_tool_call=before_tool_call,
            after_tool_call=after_tool_call,
            thinking_level=(
                None if turn_state.thinking_level == "off" else turn_state.thinking_level
            ),
            max_retries=turn_state.stream_options.maxRetries,
            max_turns=self.max_turns,
            tool_timeout=self.tool_timeout,
            on_payload=on_payload,
            on_response=on_response,
        )

    async def _execute_turn(
        self, turn_state: _TurnState, text: str, images: list[ImageContent] | None = None
    ) -> AssistantMessage:
        active_turn_state = turn_state
        messages: list[AgentMessage] = [_create_user_message(text, images)]
        if self.next_turn_queue:
            queued = self.next_turn_queue[:]
            self.next_turn_queue.clear()
            await self._emit_queue_update()
            messages = [*queued, messages[0]]
        before_result = await self._emit_hook(
            BeforeAgentStartEvent(
                prompt=text,
                images=images,
                systemPrompt=turn_state.system_prompt,
                resources=turn_state.resources,
            )
        )
        system_prompt = turn_state.system_prompt
        if before_result:
            if isinstance(before_result, dict):
                extra = before_result.get("messages")
                if extra:
                    messages.extend(extra)
                system_prompt = before_result.get("system_prompt") or before_result.get(
                    "systemPrompt", system_prompt
                )
            else:
                extra = getattr(before_result, "messages", None)
                if extra:
                    messages.extend(extra)
                system_prompt = getattr(before_result, "system_prompt", system_prompt)

        controller = _AbortController()
        self._run_abort_controller = controller

        def get_turn_state() -> _TurnState:
            return active_turn_state

        def set_turn_state(next_state: _TurnState) -> None:
            nonlocal active_turn_state
            active_turn_state = next_state

        try:
            new_messages = await run_agent_loop(
                messages,
                self._create_context(turn_state, system_prompt),
                self._create_loop_config(get_turn_state, set_turn_state),
                lambda event: self._handle_agent_event(event, controller.signal),
                controller.signal,
                self._create_stream_fn(get_turn_state),
            )
        except Exception as e:
            new_messages = await self._emit_run_failure(
                active_turn_state.model,
                e,
                controller.signal.aborted,
                controller.signal,
            )
        finally:
            self._run_abort_controller = None
            await self._flush_pending_session_writes()

        for message in reversed(new_messages):
            if getattr(message, "role", None) == "assistant":
                return message
        raise AgentHarnessError("invalid_state", "AgentHarness prompt completed without assistant")

    async def prompt(self, text: str, images: list[ImageContent] | None = None) -> AssistantMessage:
        if self.phase != "idle":
            raise AgentHarnessError("busy", "AgentHarness is busy")
        self.phase = "turn"
        try:
            return await self._execute_turn(await self._create_turn_state(), text, images)
        except Exception as e:
            self.phase = "idle"
            raise normalize_harness_error(e, "unknown") from e

    async def steer(self, text: str, images: list[ImageContent] | None = None) -> None:
        if self.phase == "idle":
            raise AgentHarnessError("invalid_state", "Cannot steer while idle")
        self.steer_queue.append(_create_user_message(text, images))
        await self._emit_queue_update()

    async def follow_up(self, text: str, images: list[ImageContent] | None = None) -> None:
        if self.phase == "idle":
            raise AgentHarnessError("invalid_state", "Cannot follow up while idle")
        self.follow_up_queue.append(_create_user_message(text, images))
        await self._emit_queue_update()

    async def next_turn(self, text: str, images: list[ImageContent] | None = None) -> None:
        self.next_turn_queue.append(_create_user_message(text, images))
        await self._emit_queue_update()

    async def append_message(self, message: AgentMessage) -> None:
        if self.phase == "idle":
            await self.session.append_message(message)
        else:
            self.pending_session_writes.append({"type": "message", "message": message})

    async def set_model(self, model: Model) -> None:
        previous = self.model
        if self.phase == "idle":
            await self.session.append_model_change(model.provider, model.model_id)
        else:
            self.pending_session_writes.append(
                {"type": "model_change", "provider": model.provider, "model_id": model.model_id}
            )
        self.model = model
        await self._emit_any(ModelUpdateEvent(model=model, previousModel=previous))

    async def set_thinking_level(self, level: ThinkingLevel) -> None:
        previous = self.thinking_level
        if self.phase == "idle":
            await self.session.append_thinking_level_change(level)
        else:
            self.pending_session_writes.append(
                {"type": "thinking_level_change", "thinking_level": level}
            )
        self.thinking_level = level
        await self._emit_any(ThinkingLevelUpdateEvent(level=level, previousLevel=previous))

    async def set_tools(
        self, tools: list[AgentTool], active_tool_names: list[str] | None = None
    ) -> None:
        next_tools = {tool.name: tool for tool in tools}
        self._validate_unique(list(next_tools), "Duplicate tool name(s)")
        next_active = active_tool_names or self.active_tool_names
        self._validate_tool_names(next_active, next_tools)
        previous_tool_names = list(self._tools)
        previous_active = list(self.active_tool_names)
        self._tools = next_tools
        self.active_tool_names = list(next_active)
        if self.phase == "idle":
            await self.session.append_active_tools_change(self.active_tool_names)
        else:
            self.pending_session_writes.append(
                {"type": "active_tools_change", "active_tool_names": list(self.active_tool_names)}
            )
        await self._emit_any(
            ToolsUpdateEvent(
                toolNames=list(self._tools),
                previousToolNames=previous_tool_names,
                activeToolNames=list(self.active_tool_names),
                previousActiveToolNames=previous_active,
            )
        )

    async def set_active_tools(self, tool_names: list[str]) -> None:
        await self.set_tools(list(self._tools.values()), tool_names)

    def get_resources(self) -> AgentHarnessResources:
        return self.resources.model_copy(deep=True)

    async def set_resources(self, resources: AgentHarnessResources | dict[str, Any]) -> None:
        previous = self.get_resources()
        self.resources = (
            resources
            if isinstance(resources, AgentHarnessResources)
            else AgentHarnessResources.model_validate(resources)
        )
        await self._emit_any(
            ResourcesUpdateEvent(resources=self.get_resources(), previousResources=previous)
        )

    async def wait_for_idle(self) -> None:
        while self.phase != "idle":
            await asyncio.sleep(0)

    async def abort(self) -> dict[str, list[AgentMessage]]:
        cleared_steer = list(self.steer_queue)
        cleared_follow_up = list(self.follow_up_queue)
        self.steer_queue.clear()
        self.follow_up_queue.clear()
        self._run_abort_controller and self._run_abort_controller.abort()
        await self._emit_queue_update()
        await self.wait_for_idle()
        from pi_agent_core.harness.types import AbortEvent

        await self._emit_any(
            AbortEvent(clearedSteer=cleared_steer, clearedFollowUp=cleared_follow_up)
        )
        return {"cleared_steer": cleared_steer, "cleared_follow_up": cleared_follow_up}

    async def compact(self, custom_instructions: str | None = None) -> Any:
        raise AgentHarnessError("invalid_state", "compact() is implemented in Phase 3 H3")

    async def navigate_tree(self, target_id: str, options: dict[str, Any] | None = None) -> Any:
        raise AgentHarnessError("invalid_state", "navigate_tree() is implemented in Phase 3 H3")

    async def skill(
        self, name: str, additional_instructions: str | None = None
    ) -> AssistantMessage:
        raise AgentHarnessError("invalid_state", "skill() is implemented in Phase 3 H4")

    async def prompt_from_template(
        self, name: str, args: list[str] | None = None
    ) -> AssistantMessage:
        raise AgentHarnessError(
            "invalid_state", "prompt_from_template() is implemented in Phase 3 H4"
        )
