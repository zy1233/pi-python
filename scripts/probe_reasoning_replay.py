"""Ask a real gateway: does it take ``reasoning_content`` back, and does the model use it?

``Model.replay_reasoning`` (``replay_reasoning = true`` in ``agent.toml``) makes the agent send the
thinking of earlier assistant messages back to a gateway of the OpenAI format. The unit tests use
fake servers. They cannot show four things that only a real gateway can show:

1. Does the gateway accept the field, or refuse it? If it refuses, what is the text of the error?
   The agent asks again without the field only when that text names ``reasoning_content``.
2. Does the gateway need the field? DeepSeek's own API refuses a request with tools whose history
   has no thinking. A gateway in front of it can refuse in the same way.
3. If the gateway accepts the field, does it pass the field to the model, or does it drop it?
4. Does the model then keep its own line of thought across tool calls?

This script runs one small task two times: with ``replay_reasoning`` off, and with it on. It uses
the same code as the agent (``run_agent_loop`` with ``langchain_stream``). In the task the model
must choose two secret numbers, keep them in its thinking only, call a tool, and then say the
numbers. It can say them only if it sees its earlier thinking. The run with ``replay_reasoning``
off is the control: a model can pick the same "random" numbers twice, and the control shows it.
The script sends only this task. It sends no file and no session of yours.

Usage (PowerShell):
    $env:REAL_LLM_API_KEY = 'sk-...'
    python scripts/probe_reasoning_replay.py --base-url https://gateway.example/v1 --model NAME

The key is read from the environment. It is never printed (a short fingerprint is printed, so you
can see which key the process has). Each run makes about 2 to 4 requests. A model does not behave
the same way each time, so use ``--repeat 3`` for a firmer result.

Exit status: 0 when every run ended with an answer, 1 when a run failed (a refused request that
the agent does not recover from counts as a failure), 2 for a wrong call of the script.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import importlib
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from pi_agent_core import AgentContext, AgentLoopConfig, Model, run_agent_loop
from pi_agent_core.adapters.langchain_convert import default_convert_to_llm
from pi_agent_core.messages import UserMessage
from pi_agent_core.tools import SimpleTool
from pi_agent_core.types import AgentToolResult

ls_mod = importlib.import_module("pi_agent_core.adapters.langchain_stream")

SYSTEM_PROMPT = "Follow the steps of the user exactly. Be brief."
THINKING_LEVELS = ("minimal", "low", "medium", "high", "xhigh", "max")
# The keys of the task. The first ``hops`` of them are used, with their values.
CHAIN = (("alpha", 7), ("bravo", 12), ("charlie", 5), ("delta", 9), ("echo", 3), ("foxtrot", 8))
# Numbers of four digits that the prompt itself names. The test does not count them.
PROMPT_NUMBERS = frozenset({1000, 9999})
FOUR_DIGITS = re.compile(r"(?<!\d)[1-9]\d{3}(?!\d)")


@dataclass(frozen=True)
class Settings:
    provider: str
    model_id: str
    base_url: str
    api_key: str = field(repr=False)  # never in a repr, so a traceback cannot show it
    thinking_level: str = "high"
    hops: int = 1  # the number of tool calls that the task needs; the run has hops + 1 turns
    timeout: float = 180.0  # seconds for one run


@dataclass
class Request:
    """One HTTP request that the chat model made."""

    number: int
    thinking_messages: int  # assistant messages that carry the field ``reasoning_content``
    thinking_chars: int  # the length of the values of that field, added up
    error: BaseException | None = None  # what ``create`` raised, if it raised
    assistant_messages: int = 0  # assistant messages in the request, with the field or without


@dataclass
class Turn:
    """One assistant message of a run."""

    number: int
    seconds: float
    thinking: str
    text: str
    tool_calls: list[tuple[str, str]]  # (name, arguments as JSON text)
    output_tokens: int
    stop_reason: str
    error: str | None


@dataclass
class Run:
    replay: bool
    turns: list[Turn] = field(default_factory=list)
    requests: list[Request] = field(default_factory=list)
    crash: str | None = None  # the run raised an error that is not a turn error (a timeout)

    @property
    def seconds(self) -> float:
        return sum(turn.seconds for turn in self.turns)

    @property
    def failed(self) -> bool:
        last = self.turns[-1] if self.turns else None
        return (
            self.crash is not None
            or last is None
            or last.stop_reason != "stop"  # not an answer: an error, a cut-off, a tool call
            or last.error is not None
        )


# ---------------------------------------------------------------------------
# The task
# ---------------------------------------------------------------------------


class LookupParams(BaseModel):
    key: str = Field(description="The key to look up")


def build_prompt() -> str:
    return (
        "Do these steps in order.\n"
        "1. Choose two different random whole numbers from 1000 to 9999. Keep both in your "
        "thinking only. Do not write them in any visible text, and do not use them in any tool "
        "argument, until step 3.\n"
        "2. Call the tool `lookup` with the key 'alpha'. It returns a value and the next key. "
        "Call `lookup` again with each next key, one call at a time, until the next key is NONE.\n"
        "3. Reply with one line: the sum of all the values, then the two numbers from step 1, "
        "all separated by commas."
    )


def expected_sum(hops: int) -> int:
    return sum(value for _, value in CHAIN[:hops])


def lookup_text(key: str, hops: int) -> str:
    chain = CHAIN[:hops]
    for index, (name, value) in enumerate(chain):
        if name == key.strip().lower():
            following = chain[index + 1][0] if index + 1 < len(chain) else "NONE"
            return f"value={value} next={following}"
    return f"error: unknown key {key!r}"


def lookup_tool(hops: int) -> SimpleTool:
    async def lookup(_tool_call_id, params: LookupParams, _signal, _on_update):
        return AgentToolResult(content=[{"type": "text", "text": lookup_text(params.key, hops)}])

    return SimpleTool(
        name="lookup",
        description="Look up a key. It returns the value of the key and the next key.",
        label="lookup",
        parameters=LookupParams,
        execute_fn=lookup,
    )


def numbers_in(text: str) -> list[int]:
    """The numbers from 1000 to 9999 in *text* that the prompt does not name, without repeats."""
    flat = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)  # 4,827 is 4827
    found = (int(match) for match in FOUR_DIGITS.findall(flat))
    return list(dict.fromkeys(n for n in found if n not in PROMPT_NUMBERS))


# ---------------------------------------------------------------------------
# Writing down the requests
# ---------------------------------------------------------------------------


class Recorder:
    """Writes down each request that a chat model makes, and the error that it gets."""

    def __init__(self) -> None:
        self.requests: list[Request] = []

    def wrap(self, client: Any) -> _RecordingClient:
        return _RecordingClient(client, self)


class _RecordingClient:
    """Stands in for ``async_client`` of a chat model: records a request, then makes it."""

    def __init__(self, inner: Any, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    async def create(self, **payload: Any) -> Any:
        assistant = [
            message
            for message in payload.get("messages") or []
            if isinstance(message, dict) and message.get("role") == "assistant"
        ]
        marked = [message for message in assistant if "reasoning_content" in message]
        request = Request(
            number=len(self._recorder.requests) + 1,
            thinking_messages=len(marked),
            thinking_chars=sum(len(str(m["reasoning_content"] or "")) for m in marked),
            assistant_messages=len(assistant),
        )
        self._recorder.requests.append(request)
        try:
            return await self._inner.create(**payload)
        except Exception as exc:
            request.error = exc
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@contextlib.contextmanager
def recording(recorder: Recorder) -> Iterator[None]:
    """Inside the block, each request of a chat model of the adapter goes through *recorder*."""
    real = ls_mod.resolve_chat_model

    def resolve(*args: Any, **kwargs: Any) -> Any:
        chat = real(*args, **kwargs)
        chat.async_client = recorder.wrap(chat.async_client)
        return chat

    ls_mod.resolve_chat_model = resolve
    try:
        yield
    finally:
        ls_mod.resolve_chat_model = real


class _TakeOutTheKey(logging.Filter):
    """Takes the key out of a log record, so a gateway that repeats the key in an error is safe."""

    def __init__(self, secret: str) -> None:
        super().__init__()
        self._secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage(), self._secret)
        record.args = None
        record.exc_info = record.exc_text = None
        return True


@contextlib.contextmanager
def logged(secret: str) -> Iterator[None]:
    """Show the warnings of the adapter on stderr (a refusal, a retry), with the key taken out."""
    logger = logging.getLogger("pi_agent_core")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("  log: %(message)s"))
    handler.addFilter(_TakeOutTheKey(secret))
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        logger.setLevel(level)
        logger.removeHandler(handler)


# ---------------------------------------------------------------------------
# Running the task
# ---------------------------------------------------------------------------


def _turn_of(message: Any, number: int, seconds: float) -> Turn:
    blocks = [block for block in message.content or [] if isinstance(block, dict)]
    return Turn(
        number=number,
        seconds=seconds,
        thinking="\n".join(
            str(block.get("thinking") or "") for block in blocks if block.get("type") == "thinking"
        ),
        text="".join(
            str(block.get("text") or "") for block in blocks if block.get("type") == "text"
        ),
        tool_calls=[
            (str(block.get("name")), json.dumps(block.get("arguments"), ensure_ascii=False))
            for block in blocks
            if block.get("type") == "toolCall"
        ],
        output_tokens=message.usage.output,
        stop_reason=str(message.stopReason),
        error=message.errorMessage,
    )


async def run_one(settings: Settings, *, replay: bool) -> Run:
    """Run the task once through the agent loop, with ``replay_reasoning`` on or off."""
    run = Run(replay=replay)
    recorder = Recorder()
    started: list[float] = []  # the start of the turn that is running (none, or one item)

    async def emit(event: Any) -> None:
        message = getattr(event, "message", None)
        if getattr(message, "role", None) != "assistant":
            return
        if event.type == "message_start":
            started[:] = [time.monotonic()]
        elif event.type == "message_end":
            seconds = time.monotonic() - started[0] if started else 0.0
            started.clear()
            run.turns.append(_turn_of(message, len(run.turns) + 1, seconds))

    model = Model(
        provider=settings.provider,
        model_id=settings.model_id,
        base_url=settings.base_url,
        reasoning=True,
        replay_reasoning=replay,
    )
    config = AgentLoopConfig(
        model=model,
        convert_to_llm=default_convert_to_llm,
        api_key=settings.api_key,
        thinking_level=settings.thinking_level,  # type: ignore[arg-type]
        max_turns=settings.hops + 6,
    )
    context = AgentContext(
        system_prompt=SYSTEM_PROMPT, messages=[], tools=[lookup_tool(settings.hops)]
    )
    # The agent keeps the refusals that it saw for the life of the process. Each run starts clean.
    ls_mod._REPLAY_REFUSED.clear()
    try:
        with recording(recorder):
            await asyncio.wait_for(
                run_agent_loop([UserMessage(content=build_prompt())], context, config, emit),
                timeout=settings.timeout,
            )
    except Exception as exc:
        run.crash = f"{type(exc).__name__}: {exc}"
    run.requests = recorder.requests
    return run


# ---------------------------------------------------------------------------
# Judging a run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    code: str
    text: str


def redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def one_line(text: str, limit: int = 300) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _was_transient(request: Request, run: Run) -> bool:
    """An error that is not a 400 or a 422, and a later request with the field worked.

    A busy gateway answers 429 or 5xx now and then. The agent asks again, and it works. That
    says nothing about the field.
    """
    if getattr(request.error, "status_code", None) in (400, 422):
        return False
    return any(
        later.number > request.number
        and later.thinking_messages >= request.thinking_messages
        and later.error is None
        for later in run.requests
    )


def judge_wire(run: Run, secret: str = "") -> Verdict:
    """What the gateway did with the field ``reasoning_content``."""
    sent = [request for request in run.requests if request.thinking_messages]
    if not sent:
        return Verdict("not_sent", "No request carried reasoning_content.")
    failed = [request for request in sent if request.error is not None]
    transient = [request for request in failed if _was_transient(request, run)]
    failed = [request for request in failed if request not in transient]
    if not failed:
        biggest = max(request.thinking_chars for request in sent)
        retried = f" {len(transient)} transient error(s) were retried." if transient else ""
        return Verdict(
            "accepted",
            f"{len(sent)} request(s) carried reasoning_content (up to {biggest} chars). "
            f"The gateway accepted them.{retried}",
        )
    first = failed[0]
    status = getattr(first.error, "status_code", None)
    message = one_line(redact(str(first.error), secret))
    head = (
        f"request {first.number} carried reasoning_content on {first.thinking_messages} "
        f"message(s) and failed: HTTP {status}: {message}"
    )
    if status not in (400, 422):
        return Verdict(
            "failed_other",
            f"{head}. This is not a 400 or a 422, so the field is probably not the cause.",
        )
    later = next((r for r in run.requests if r.number > first.number), None)
    asked_again = later is not None and later.thinking_messages == 0 and later.error is None
    if not ls_mod._is_replay_refusal(first.error):
        return Verdict(
            "failed_unnamed",
            f"{head}. The text does not name reasoning_content, so the agent does NOT take it "
            "for a refusal of the field. It does not ask again, and the turn fails.",
        )
    if asked_again:
        return Verdict(
            "refused_retried",
            f"{head}. The agent recognises the text, asked again without the field, "
            "and that worked.",
        )
    return Verdict(
        "refused_unrecovered",
        f"{head}. The agent recognises the text, but the request without the field "
        "did not work either.",
    )


def judge_required(run: Run, secret: str = "") -> Verdict | None:
    """Whether the gateway refused a request because it had no ``reasoning_content`` in it.

    DeepSeek's own API does this: it answers 400 to a request with tools whose history has no
    thinking. A gateway in front of it can pass that answer on.
    """
    for request in run.requests:
        if (
            request.assistant_messages
            and not request.thinking_messages
            and request.error is not None
            and ls_mod._is_replay_refusal(request.error)
        ):
            status = getattr(request.error, "status_code", None)
            message = one_line(redact(str(request.error), secret))
            return Verdict(
                "required",
                f"request {request.number} had {request.assistant_messages} assistant "
                f"message(s) and no reasoning_content, and failed: HTTP {status}: {message}. "
                "The gateway NEEDS the field.",
            )
    return None


def judge_memory(run: Run) -> Verdict:
    """Did the model see its own earlier thinking? It must say the two numbers that it chose."""
    if not run.turns or run.turns[-1].stop_reason != "stop":
        return Verdict("no_answer", "The run did not end with an answer. The test could not run.")
    if len(run.turns) < 2:
        return Verdict(
            "no_tool_call", "The model answered without a tool call. The test could not run."
        )
    first, last = run.turns[0], run.turns[-1]
    if not first.thinking.strip():
        return Verdict(
            "no_thinking",
            "The gateway returned no thinking for turn 1. There is nothing to send back.",
        )
    chosen = numbers_in(first.thinking)
    if not chosen:
        return Verdict(
            "no_number",
            "The thinking of turn 1 names no number from 1000 to 9999. The test could not run.",
        )
    said = numbers_in(
        " ".join(
            f"{turn.text} {' '.join(args for _, args in turn.tool_calls)}"
            for turn in run.turns[:-1]
        )
    )
    answered = numbers_in(last.text)
    hits = [number for number in answered if number in chosen]
    if hits and any(number in said for number in hits):
        return Verdict(
            "leaked",
            f"The number {hits[0]} was also in a visible text or a tool argument of an earlier "
            "turn, so the result is not clean.",
        )
    if len(hits) >= 2:
        return Verdict(
            "recalled",
            f"The answer gives {hits}, and the thinking of turn 1 had both. "
            "The model saw its earlier thinking.",
        )
    if hits:
        return Verdict(
            "partial",
            f"The answer gives {answered}. Only {hits[0]} is in the thinking of turn 1 {chosen}. "
            "That can be chance, because a model picks the same 'random' numbers twice. "
            "It is not counted.",
        )
    return Verdict(
        "forgot",
        f"The answer gives {answered or 'no number'}, but the thinking of turn 1 had {chosen}. "
        "The model did not see its earlier thinking.",
    )


# ---------------------------------------------------------------------------
# Writing the report
# ---------------------------------------------------------------------------


def describe_turn(turn: Turn, settings: Settings) -> str:
    head = (
        f"turn {turn.number}  {turn.seconds:5.1f} s  thinking {len(turn.thinking):5d} chars"
        f"  out {turn.output_tokens:5d} tok"
    )
    if turn.stop_reason in ("error", "aborted") or turn.error:
        reason = one_line(redact(turn.error or "", settings.api_key))
        return f"{head}  FAILED ({turn.stop_reason}): {reason}"
    if turn.tool_calls:
        return f"{head}  -> " + ", ".join(f"{name}({args})" for name, args in turn.tool_calls)
    numbers = [int(n) for n in re.findall(r"\d+", turn.text)]
    sum_ok = "sum ok" if expected_sum(settings.hops) in numbers else "sum WRONG"
    return f"{head}  -> {one_line(turn.text, 80)!r}  ({sum_ok})"


def run_title(replay: bool, index: int, total: int) -> str:
    """The first line of a run, printed before the run starts.

    The log lines of the adapter come while the run goes on. With the title first, they are
    under the title of their own run.
    """
    state = "on" if replay else "off"
    return f"--- run {index} of {total}: replay_reasoning = {state} ---"


def format_run(run: Run, settings: Settings, index: int, total: int) -> list[str]:
    """The title of a run, its turns, and what the gateway and the model did."""
    lines = [run_title(run.replay, index, total)]
    lines += [f"  {describe_turn(turn, settings)}" for turn in run.turns]
    if run.crash:
        lines.append(f"  RUN FAILED: {one_line(redact(run.crash, settings.api_key))}")
    elif run.turns and run.turns[-1].stop_reason not in ("stop", "error", "aborted"):
        lines.append(
            f"  RUN FAILED: the last turn stopped with {run.turns[-1].stop_reason!r}, "
            "not with an answer."
        )
    wire = judge_wire(run, settings.api_key)
    required = judge_required(run, settings.api_key)
    if run.replay or wire.code != "not_sent":
        lines.append(f"  wire:   [{wire.code}] {wire.text}")
    elif required is None:
        lines.append("  wire:   no request carried reasoning_content (as expected)")
    if required is not None:
        lines.append(f"  wire:   [{required.code}] {required.text}")
    memory = judge_memory(run)
    lines.append(f"  memory: [{memory.code}] {memory.text}")
    return lines


GATEWAY_LINES = {
    "accepted": "The gateway ACCEPTS reasoning_content.",
    "refused_retried": (
        "The gateway REFUSES reasoning_content. The agent recognises the error and asks again "
        "without the field."
    ),
    "refused_unrecovered": (
        "The gateway REFUSES reasoning_content. The agent recognises the error, but the request "
        "without the field failed too."
    ),
    "failed_unnamed": (
        "A request with reasoning_content failed with a 400 or a 422 that does not name the "
        "field. The agent does NOT take it for a refusal, so the turn fails. Read the text above."
    ),
    "failed_other": (
        "A request with reasoning_content failed with an error that is not a 400 or a 422. "
        "The field is probably not the cause."
    ),
    "not_sent": "No request carried reasoning_content. Nothing was tested.",
}


def _mean(values: Sequence[float]) -> str:
    return f"{sum(values) / len(values):.0f}" if values else "n/a"


def conclude(offs: Sequence[Run], ons: Sequence[Run], secret: str = "") -> list[str]:
    """The result over all runs, and what to do about it."""
    lines = ["=== Result ==="]
    wires = Counter(judge_wire(run, secret).code for run in ons)
    for code, count in wires.most_common():
        lines.append(f"gateway: {GATEWAY_LINES[code]} ({count} of {len(ons)} runs with replay on)")
    needing = [run for run in (*offs, *ons) if judge_required(run, secret) is not None]
    if needing:
        lines.append(
            "gateway: The gateway REQUIRES reasoning_content. It refused a request that had none "
            f"({len(needing)} of {len(offs) + len(ons)} runs)."
        )
    on_memory = Counter(judge_memory(run).code for run in ons)
    off_memory = Counter(judge_memory(run).code for run in offs)

    def tell(counter: Counter[str], total: int) -> str:
        other = total - counter["recalled"] - counter["forgot"]
        return (
            f"recalled both numbers in {counter['recalled']}, forgot them in {counter['forgot']}, "
            f"no clean result in {other} (of {total} runs)"
        )

    if ons:
        lines.append(f"model, replay on:  {tell(on_memory, len(ons))}")
    if offs:
        lines.append(f"model, replay off: {tell(off_memory, len(offs))}")
    if offs and ons:

        def last_thinking(runs: Sequence[Run]) -> list[float]:
            return [
                len(run.turns[-1].thinking)
                for run in runs
                if len(run.turns) >= 2 and not run.failed
            ]

        off_time = _mean([run.seconds for run in offs])
        on_time = _mean([run.seconds for run in ons])
        lines.append(
            "thinking of the last turn (chars, mean): "
            f"off {_mean(last_thinking(offs))}, on {_mean(last_thinking(ons))}. "
            f"Time of a run (s, mean): off {off_time}, on {on_time}. "
            "A few runs show a hint, not a measurement."
        )
    if any(run.failed and judge_required(run, secret) is None for run in offs):
        lines.append(
            "warning: a run with replay off failed too. Fix the setup first (gateway, model, key). "
            "The other results may not be about reasoning_content."
        )
    lines.append(f"advice: {advise(wires, on_memory, off_memory, required=bool(needing))}")
    return lines


def advise(
    wires: Counter[str],
    on_memory: Counter[str],
    off_memory: Counter[str],
    required: bool = False,
) -> str:
    if required:
        field_fails = (
            wires["failed_unnamed"]
            or wires["failed_other"]
            or wires["refused_retried"]
            or wires["refused_unrecovered"]
        )
        if not wires:
            return (
                "Set replay_reasoning = true for this model: the gateway refuses a request with "
                "no reasoning_content (see the text above). Run again without --only to check "
                "that the gateway takes the field."
            )
        if wires["accepted"] and not field_fails:
            return (
                "Set replay_reasoning = true for this model. The gateway refuses a request with "
                "no reasoning_content (see the text above), and it takes the field."
            )
        return (
            "The gateway refuses a request with no reasoning_content, and a request with the "
            "field did not work either. See the texts above."
        )
    if wires["failed_unnamed"] or wires["refused_unrecovered"] or wires["failed_other"]:
        return (
            "Keep replay_reasoning off for this gateway. With it on, a turn can fail. If the "
            "error text above is about reasoning_content, send it to the maintainers: the agent "
            "can then learn to recognise it. If it is about something else, the run with "
            "replay off probably fails the same way. Fix that first."
        )
    if wires["refused_retried"]:
        return (
            "Keep replay_reasoning off for this gateway. The agent would recover, but only "
            "after one failed request."
        )
    if not wires or set(wires) == {"not_sent"}:
        return "Nothing was tested. Check that the model returns thinking and that the run ended."
    if off_memory["recalled"]:
        return (
            "The model gave its numbers also with replay off, so it works them out again by "
            "itself. This test cannot show a gain. Compare the thinking size and the time above, "
            "or use --hops 3."
        )
    if on_memory["recalled"] and not on_memory["forgot"]:
        if off_memory["forgot"]:
            return (
                "Set replay_reasoning = true for this model. The gateway takes the thinking, "
                "and the model uses it (with replay off, the model forgot)."
            )
        return (
            "The model used its thinking with replay on. There is no clean run with replay off "
            "to compare with, so chance is not excluded. Run again without --only."
        )
    if on_memory["forgot"] and not on_memory["recalled"]:
        return (
            "The gateway takes the field, but the model does not use it. replay_reasoning gives "
            "no gain here. Keep it off."
        )
    return "The result is mixed or not clean. Run again with --repeat 3."


def fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ask a real gateway whether it takes reasoning_content back, and whether "
        "the model uses it. See the top of this file.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("PROBE_BASE_URL") or os.environ.get("REAL_LLM_BASE_URL"),
        help="the gateway, as in agent.toml (default: $PROBE_BASE_URL or $REAL_LLM_BASE_URL)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("PROBE_MODEL") or os.environ.get("REAL_LLM_MODEL"),
        help="the model id, as in agent.toml (default: $PROBE_MODEL or $REAL_LLM_MODEL)",
    )
    parser.add_argument("--provider", choices=("openai", "deepseek"), default="openai")
    parser.add_argument(
        "--api-key-env",
        default="REAL_LLM_API_KEY",
        help="the variable that holds the key (default: REAL_LLM_API_KEY)",
    )
    parser.add_argument("--thinking-level", choices=THINKING_LEVELS, default="high")
    parser.add_argument(
        "--hops", type=int, default=1, help=f"tool calls in the task, 1 to {len(CHAIN)}"
    )
    parser.add_argument("--repeat", type=int, default=1, help="runs for each setting")
    parser.add_argument("--only", choices=("both", "on", "off"), default="both")
    parser.add_argument("--timeout", type=float, default=180.0, help="seconds for one run")
    args = parser.parse_args(argv)
    if not args.base_url or not args.model:
        parser.error(
            "--base-url and --model are needed (or $REAL_LLM_BASE_URL and $REAL_LLM_MODEL)"
        )
    if urlparse(args.base_url).scheme not in ("http", "https"):
        parser.error("--base-url must start with http:// or https://")
    if not 1 <= args.hops <= len(CHAIN):
        parser.error(f"--hops must be from 1 to {len(CHAIN)}")
    if args.repeat < 1:
        parser.error("--repeat must be 1 or more")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    key = os.environ.get(args.api_key_env, "")
    if not key:
        print(
            f"error: ${args.api_key_env} is not set. The key is read from the environment.",
            file=sys.stderr,
        )
        return 2
    settings = Settings(
        provider=args.provider,
        model_id=args.model,
        base_url=args.base_url,
        api_key=key,
        thinking_level=args.thinking_level,
        hops=args.hops,
        timeout=args.timeout,
    )
    probe_model = Model(provider=args.provider, model_id=args.model, base_url=args.base_url)
    only = args.only
    url = urlparse(args.base_url)
    host = f"{url.hostname}:{url.port}" if url.port else url.hostname
    print(
        f"Gateway: {host}  provider: {args.provider}  "
        f"model: {args.model}  thinking: {args.thinking_level}"
    )
    print(f"Key:     ${args.api_key_env}, fingerprint {fingerprint(key)} (the key is not printed)")
    print(
        f"Task:    {args.hops} tool call(s), {args.hops + 1} turns. "
        "The model must keep two secret numbers in its thinking."
    )
    if ls_mod._replay_is_required(probe_model):
        print(
            "Note:    DeepSeek's own API always gets the thinking back. The 'off' run is skipped."
        )
        only = "on"
    print()

    offs: list[Run] = []
    ons: list[Run] = []
    with logged(key):
        for index in range(1, args.repeat + 1):
            for replay in (False, True):
                if (replay and only == "off") or (not replay and only == "on"):
                    continue
                print(run_title(replay, index, args.repeat), flush=True)
                run = asyncio.run(run_one(settings, replay=replay))
                (ons if replay else offs).append(run)
                print("\n".join(format_run(run, settings, index, args.repeat)[1:]), flush=True)
                print()
    print("\n".join(conclude(offs, ons, key)))
    return 1 if any(run.failed for run in (*offs, *ons)) else 0


if __name__ == "__main__":
    sys.exit(main())
