"""Stateful Agent wrapper — ported from packages/agent/src/agent.ts."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pi_agent_core.adapters.langchain_convert import default_convert_to_llm
from pi_agent_core.agent_loop import run_agent_loop, run_agent_loop_continue
from pi_agent_core.messages import AssistantMessage, ImageContent, Usage, UserMessage
from pi_agent_core.queues import PendingMessageQueue
from pi_agent_core.types import (
    AgentContext,
    AgentEndEvent,
    AgentEvent,
    AgentLoopConfig,
    AgentLoopTurnUpdate,
    AgentMessage,
    AgentTool,
    ConvertToLlmFn,
    CostCalculator,
    MessageEndEvent,
    MessageStartEvent,
    Model,
    QueueMode,
    StreamFn,
    ThinkingLevel,
    ToolExecutionMode,
    TransformContextFn,
    TurnEndEvent,
)

EMPTY_USAGE = Usage()


@dataclass
class _ActiveRun:
    promise: asyncio.Future
    abort_controller: Any


class Agent:
    """Stateful wrapper around the low-level agent loop."""

    def __init__(
        self,
        *,
        initial_state: dict[str, Any] | None = None,
        convert_to_llm: ConvertToLlmFn | None = None,
        transform_context: TransformContextFn | None = None,
        stream_fn: StreamFn | None = None,
        get_api_key: Callable[[str], str | None] | None = None,
        before_tool_call: Callable[..., Any] | None = None,
        after_tool_call: Callable[..., Any] | None = None,
        prepare_next_turn: Callable[..., Any] | None = None,
        steering_mode: QueueMode = "one-at-a-time",
        follow_up_mode: QueueMode = "one-at-a-time",
        session_id: str | None = None,
        tool_execution: ToolExecutionMode = "parallel",
        cost_calculator: CostCalculator | None = None,
    ) -> None:
        state = initial_state or {}
        self._system_prompt: str = state.get("system_prompt", "")
        self._model: Model = state.get(
            "model",
            Model(provider="unknown", model_id="unknown"),
        )
        self._thinking_level: ThinkingLevel = state.get("thinking_level", "off")
        self._tools: list[AgentTool] = list(state.get("tools", []))
        self._messages: list[AgentMessage] = list(state.get("messages", []))

        self.is_streaming: bool = False
        self.streaming_message: AgentMessage | None = None
        self.pending_tool_calls: set[str] = set()
        self.error_message: str | None = None

        self.convert_to_llm = convert_to_llm or default_convert_to_llm
        self.transform_context = transform_context
        self.stream_fn = stream_fn
        self.get_api_key = get_api_key
        self.before_tool_call = before_tool_call
        self.after_tool_call = after_tool_call
        self.prepare_next_turn = prepare_next_turn
        self.session_id = session_id
        self.tool_execution = tool_execution
        self.cost_calculator = cost_calculator

        self._steering_queue = PendingMessageQueue(steering_mode)
        self._follow_up_queue = PendingMessageQueue(follow_up_mode)
        self._listeners: list[Callable[[AgentEvent, Any], Any]] = []
        self._active_run: _ActiveRun | None = None

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        self._system_prompt = value

    @property
    def model(self) -> Model:
        return self._model

    @model.setter
    def model(self, value: Model) -> None:
        self._model = value

    @property
    def thinking_level(self) -> ThinkingLevel:
        return self._thinking_level

    @thinking_level.setter
    def thinking_level(self, value: ThinkingLevel) -> None:
        self._thinking_level = value

    @property
    def tools(self) -> list[AgentTool]:
        return self._tools

    @tools.setter
    def tools(self, value: list[AgentTool]) -> None:
        self._tools = list(value)

    @property
    def messages(self) -> list[AgentMessage]:
        return self._messages

    @messages.setter
    def messages(self, value: list[AgentMessage]) -> None:
        self._messages = list(value)

    @property
    def steering_mode(self) -> QueueMode:
        return self._steering_queue.mode

    @steering_mode.setter
    def steering_mode(self, mode: QueueMode) -> None:
        self._steering_queue.mode = mode

    @property
    def follow_up_mode(self) -> QueueMode:
        return self._follow_up_queue.mode

    @follow_up_mode.setter
    def follow_up_mode(self, mode: QueueMode) -> None:
        self._follow_up_queue.mode = mode

    @property
    def signal(self) -> Any | None:
        if self._active_run:
            return self._active_run.abort_controller.signal
        return None

    def subscribe(self, listener: Callable[[AgentEvent, Any], Any]) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def steer(self, message: AgentMessage) -> None:
        self._steering_queue.enqueue(message)

    def follow_up(self, message: AgentMessage) -> None:
        self._follow_up_queue.enqueue(message)

    def clear_steering_queue(self) -> None:
        self._steering_queue.clear()

    def clear_follow_up_queue(self) -> None:
        self._follow_up_queue.clear()

    def clear_all_queues(self) -> None:
        self.clear_steering_queue()
        self.clear_follow_up_queue()

    def has_queued_messages(self) -> bool:
        return self._steering_queue.has_items() or self._follow_up_queue.has_items()

    def abort(self) -> None:
        if self._active_run:
            self._active_run.abort_controller.abort()

    async def wait_for_idle(self) -> None:
        if self._active_run:
            await self._active_run.promise

    def reset(self) -> None:
        self._messages = []
        self.is_streaming = False
        self.streaming_message = None
        self.pending_tool_calls = set()
        self.error_message = None
        self.clear_all_queues()

    async def prompt(
        self,
        input: str | AgentMessage | list[AgentMessage],
        images: list[ImageContent] | None = None,
    ) -> None:
        if self._active_run:
            raise RuntimeError(
                "Agent is already processing a prompt. Use steer() or followUp() to queue messages."
            )
        messages = self._normalize_prompt_input(input, images)
        await self._run_prompt_messages(messages)

    async def continue_(self) -> None:
        if self._active_run:
            raise RuntimeError("Agent is already processing.")
        if not self._messages:
            raise RuntimeError("No messages to continue from")
        last = self._messages[-1]
        if getattr(last, "role", None) == "assistant":
            steering = self._steering_queue.drain()
            if steering:
                await self._run_prompt_messages(steering, skip_initial_steering_poll=True)
                return
            follow_ups = self._follow_up_queue.drain()
            if follow_ups:
                await self._run_prompt_messages(follow_ups)
                return
            raise RuntimeError("Cannot continue from message role: assistant")
        await self._run_continuation()

    def _normalize_prompt_input(
        self,
        input: str | AgentMessage | list[AgentMessage],
        images: list[ImageContent] | None,
    ) -> list[AgentMessage]:
        if isinstance(input, list):
            return input
        if not isinstance(input, str):
            return [input]
        content: list = [{"type": "text", "text": input}]
        if images:
            content.extend(images)
        return [UserMessage(content=content, timestamp=int(time.time() * 1000))]

    async def _run_prompt_messages(
        self,
        messages: list[AgentMessage],
        *,
        skip_initial_steering_poll: bool = False,
    ) -> None:
        await self._run_with_lifecycle(
            lambda signal: run_agent_loop(
                messages,
                self._create_context_snapshot(),
                self._create_loop_config(skip_initial_steering_poll=skip_initial_steering_poll),
                self._process_events,
                signal,
                self.stream_fn,
            )
        )

    async def _run_continuation(self) -> None:
        await self._run_with_lifecycle(
            lambda signal: run_agent_loop_continue(
                self._create_context_snapshot(),
                self._create_loop_config(),
                self._process_events,
                signal,
                self.stream_fn,
            )
        )

    def _create_context_snapshot(self) -> AgentContext:
        return AgentContext(
            system_prompt=self._system_prompt,
            messages=self._messages[:],
            tools=self._tools[:],
        )

    def _create_loop_config(self, *, skip_initial_steering_poll: bool = False) -> AgentLoopConfig:
        skip = skip_initial_steering_poll

        async def get_steering() -> list[AgentMessage]:
            nonlocal skip
            if skip:
                skip = False
                return []
            return self._steering_queue.drain()

        async def get_follow_up() -> list[AgentMessage]:
            return self._follow_up_queue.drain()

        async def prepare() -> AgentLoopTurnUpdate | None:
            if self.prepare_next_turn and self.signal:
                result = self.prepare_next_turn(self.signal)
                if inspect.isawaitable(result):
                    return await result
                return result
            return None

        return AgentLoopConfig(
            model=self._model,
            convert_to_llm=self.convert_to_llm,
            transform_context=self.transform_context,
            get_api_key=self.get_api_key,
            before_tool_call=self.before_tool_call,
            after_tool_call=self.after_tool_call,
            prepare_next_turn=prepare if self.prepare_next_turn else None,
            get_steering_messages=get_steering,
            get_follow_up_messages=get_follow_up,
            tool_execution=self.tool_execution,
            thinking_level=self._thinking_level,
            cost_calculator=self.cost_calculator,
        )

    async def _run_with_lifecycle(self, executor: Callable[[Any], Any]) -> None:
        if self._active_run:
            raise RuntimeError("Agent is already processing.")

        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        abort_controller = _AbortController()
        self._active_run = _ActiveRun(promise=future, abort_controller=abort_controller)

        self.is_streaming = True
        self.streaming_message = None
        self.error_message = None

        try:
            result = executor(abort_controller.signal)
            if inspect.isawaitable(result):
                await result
        except Exception as error:
            await self._handle_run_failure(error, abort_controller.signal.aborted)
        finally:
            self._finish_run()
            if not future.done():
                future.set_result(None)

    async def _handle_run_failure(self, error: Exception, aborted: bool) -> None:
        failure = AssistantMessage(
            content=[{"type": "text", "text": ""}],
            api=self._model.api,
            provider=self._model.provider,
            model=self._model.model_id,
            usage=EMPTY_USAGE,
            stopReason="aborted" if aborted else "error",
            errorMessage=str(error),
            timestamp=int(time.time() * 1000),
        )
        await self._process_events(MessageStartEvent(message=failure))
        await self._process_events(MessageEndEvent(message=failure))
        await self._process_events(TurnEndEvent(message=failure, tool_results=[]))
        await self._process_events(AgentEndEvent(messages=[failure]))

    def _finish_run(self) -> None:
        self.is_streaming = False
        self.streaming_message = None
        self.pending_tool_calls = set()
        self._active_run = None

    async def _process_events(self, event: AgentEvent) -> None:
        if event.type == "message_start" or event.type == "message_update":
            self.streaming_message = event.message
        elif event.type == "message_end":
            self.streaming_message = None
            self._messages.append(event.message)
        elif event.type == "tool_execution_start":
            pending = set(self.pending_tool_calls)
            pending.add(event.tool_call_id)
            self.pending_tool_calls = pending
        elif event.type == "tool_execution_end":
            pending = set(self.pending_tool_calls)
            pending.discard(event.tool_call_id)
            self.pending_tool_calls = pending
        elif event.type == "turn_end":
            if getattr(event.message, "role", None) == "assistant":
                err = getattr(event.message, "errorMessage", None)
                if err:
                    self.error_message = err
        elif event.type == "agent_end":
            self.streaming_message = None

        if not self._active_run:
            raise RuntimeError("Agent listener invoked outside active run")
        signal = self._active_run.abort_controller.signal
        for listener in self._listeners:
            result = listener(event, signal)
            if inspect.isawaitable(result):
                await result


class _AbortController:
    def __init__(self) -> None:
        self.signal = _AbortSignal()


class _AbortSignal:
    def __init__(self) -> None:
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True
