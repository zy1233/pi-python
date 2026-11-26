"""``scripts/probe_reasoning_replay.py`` against a fake gateway (no network).

A user runs the probe against a real gateway and acts on what it says. So each verdict must come
for the right reason. Here a fake gateway acts as each kind of gateway that the probe has to tell
apart: one that takes the field and its model uses it, one that takes the field and drops it, one
that refuses it, and so on. The probe runs the real agent loop and the real chat class. Only the
HTTP call is fake.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langchain_openai")

import openai

from pi_agent_core.tests.fake_gateway import GATEWAY_URL, Stream, api_error, chunk, wire_up

ROOT = Path(__file__).resolve().parents[2]
# A mutation check sets PROBE_SCRIPT to a changed copy of the script.
SCRIPT = Path(os.environ.get("PROBE_SCRIPT") or ROOT / "scripts" / "probe_reasoning_replay.py")


def _load():
    spec = importlib.util.spec_from_file_location("probe_reasoning_replay", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(module)
    return module


probe = _load()
ls_mod = importlib.import_module("pi_agent_core.adapters.langchain_stream")

KEY = "sk-probe-FAKE-0123456789abcdef"
SECRETS = (4827, 7391)  # the numbers that the fake model picks in the thinking of turn 1
MADE_UP = (2468, 1357)  # the numbers that it makes up when it cannot read that thinking
FIELD_ERROR = "Unknown parameter: 'messages[1].reasoning_content'."

_ENVIRONMENT = (
    "REAL_LLM_API_KEY",
    "REAL_LLM_BASE_URL",
    "REAL_LLM_MODEL",
    "PROBE_BASE_URL",
    "PROBE_MODEL",
    "DEEPSEEK_API_BASE",
)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch):
    """A shell of a developer may set these. The refusals that were seen outlive a test."""
    for name in _ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    ls_mod._REPLAY_REFUSED.clear()
    yield
    ls_mod._REPLAY_REFUSED.clear()


# ---------------------------------------------------------------------------
# The fake gateway
# ---------------------------------------------------------------------------


def _round(thinking: str | None, text: str, call: tuple[str, str] | None = None) -> list[dict]:
    """The chunks of one streamed reply: thinking, visible text, a call of ``lookup``."""
    first: dict[str, Any] = {"role": "assistant", "content": ""}
    if thinking is not None:
        first["reasoning_content"] = thinking
    chunks = [chunk(first)]
    if text:
        chunks.append(chunk({"content": text}))
    if call is not None:
        call_id, key = call
        function = {"name": "lookup", "arguments": json.dumps({"key": key})}
        call_delta = {"index": 0, "id": call_id, "type": "function", "function": function}
        chunks.append(chunk({"tool_calls": [call_delta]}))
    chunks.append(chunk({}, finish="tool_calls" if call else "stop"))
    return chunks


@dataclass
class Gateway:
    """Stands in for ``async_client``: a gateway with a model that does the task of the probe.

    The model picks ``SECRETS`` in the thinking of turn 1, calls ``lookup`` for each key of the
    chain, and then says the sum and two numbers. It says ``SECRETS`` only if a
    ``reasoning_content`` of the request shows them to it (or if ``rederives``). The other
    fields set how the gateway or the model acts.
    """

    hops: int = 1
    thinks: bool = True  # the gateway returns thinking at all
    names_secrets: bool = True  # the thinking of turn 1 names the two numbers
    sees_replay: bool = True  # the model can read the field that a request carries
    rederives: bool = False  # the model picks the same numbers again, with no help
    leaks: bool = False  # the model writes its numbers in the visible text of turn 1
    calls_tool: bool = True
    delay: float = 0.0  # seconds before each reply
    refuse: Callable[[dict], BaseException | None] | None = None
    requests: list[dict] = field(default_factory=list)

    async def create(self, **payload: Any) -> Stream:
        self.requests.append(payload)
        if self.delay:
            await asyncio.sleep(self.delay)
        error = self.refuse(payload) if self.refuse is not None else None
        if error is not None:
            raise error
        return Stream(self._reply(payload["messages"]))

    def _reply(self, messages: list[dict]) -> list[dict]:
        assistant = [m for m in messages if m.get("role") == "assistant"]
        carried = " ".join(str(m.get("reasoning_content") or "") for m in assistant)
        done = sum(1 for m in messages if m.get("role") == "tool")
        sees = self.sees_replay and all(str(n) in carried for n in SECRETS)
        if done == 0:
            thinking = (
                f"I pick {SECRETS[0]} and {SECRETS[1]}. Now look up alpha."
                if self.names_secrets
                else "I will pick two numbers later. Now look up alpha."
            )
            visible = f"My numbers are {SECRETS[0]} and {SECRETS[1]}." if self.leaks else ""
        else:
            thinking = "Next step." if done < self.hops else "Wrap up."
            visible = ""
        reasoning = thinking if self.thinks else None
        if self.calls_tool and done < self.hops:
            return _round(reasoning, visible, (f"call_{done + 1}", probe.CHAIN[done][0]))
        total = sum(value for _, value in probe.CHAIN[: self.hops])
        numbers = SECRETS if (done == 0 or sees or self.rederives) else MADE_UP
        return _round(reasoning, f"{total}, {numbers[0]}, {numbers[1]}")


def refusal(status: int = 400) -> openai.APIStatusError:
    """The error of a gateway that checks the fields of a message."""
    return api_error(status, FIELD_ERROR)


def carries_thinking(payload: dict) -> bool:
    return any(
        "reasoning_content" in message
        for message in payload["messages"]
        if message.get("role") == "assistant"
    )


def strict(error: Callable[[], BaseException]) -> Callable[[dict], BaseException | None]:
    """A gateway that answers *error* to a request that carries ``reasoning_content``."""
    return lambda payload: error() if carries_thinking(payload) else None


NEEDS_TEXT = "Missing reasoning_content field in the assistant message at message index 2."


def needs_thinking(status: int = 400) -> Callable[[dict], BaseException | None]:
    """A gateway like DeepSeek's own API: it refuses a request whose history has no thinking."""

    def refuse(payload: dict) -> BaseException | None:
        assistant = [m for m in payload["messages"] if m.get("role") == "assistant"]
        if any("reasoning_content" not in message for message in assistant):
            return api_error(status, NEEDS_TEXT)
        return None

    return refuse


def settings(**changes: Any) -> Any:
    values: dict[str, Any] = {
        "provider": "openai",
        "model_id": "deepseek-flash",
        "base_url": GATEWAY_URL,
        "api_key": KEY,
        "hops": 1,
        "timeout": 30.0,
    }
    return probe.Settings(**{**values, **changes})


async def both_runs(monkeypatch: pytest.MonkeyPatch, gateway: Gateway, **changes: Any):
    """The run with replay off and the run with replay on, against one gateway."""
    wire_up(monkeypatch, gateway)
    off = await probe.run_one(settings(**changes), replay=False)
    on = await probe.run_one(settings(**changes), replay=True)
    return off, on


# ---------------------------------------------------------------------------
# Runs that a test builds by hand
# ---------------------------------------------------------------------------


def turn(
    number: int = 1,
    *,
    thinking: str = "",
    text: str = "",
    calls: tuple[tuple[str, str], ...] = (),
    stop: str = "toolUse",
    error: str | None = None,
    seconds: float = 1.0,
) -> Any:
    return probe.Turn(
        number=number,
        seconds=seconds,
        thinking=thinking,
        text=text,
        tool_calls=list(calls),
        output_tokens=0,
        stop_reason=stop,
        error=error,
    )


def two_turns(
    *,
    thinking: str = "I pick 4827 and 7391.",
    visible: str = "",
    args: str = '{"key": "alpha"}',
    answer: str = "7, 4827, 7391",
) -> Any:
    """Turn 1 thinks and calls the tool. Turn 2 answers."""
    run = probe.Run(replay=True)
    run.turns = [
        turn(1, thinking=thinking, text=visible, calls=(("lookup", args),)),
        turn(2, thinking="wrap up", text=answer, stop="stop"),
    ]
    return run


def request(
    number: int, thinking: int, *, chars: int = 10, error: Any = None, assistant: int = 0
) -> Any:
    """A request of a run: *thinking* of its *assistant* messages carry ``reasoning_content``."""
    return probe.Request(
        number=number,
        thinking_messages=thinking,
        thinking_chars=chars,
        error=error,
        assistant_messages=max(assistant, thinking),
    )


def wire_run(*requests: Any) -> Any:
    run = probe.Run(replay=True)
    run.requests = list(requests)
    return run


# ---------------------------------------------------------------------------
# The task
# ---------------------------------------------------------------------------


class TestTheTask:
    def test_the_prompt_asks_for_two_numbers_that_stay_in_the_thinking(self):
        prompt = probe.build_prompt()

        assert "two different random whole numbers" in prompt
        assert "thinking only" in prompt
        assert "one call at a time" in prompt

    def test_the_tool_walks_the_chain(self):
        assert probe.lookup_text("alpha", 3) == "value=7 next=bravo"
        assert probe.lookup_text(" Charlie ", 3) == "value=5 next=NONE"
        assert probe.lookup_text("alpha", 1) == "value=7 next=NONE"
        assert probe.lookup_text("delta", 3).startswith("error: unknown key")

    @pytest.mark.parametrize(("hops", "total"), [(1, 7), (3, 24), (6, 44)])
    def test_the_sum_of_the_values(self, hops: int, total: int):
        assert probe.expected_sum(hops) == total

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("My numbers are 4827 and 7391.", [4827, 7391]),
            ("7, 4827, 7391", [4827, 7391]),
            ("7,4827,7391", [4827, 7391]),
            ("4,827", [4827]),
            ("4827 4827 4827", [4827]),
            ("from 1000 to 9999", []),
            ("12345 and 0123 and 999", []),
            ("", []),
        ],
    )
    def test_the_numbers_of_four_digits_in_a_text(self, text: str, expected: list[int]):
        assert probe.numbers_in(text) == expected

    def test_a_key_is_not_in_the_repr_of_the_settings(self):
        assert KEY not in repr(settings())

    def test_the_fingerprint_does_not_show_the_key(self):
        shown = probe.fingerprint(KEY)

        assert len(shown) == 12
        assert shown != KEY[:12]
        assert shown == probe.fingerprint(KEY)
        assert shown != probe.fingerprint(KEY + "x")


class TestTheRun:
    @pytest.mark.parametrize(
        ("stop", "error", "failed"),
        [
            ("stop", None, False),
            ("toolUse", None, True),
            ("length", None, True),
            ("error", "boom", True),
            ("aborted", None, True),
            ("stop", "boom", True),
        ],
    )
    def test_a_run_has_failed_unless_its_last_turn_is_an_answer(
        self, stop: str, error: str | None, failed: bool
    ):
        run = probe.Run(replay=True)
        run.turns = [turn(1, stop=stop, error=error)]

        assert run.failed is failed

    def test_a_run_with_no_turn_has_failed(self):
        assert probe.Run(replay=True).failed

    def test_a_run_that_crashed_has_failed_whatever_its_turns_say(self):
        run = two_turns()
        assert not run.failed

        run.crash = "TimeoutError: "

        assert run.failed

    def test_a_run_adds_up_the_time_of_its_turns(self):
        run = probe.Run(replay=True)
        run.turns = [turn(1, seconds=1.5), turn(2, seconds=2.0, stop="stop")]

        assert run.seconds == 3.5

    def test_a_run_that_stopped_without_an_answer_says_why(self):
        run = probe.Run(replay=True)
        run.turns = [turn(1, text="cut", stop="length")]

        lines = probe.format_run(run, settings(), 1, 1)

        assert any("RUN FAILED: the last turn stopped with 'length'" in line for line in lines)


# ---------------------------------------------------------------------------
# The record of the requests
# ---------------------------------------------------------------------------


class _Inner:
    base_url = "http://inner.example.test/v1"

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error

    async def create(self, **payload: Any) -> str:
        if self.error is not None:
            raise self.error
        return "stream"


class TestTheRecorder:
    async def test_it_counts_the_thinking_that_a_request_carries(self):
        recorder = probe.Recorder()
        client = recorder.wrap(_Inner())
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u", "reasoning_content": "not counted"},
            {"role": "assistant", "content": "a", "reasoning_content": "abc"},
            {"role": "assistant", "content": "b", "reasoning_content": None},
            {"role": "assistant", "content": "c"},
            {"role": "tool", "content": "t", "reasoning_content": "not counted"},
        ]

        assert await client.create(messages=messages, stream=True) == "stream"

        [first] = recorder.requests
        assert (first.number, first.thinking_messages, first.thinking_chars) == (1, 2, 3)
        assert first.error is None
        assert client.base_url == "http://inner.example.test/v1"  # the rest passes through

    async def test_it_numbers_the_requests_and_keeps_the_error(self):
        recorder = probe.Recorder()
        failing = _Inner(error=ValueError("boom"))
        client = recorder.wrap(failing)

        await recorder.wrap(_Inner()).create(messages=[])
        with pytest.raises(ValueError, match="boom"):
            await client.create(messages=[])

        assert [r.number for r in recorder.requests] == [1, 2]
        assert recorder.requests[0].error is None
        assert recorder.requests[1].error is failing.error


# ---------------------------------------------------------------------------
# What the gateway did with the field
# ---------------------------------------------------------------------------


class TestTheWireVerdict:
    def test_a_run_that_sent_nothing(self):
        verdict = probe.judge_wire(wire_run(request(1, 0), request(2, 0)))

        assert verdict.code == "not_sent"

    def test_a_gateway_that_takes_the_field(self):
        verdict = probe.judge_wire(wire_run(request(1, 0), request(2, 1, chars=12)))

        assert verdict.code == "accepted"
        assert "up to 12 chars" in verdict.text

    def test_the_biggest_request_sets_the_size(self):
        run = wire_run(
            request(1, 0), request(2, 1, chars=12), request(3, 2, chars=30), request(4, 3, chars=21)
        )

        verdict = probe.judge_wire(run)

        assert verdict.code == "accepted"
        assert "3 request(s)" in verdict.text and "up to 30 chars" in verdict.text

    def test_a_busy_gateway_is_not_a_gateway_that_refuses(self):
        """429 now and then: the agent asked again with the field, and that worked."""
        run = wire_run(
            request(1, 0), request(2, 1, error=api_error(429, "slow down")), request(3, 1)
        )

        verdict = probe.judge_wire(run)

        assert verdict.code == "accepted"
        assert "1 transient error(s) were retried" in verdict.text

    def test_an_error_that_no_later_request_answers_is_a_failure(self):
        run = wire_run(request(1, 0), request(2, 1, error=api_error(503, "down")))

        assert probe.judge_wire(run).code == "failed_other"

    def test_two_busy_answers_in_a_row_are_a_failure_and_the_first_is_named(self):
        run = wire_run(
            request(1, 0),
            request(2, 1, error=api_error(429, "slow down")),
            request(3, 1, error=api_error(429, "slow down")),
        )

        verdict = probe.judge_wire(run)

        assert verdict.code == "failed_other"
        assert "request 2 " in verdict.text and "HTTP 429" in verdict.text

    def test_a_later_request_without_the_field_does_not_show_that_the_field_works(self):
        """The retry that worked had no ``reasoning_content`` in it: it says nothing about it."""
        run = wire_run(
            request(1, 0), request(2, 1, error=api_error(429, "slow down")), request(3, 0)
        )

        assert probe.judge_wire(run).code == "failed_other"

    @pytest.mark.parametrize("status", [400, 422])
    def test_a_gateway_that_refuses_the_field_and_the_agent_asks_again(self, status: int):
        run = wire_run(request(1, 0), request(2, 1, error=refusal(status)), request(3, 0))

        verdict = probe.judge_wire(run)

        assert verdict.code == "refused_retried"
        assert f"HTTP {status}" in verdict.text
        assert "reasoning_content" in verdict.text  # the raw text of the error is in the report

    def test_a_refusal_after_which_the_request_without_the_field_fails_too(self):
        run = wire_run(
            request(1, 0),
            request(2, 1, error=refusal()),
            request(3, 0, error=api_error(500, "boom")),
        )

        assert probe.judge_wire(run).code == "refused_unrecovered"

    def test_a_400_that_does_not_name_the_field(self):
        run = wire_run(request(1, 0), request(2, 1, error=api_error(400, "context too long")))

        verdict = probe.judge_wire(run)

        assert verdict.code == "failed_unnamed"
        assert "context too long" in verdict.text

    @pytest.mark.parametrize("status", [401, 404, 500])
    def test_an_error_that_is_not_a_400_or_a_422(self, status: int):
        run = wire_run(request(1, 0), request(2, 1, error=api_error(status, "no")))

        assert probe.judge_wire(run).code == "failed_other"

    def test_a_500_that_names_the_field_is_still_not_a_refusal(self):
        run = wire_run(request(1, 0), request(2, 1, error=api_error(500, FIELD_ERROR)))

        assert probe.judge_wire(run).code == "failed_other"

    def test_the_key_is_not_in_the_text(self):
        error = api_error(400, f"bad key {KEY} in a request")
        run = wire_run(request(1, 0), request(2, 1, error=error))

        verdict = probe.judge_wire(run, KEY)

        assert KEY not in verdict.text
        assert "***" in verdict.text


class TestTheRequiredVerdict:
    """A gateway that refuses a request with no thinking in its history (DeepSeek's API does)."""

    @pytest.mark.parametrize("status", [400, 422])
    def test_a_refusal_of_a_request_without_the_field(self, status: int):
        error = api_error(status, NEEDS_TEXT)
        run = wire_run(request(1, 0), request(2, 0, assistant=1, error=error))

        verdict = probe.judge_required(run)

        assert verdict is not None and verdict.code == "required"
        assert "request 2 " in verdict.text and f"HTTP {status}" in verdict.text
        assert "NEEDS the field" in verdict.text and "Missing reasoning_content" in verdict.text

    @pytest.mark.parametrize(
        "run",
        [
            wire_run(),
            wire_run(request(1, 0), request(2, 0, assistant=1)),
            # the first request has no earlier assistant message, so no field is missing
            wire_run(request(1, 0, error=api_error(400, NEEDS_TEXT))),
            # the field was sent
            wire_run(request(1, 0), request(2, 1, error=api_error(400, NEEDS_TEXT))),
            # an error that does not name the field
            wire_run(request(2, 0, assistant=1, error=api_error(400, "context too long"))),
            # not a 400 or a 422
            wire_run(request(2, 0, assistant=1, error=api_error(500, NEEDS_TEXT))),
        ],
    )
    def test_the_other_runs_are_not_this(self, run: Any):
        assert probe.judge_required(run) is None

    def test_the_key_is_not_in_the_text(self):
        error = api_error(400, f"{NEEDS_TEXT} Key {KEY}.")
        run = wire_run(request(2, 0, assistant=1, error=error))

        verdict = probe.judge_required(run, KEY)

        assert verdict is not None and KEY not in verdict.text and "***" in verdict.text


# ---------------------------------------------------------------------------
# Whether the model saw its own thinking
# ---------------------------------------------------------------------------


class TestTheMemoryVerdict:
    @pytest.mark.parametrize(
        ("changes", "code"),
        [
            ({}, "recalled"),
            ({"answer": "7, 7391, 4827"}, "recalled"),
            ({"answer": "7,4827,7391"}, "recalled"),
            ({"answer": "7, 4,827 and 7,391"}, "recalled"),
            ({"answer": "7, 2468, 1357"}, "forgot"),
            ({"answer": "7"}, "forgot"),
            ({"answer": "7, 4827, 1357"}, "partial"),
            ({"visible": "My numbers are 4827 and 7391."}, "leaked"),
            ({"args": '{"key": "4827"}'}, "leaked"),
            ({"thinking": ""}, "no_thinking"),
            ({"thinking": "  \n "}, "no_thinking"),
            ({"thinking": "I pick two numbers."}, "no_number"),
        ],
    )
    def test_the_verdict(self, changes: dict[str, str], code: str):
        assert probe.judge_memory(two_turns(**changes)).code == code

    def test_the_numbers_of_the_prompt_do_not_count(self):
        verdict = probe.judge_memory(two_turns(thinking="Range 1000 to 9999."))

        assert verdict.code == "no_number"

    def test_a_forgetful_model_is_told_with_the_numbers(self):
        verdict = probe.judge_memory(two_turns(answer="7, 2468, 1357"))

        assert "2468" in verdict.text and "4827" in verdict.text

    def test_no_turns_is_no_answer(self):
        assert probe.judge_memory(probe.Run(replay=True)).code == "no_answer"

    @pytest.mark.parametrize("stop", ["error", "aborted", "length"])
    def test_a_last_turn_that_did_not_end_with_an_answer_is_no_answer(self, stop: str):
        run = two_turns()
        run.turns[-1] = turn(2, text="7, 4827, 7391", stop=stop)

        assert probe.judge_memory(run).code == "no_answer"

    def test_an_answer_with_no_tool_call(self):
        run = probe.Run(replay=True)
        run.turns = [turn(1, thinking="I pick 4827 and 7391.", text="7, 4827, 7391", stop="stop")]

        assert probe.judge_memory(run).code == "no_tool_call"


# ---------------------------------------------------------------------------
# One run through the real agent loop
# ---------------------------------------------------------------------------


class TestOneRun:
    async def test_with_replay_off_nothing_goes_back_and_the_model_forgets(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        wire_up(monkeypatch, gateway := Gateway())

        run = await probe.run_one(settings(), replay=False)

        assert not run.failed
        assert [t.stop_reason for t in run.turns] == ["toolUse", "stop"]
        assert run.turns[0].tool_calls == [("lookup", '{"key": "alpha"}')]
        assert run.turns[0].thinking == "I pick 4827 and 7391. Now look up alpha."
        assert [r.thinking_messages for r in run.requests] == [0, 0]
        assert "reasoning_content" not in json.dumps(gateway.requests[1]["messages"])
        assert probe.judge_wire(run).code == "not_sent"
        assert probe.judge_memory(run).code == "forgot"

    async def test_with_replay_on_the_thinking_goes_back_and_the_model_uses_it(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        wire_up(monkeypatch, Gateway())

        run = await probe.run_one(settings(), replay=True)

        assert not run.failed
        assert [r.thinking_messages for r in run.requests] == [0, 1]
        assert run.requests[1].thinking_chars == len(run.turns[0].thinking)
        assert probe.judge_wire(run).code == "accepted"
        assert probe.judge_memory(run).code == "recalled"
        assert run.turns[-1].text == "7, 4827, 7391"

    async def test_a_gateway_that_takes_the_field_and_drops_it(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        wire_up(monkeypatch, Gateway(sees_replay=False))

        run = await probe.run_one(settings(), replay=True)

        assert probe.judge_wire(run).code == "accepted"
        assert probe.judge_memory(run).code == "forgot"

    async def test_a_gateway_that_refuses_the_field_is_asked_again_without_it(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        wire_up(monkeypatch, Gateway(refuse=strict(refusal)))

        run = await probe.run_one(settings(), replay=True)

        assert not run.failed
        assert [r.thinking_messages for r in run.requests] == [0, 1, 0]
        assert run.requests[1].error is not None
        assert run.requests[2].error is None
        verdict = probe.judge_wire(run)
        assert verdict.code == "refused_retried"
        assert FIELD_ERROR in verdict.text
        assert probe.judge_memory(run).code == "forgot"

    async def test_a_422_refusal_is_told_the_same_way(self, monkeypatch: pytest.MonkeyPatch):
        wire_up(monkeypatch, Gateway(refuse=strict(lambda: refusal(422))))

        run = await probe.run_one(settings(), replay=True)

        assert not run.failed
        assert probe.judge_wire(run).code == "refused_retried"

    async def test_a_refusal_that_does_not_name_the_field_fails_the_run(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        wire_up(monkeypatch, Gateway(refuse=strict(lambda: api_error(400, "context too long"))))

        run = await probe.run_one(settings(), replay=True)

        assert run.failed
        assert run.turns[-1].stop_reason == "error"
        assert probe.judge_wire(run).code == "failed_unnamed"
        assert probe.judge_memory(run).code == "no_answer"

    async def test_an_error_that_is_not_a_400_or_a_422(self, monkeypatch: pytest.MonkeyPatch):
        wire_up(monkeypatch, Gateway(refuse=strict(lambda: api_error(401, "bad key"))))

        run = await probe.run_one(settings(), replay=True)

        assert run.failed
        assert probe.judge_wire(run).code == "failed_other"

    async def test_more_hops_send_more_thinking_back(self, monkeypatch: pytest.MonkeyPatch):
        wire_up(monkeypatch, gateway := Gateway(hops=3))

        run = await probe.run_one(settings(hops=3), replay=True)

        last_request = gateway.requests[-1]["messages"]
        assert [m["content"] for m in last_request if m["role"] == "tool"] == [
            "value=7 next=bravo",
            "value=12 next=charlie",
            "value=5 next=NONE",
        ]

        assert [t.stop_reason for t in run.turns] == ["toolUse", "toolUse", "toolUse", "stop"]
        assert [r.thinking_messages for r in run.requests] == [0, 1, 2, 3]
        assert [t.tool_calls[0][1] for t in run.turns[:3]] == [
            '{"key": "alpha"}',
            '{"key": "bravo"}',
            '{"key": "charlie"}',
        ]
        assert run.turns[-1].text.startswith("24,")
        assert probe.judge_memory(run).code == "recalled"

    @pytest.mark.parametrize(
        ("gateway", "code"),
        [
            (Gateway(thinks=False), "no_thinking"),
            (Gateway(names_secrets=False), "no_number"),
            (Gateway(calls_tool=False), "no_tool_call"),
            (Gateway(leaks=True), "leaked"),
        ],
    )
    async def test_a_run_that_cannot_tell_says_so(
        self, monkeypatch: pytest.MonkeyPatch, gateway: Gateway, code: str
    ):
        wire_up(monkeypatch, gateway)

        run = await probe.run_one(settings(), replay=True)

        assert probe.judge_memory(run).code == code

    async def test_a_gateway_that_needs_the_field_fails_the_run_with_replay_off(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        wire_up(monkeypatch, Gateway(refuse=needs_thinking()))

        off = await probe.run_one(settings(), replay=False)
        on = await probe.run_one(settings(), replay=True)

        assert off.failed
        required = probe.judge_required(off)
        assert required is not None and required.code == "required"
        assert probe.judge_wire(off).code == "not_sent"
        assert not on.failed
        assert probe.judge_required(on) is None
        assert probe.judge_wire(on).code == "accepted"
        assert probe.judge_memory(on).code == "recalled"

    async def test_each_run_starts_without_the_refusals_of_the_run_before(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The agent keeps a refusal for the life of the process. The probe must not."""
        wire_up(monkeypatch, Gateway(refuse=strict(refusal)))

        first = await probe.run_one(settings(), replay=True)
        second = await probe.run_one(settings(), replay=True)

        assert probe.judge_wire(first).code == "refused_retried"
        assert probe.judge_wire(second).code == "refused_retried"

    async def test_the_chat_model_is_put_back_after_a_run(self, monkeypatch: pytest.MonkeyPatch):
        wire_up(monkeypatch, Gateway())
        before = ls_mod.resolve_chat_model

        await probe.run_one(settings(), replay=True)

        assert ls_mod.resolve_chat_model is before

    async def test_a_run_that_takes_too_long_is_a_failure_and_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        wire_up(monkeypatch, Gateway(delay=2.0))
        before = ls_mod.resolve_chat_model

        run = await probe.run_one(settings(timeout=0.1), replay=True)

        assert run.failed
        assert run.crash is not None and run.crash.startswith("TimeoutError")
        assert ls_mod.resolve_chat_model is before


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


class TestTheReport:
    async def test_a_run_is_written_turn_by_turn(self, monkeypatch: pytest.MonkeyPatch):
        off, on = await both_runs(monkeypatch, Gateway())

        off_lines = probe.format_run(off, settings(), 1, 2)
        on_lines = probe.format_run(on, settings(), 1, 2)

        assert off_lines[0] == "--- run 1 of 2: replay_reasoning = off ---"
        assert on_lines[0] == "--- run 1 of 2: replay_reasoning = on ---"
        assert 'lookup({"key": "alpha"})' in off_lines[1]
        assert "(sum ok)" in off_lines[2]
        assert any("no request carried reasoning_content (as expected)" in s for s in off_lines)
        assert any("[accepted]" in s for s in on_lines)
        assert any("[recalled]" in s for s in on_lines)

    def test_a_wrong_sum_is_marked(self):
        wrong = turn(2, text="8, 4827, 7391", stop="stop")

        assert "(sum WRONG)" in probe.describe_turn(wrong, settings())

    def test_a_failed_turn_is_marked_and_the_key_is_taken_out(self):
        failed = turn(2, stop="error", error=f"401 for key {KEY}")

        line = probe.describe_turn(failed, settings())

        assert "FAILED (error)" in line
        assert KEY not in line and "***" in line

    @pytest.mark.parametrize(
        ("make", "advice"),
        [
            (lambda: Gateway(), "Set replay_reasoning = true"),
            (lambda: Gateway(sees_replay=False), "gives no gain here"),
            (lambda: Gateway(rederives=True), "also with replay off"),
            (lambda: Gateway(refuse=strict(refusal)), "only after one failed request"),
            (lambda: Gateway(refuse=strict(lambda: api_error(400, "too long"))), "can fail"),
            (lambda: Gateway(refuse=strict(lambda: api_error(401, "no"))), "can fail"),
        ],
    )
    async def test_the_advice(
        self, monkeypatch: pytest.MonkeyPatch, make: Callable[[], Gateway], advice: str
    ):
        off, on = await both_runs(monkeypatch, make())

        lines = probe.conclude([off], [on], KEY)

        assert lines[0] == "=== Result ==="
        assert advice in lines[-1]
        assert lines[-1].startswith("advice: ")

    async def test_the_result_counts_the_runs(self, monkeypatch: pytest.MonkeyPatch):
        off, on = await both_runs(monkeypatch, Gateway())

        text = "\n".join(probe.conclude([off, off], [on, on, on], KEY))

        assert "The gateway ACCEPTS reasoning_content. (3 of 3 runs with replay on)" in text
        assert "model, replay on:  recalled both numbers in 3, forgot them in 0" in text
        assert "model, replay off: recalled both numbers in 0, forgot them in 2" in text
        assert "thinking of the last turn (chars, mean): off " in text

    def test_the_thinking_of_a_failed_run_does_not_count_for_the_last_turn(self):
        ok = two_turns()
        ok.turns[-1].thinking = "x" * 100
        failed = two_turns()
        failed.turns[-1] = turn(2, stop="error", error="boom")

        text = "\n".join(probe.conclude([failed], [ok], KEY))

        assert "(chars, mean): off n/a, on 100." in text

    def test_the_time_of_a_run_is_the_mean_over_the_runs(self):
        def timed(replay: bool, *seconds: float) -> Any:
            run = two_turns()
            run.replay = replay
            for each, value in zip(run.turns, seconds, strict=True):
                each.seconds = value
            return run

        offs = [timed(False, 1.0, 2.0), timed(False, 3.0, 4.0)]  # 3 s and 7 s
        ons = [timed(True, 0.5, 0.5)]  # 1 s

        text = "\n".join(probe.conclude(offs, ons, KEY))

        assert "Time of a run (s, mean): off 5, on 1." in text

    async def test_a_refusal_is_in_the_result(self, monkeypatch: pytest.MonkeyPatch):
        off, on = await both_runs(monkeypatch, Gateway(refuse=strict(refusal)))

        text = "\n".join(probe.conclude([off], [on], KEY))

        assert "The gateway REFUSES reasoning_content" in text

    async def test_without_a_run_with_replay_off_chance_is_not_excluded(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _, on = await both_runs(monkeypatch, Gateway())

        lines = probe.conclude([], [on], KEY)

        assert "no clean run with replay off" in lines[-1]

    async def test_a_gateway_that_needs_the_field_is_told_so(self, monkeypatch: pytest.MonkeyPatch):
        off, on = await both_runs(monkeypatch, Gateway(refuse=needs_thinking()))

        lines = probe.conclude([off], [on], KEY)
        shown = probe.format_run(off, settings(), 1, 1)

        text = "\n".join(lines)
        assert "gateway: The gateway REQUIRES reasoning_content." in text
        assert "(1 of 2 runs)" in text
        assert not any(line.startswith("warning:") for line in lines)  # the failure is explained
        assert lines[-1].startswith("advice: Set replay_reasoning = true for this model. ")
        assert "and it takes the field" in lines[-1]
        assert any("[required]" in line for line in shown)
        assert not any("(as expected)" in line for line in shown)

    async def test_a_gateway_that_refuses_the_field_and_needs_it_is_told_so(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        def refuse(payload: dict) -> BaseException | None:
            if not any(m.get("role") == "assistant" for m in payload["messages"]):
                return None
            return refusal() if carries_thinking(payload) else api_error(400, NEEDS_TEXT)

        off, on = await both_runs(monkeypatch, Gateway(refuse=refuse))

        lines = probe.conclude([off], [on], KEY)

        assert "[refused_unrecovered]" in "\n".join(probe.format_run(on, settings(), 1, 1))
        assert "(2 of 2 runs)" in "\n".join(lines)
        assert "did not work either" in lines[-1]

    async def test_without_a_run_with_the_field_the_advice_says_to_check_it(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        off, _ = await both_runs(monkeypatch, Gateway(refuse=needs_thinking()))

        lines = probe.conclude([off], [], KEY)

        assert "Run again without --only" in lines[-1]
        assert lines[-1].startswith("advice: Set replay_reasoning = true for this model: ")

    async def test_one_run_that_recalls_and_one_that_forgets_is_mixed(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        off, on = await both_runs(monkeypatch, Gateway())
        _, forgot = await both_runs(monkeypatch, Gateway(sees_replay=False))

        lines = probe.conclude([off], [on, forgot], KEY)

        assert "mixed" in lines[-1]

    async def test_a_run_with_replay_off_that_fails_is_a_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        off, on = await both_runs(
            monkeypatch, Gateway(refuse=lambda payload: api_error(401, "bad key"))
        )

        lines = probe.conclude([off], [on], KEY)

        assert any(line.startswith("warning: a run with replay off failed too") for line in lines)

    async def test_nothing_was_tested_when_no_field_was_sent(self, monkeypatch: pytest.MonkeyPatch):
        off, _ = await both_runs(monkeypatch, Gateway())

        lines = probe.conclude([off], [off], KEY)

        assert "Nothing was tested" in lines[-1]


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


class TestTheCommandLine:
    def test_a_full_run_writes_the_report_and_exits_with_0(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        wire_up(monkeypatch, Gateway())
        monkeypatch.setenv("REAL_LLM_API_KEY", KEY)

        code = probe.main(["--base-url", GATEWAY_URL, "--model", "deepseek-flash"])

        out = capsys.readouterr().out
        assert code == 0
        assert "replay_reasoning = off" in out and "replay_reasoning = on" in out
        assert "[accepted]" in out and "[recalled]" in out and "[forgot]" in out
        assert "advice: Set replay_reasoning = true" in out
        assert "llm-gw.example.test" in out
        assert probe.fingerprint(KEY) in out
        assert KEY not in out

    def test_a_failed_run_exits_with_1_and_the_key_is_not_in_the_report(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        wire_up(monkeypatch, Gateway(refuse=lambda payload: api_error(401, f"bad key {KEY}")))
        monkeypatch.setenv("REAL_LLM_API_KEY", KEY)

        code = probe.main(["--base-url", GATEWAY_URL, "--model", "m"])

        out = capsys.readouterr()
        assert code == 1
        assert "FAILED (error)" in out.out
        assert "bad key ***" in out.out  # the text of the error is there, with the key taken out
        assert KEY not in out.out and KEY not in out.err

    def test_a_gateway_that_needs_the_field_is_reported_with_the_text_of_its_refusal(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        wire_up(monkeypatch, Gateway(refuse=needs_thinking()))
        monkeypatch.setenv("REAL_LLM_API_KEY", KEY)

        code = probe.main(["--base-url", GATEWAY_URL, "--model", "m"])

        out = capsys.readouterr().out
        assert code == 1  # the run with replay off failed, and that is the finding
        assert "[required]" in out and NEEDS_TEXT in out
        assert "The gateway REQUIRES reasoning_content" in out
        assert "advice: Set replay_reasoning = true for this model. " in out

    def test_the_warnings_of_the_adapter_are_shown_and_the_key_is_taken_out(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        """A gateway that repeats the key in its error must not put the key in the output."""
        error = api_error(400, f"{FIELD_ERROR} The key was {KEY}.")
        wire_up(monkeypatch, Gateway(refuse=strict(lambda: error)))
        monkeypatch.setenv("REAL_LLM_API_KEY", KEY)

        code = probe.main(["--base-url", GATEWAY_URL, "--model", "m", "--only", "on"])

        shown = capsys.readouterr()
        assert code == 0
        assert "  log: " in shown.err
        assert "Asking again without it" in shown.err
        assert "The key was ***." in shown.err
        assert KEY not in shown.out and KEY not in shown.err
        assert "[refused_retried]" in shown.out

    def test_the_log_handler_and_level_are_put_back_when_the_run_ends(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        logger = logging.getLogger("pi_agent_core")
        handlers, level = list(logger.handlers), logger.level
        logger.setLevel(logging.ERROR)  # a level that the probe has to change, and then restore
        try:
            wire_up(monkeypatch, Gateway())
            monkeypatch.setenv("REAL_LLM_API_KEY", KEY)

            probe.main(["--base-url", GATEWAY_URL, "--model", "m", "--only", "on"])

            assert logger.handlers == handlers
            assert logger.level == logging.ERROR
        finally:
            logger.setLevel(level)

    def test_no_key_is_exit_2_and_the_message_names_the_variable(
        self, capsys: pytest.CaptureFixture[str]
    ):
        code = probe.main(["--base-url", GATEWAY_URL, "--model", "m"])

        assert code == 2
        assert "$REAL_LLM_API_KEY is not set" in capsys.readouterr().err

    def test_the_key_can_come_from_another_variable(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        wire_up(monkeypatch, Gateway())
        monkeypatch.setenv("MY_GATEWAY_KEY", KEY)

        code = probe.main(
            ["--base-url", GATEWAY_URL, "--model", "m", "--api-key-env", "MY_GATEWAY_KEY"]
        )

        out = capsys.readouterr().out
        assert code == 0
        assert "$MY_GATEWAY_KEY" in out and KEY not in out

    def test_only_on_makes_no_run_with_replay_off(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        wire_up(monkeypatch, Gateway())
        monkeypatch.setenv("REAL_LLM_API_KEY", KEY)

        probe.main(["--base-url", GATEWAY_URL, "--model", "m", "--only", "on"])

        out = capsys.readouterr().out
        assert "replay_reasoning = on" in out
        assert "replay_reasoning = off" not in out

    def test_repeat_makes_each_run_again(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        wire_up(monkeypatch, Gateway())
        monkeypatch.setenv("REAL_LLM_API_KEY", KEY)

        probe.main(["--base-url", GATEWAY_URL, "--model", "m", "--repeat", "2"])

        out = capsys.readouterr().out
        assert out.count("--- run ") == 4
        assert "run 2 of 2: replay_reasoning = on" in out
        assert "(of 2 runs)" in out

    def test_deepseeks_own_api_has_no_run_with_replay_off(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        """That API always gets the thinking back, so a run with replay off is no control."""
        wire_up(monkeypatch, Gateway())
        monkeypatch.setenv("REAL_LLM_API_KEY", KEY)

        probe.main(
            ["--provider", "deepseek", "--base-url", "https://api.deepseek.com/v1", "--model", "m"]
        )

        out = capsys.readouterr().out
        assert "always gets the thinking back" in out
        assert "replay_reasoning = off" not in out
        assert "replay_reasoning = on" in out

    def test_the_gateway_and_the_model_default_to_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("REAL_LLM_BASE_URL", "https://a.example/v1")
        monkeypatch.setenv("REAL_LLM_MODEL", "model-a")

        args = probe.parse_args([])

        assert (args.base_url, args.model) == ("https://a.example/v1", "model-a")

        monkeypatch.setenv("PROBE_BASE_URL", "https://b.example/v1")
        monkeypatch.setenv("PROBE_MODEL", "model-b")

        args = probe.parse_args([])

        assert (args.base_url, args.model) == ("https://b.example/v1", "model-b")
        assert (args.provider, args.thinking_level, args.hops, args.repeat) == (
            "openai",
            "high",
            1,
            1,
        )

    @pytest.mark.parametrize(
        "argv",
        [
            ["--model", "m"],
            ["--base-url", GATEWAY_URL],
            ["--base-url", "ftp://gateway.example/v1", "--model", "m"],
            ["--base-url", "gateway.example/v1", "--model", "m"],
            ["--base-url", GATEWAY_URL, "--model", "m", "--hops", "0"],
            ["--base-url", GATEWAY_URL, "--model", "m", "--hops", "7"],
            ["--base-url", GATEWAY_URL, "--model", "m", "--repeat", "0"],
            ["--base-url", GATEWAY_URL, "--model", "m", "--thinking-level", "off"],
            ["--base-url", GATEWAY_URL, "--model", "m", "--provider", "anthropic"],
        ],
    )
    def test_a_wrong_call_is_exit_2(self, argv: list[str], capsys: pytest.CaptureFixture[str]):
        with pytest.raises(SystemExit) as info:
            probe.parse_args(argv)

        assert info.value.code == 2
        assert capsys.readouterr().err

    def test_it_runs_as_a_program(self):
        """The ``__main__`` guard and the exit status, in a process of their own."""
        env = {k: v for k, v in os.environ.items() if k not in _ENVIRONMENT}
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT), env.get("PYTHONPATH")]))

        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--base-url", GATEWAY_URL, "--model", "m"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=ROOT,
            timeout=120,
        )

        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "$REAL_LLM_API_KEY is not set" in proc.stderr
