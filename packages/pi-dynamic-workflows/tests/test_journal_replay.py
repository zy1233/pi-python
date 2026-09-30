"""Journal replay (audit P7-05).

The journal used to be matched by *position*: the n-th ``agent()`` call had to find the
n-th recorded entry. ``parallel()`` records in completion order, so a resumed fan-out
matched nothing (and the first miss threw the rest of the journal away), and a failed
agent was recorded as a successful ``None`` that every resume replayed.

Replay is now keyed by the request: a call is answered by the earliest recorded answer to
the *same* request that this run has not used yet, whatever order things completed in.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from pi_dynamic_workflows.journal import _MISS, Journal, hash_request
from pi_dynamic_workflows.runtime import AgentResult, SubagentExecutor, WorkflowRuntime


class _Executor(SubagentExecutor):
    """Answers ``answer to <prompt>``; ``delays`` sets how long a prompt takes, ``failing``
    names the prompts whose agent fails with ``error``."""

    def __init__(
        self,
        *,
        delays: dict[str, float] | None = None,
        failing: tuple[str, ...] = (),
        error: str = "provider down",
    ) -> None:
        self.calls: list[str] = []
        self._delays = delays or {}
        self._failing = failing
        self._error = error

    async def run_agent(self, prompt: str, **kwargs: Any) -> AgentResult:
        self.calls.append(prompt)
        await asyncio.sleep(self._delays.get(prompt, 0))
        if prompt in self._failing:
            return AgentResult(error=self._error)
        return AgentResult(text=f"answer to {prompt}", tokens_used=1)


def _sequence(*prompts: str) -> str:
    """A script that asks each prompt in turn and returns all the answers."""
    asks = "\n".join(f"    out.append(await agent({prompt!r}))" for prompt in prompts)
    return f'meta = {{"name": "seq"}}\nasync def main():\n    out = []\n{asks}\n    result(out)\n'


FAN_OUT = """
meta = {"name": "fan-out"}
async def main():
    answers = await parallel([lambda: agent("a"), lambda: agent("b"), lambda: agent("c")])
    result(answers)
"""


async def _run(executor: SubagentExecutor, journal: Journal, script: str) -> Any:
    return await WorkflowRuntime(executor, journal=journal).execute(script)


def _recorded_results(path: Path) -> list[Any]:
    return [json.loads(line)["result"] for line in path.read_text(encoding="utf-8").splitlines()]


class TestReplayDoesNotDependOnCompletionOrder:
    async def test_a_resumed_fan_out_is_answered_from_the_journal(self, tmp_path: Path):
        path = tmp_path / "run.jsonl"
        # "a" is the slowest, so the journal is written c, b, a: not in call order.
        first = await _run(_Executor(delays={"a": 0.06, "b": 0.03}), Journal(path), FAN_OUT)
        assert _recorded_results(path) == ["answer to c", "answer to b", "answer to a"]

        resumed = _Executor()
        second = await _run(resumed, Journal.load(path), FAN_OUT)

        assert resumed.calls == []
        assert second.result == first.result == ["answer to a", "answer to b", "answer to c"]

    async def test_a_changed_request_does_not_cost_the_journal_its_other_entries(
        self, tmp_path: Path
    ):
        path = tmp_path / "run.jsonl"
        await _run(_Executor(), Journal(path), _sequence("a", "b", "c"))

        changed = _Executor()
        run = await _run(changed, Journal.load(path), _sequence("a", "B", "c"))

        assert changed.calls == ["B"]
        assert run.result == ["answer to a", "answer to B", "answer to c"]

    async def test_a_miss_neither_rewrites_nor_shortens_the_journal_file(self, tmp_path: Path):
        path = tmp_path / "run.jsonl"
        await _run(_Executor(), Journal(path), _sequence("a", "b"))
        before = path.read_text(encoding="utf-8")

        await _run(_Executor(), Journal.load(path), _sequence("a", "X"))

        after = path.read_text(encoding="utf-8")
        assert after.startswith(before)  # what was recorded is still there, byte for byte
        assert len(after.splitlines()) == 3  # and only the one new answer was added

    async def test_a_resume_that_is_missing_entries_only_pays_for_the_missing_ones(
        self, tmp_path: Path
    ):
        path = tmp_path / "run.jsonl"
        await _run(_Executor(failing=("b",)), Journal(path), FAN_OUT)  # b failed, a and c ran

        healthy = _Executor()
        run = await _run(healthy, Journal.load(path), FAN_OUT)

        assert healthy.calls == ["b"]
        assert run.result == ["answer to a", "answer to b", "answer to c"]


class TestIdenticalRequests:
    class _Counting(SubagentExecutor):
        def __init__(self) -> None:
            self.calls = 0

        async def run_agent(self, prompt: str, **kwargs: Any) -> AgentResult:
            self.calls += 1
            return AgentResult(text=f"roll {self.calls}")

    async def test_they_are_replayed_in_the_order_they_were_recorded(self, tmp_path: Path):
        path = tmp_path / "run.jsonl"
        script = _sequence("roll", "roll", "roll")
        first = await _run(self._Counting(), Journal(path), script)
        assert first.result == ["roll 1", "roll 2", "roll 3"]

        again = self._Counting()
        run = await _run(again, Journal.load(path), script)

        assert again.calls == 0
        assert run.result == ["roll 1", "roll 2", "roll 3"]

    async def test_a_call_beyond_what_was_recorded_runs_fresh(self, tmp_path: Path):
        path = tmp_path / "run.jsonl"
        await _run(self._Counting(), Journal(path), _sequence("roll", "roll"))

        more = self._Counting()
        run = await _run(more, Journal.load(path), _sequence("roll", "roll", "roll"))

        assert more.calls == 1
        assert run.result == ["roll 1", "roll 2", "roll 1"]  # the last one is the new run's

    async def test_a_run_does_not_replay_its_own_answers(self, tmp_path: Path):
        executor = _Executor()

        await _run(executor, Journal(tmp_path / "run.jsonl"), _sequence("same", "same"))

        assert executor.calls == ["same", "same"]


class TestFailuresAreNotRemembered:
    @pytest.mark.parametrize("error", ["provider down", "timeout"])
    async def test_a_failed_agent_is_not_journaled_so_a_resume_retries_it(
        self, tmp_path: Path, error: str
    ):
        path = tmp_path / "run.jsonl"

        run = await _run(_Executor(failing=("b",), error=error), Journal(path), _sequence("a", "b"))

        assert run.result == ["answer to a", None]  # the script still sees None
        assert _recorded_results(path) == ["answer to a"]

        healthy = _Executor()
        resumed = await _run(healthy, Journal.load(path), _sequence("a", "b"))

        assert healthy.calls == ["b"]
        assert resumed.result == ["answer to a", "answer to b"]

    async def test_a_worthless_but_successful_answer_is_still_journaled(self, tmp_path: Path):
        class Silent(SubagentExecutor):
            async def run_agent(self, prompt: str, **kwargs: Any) -> AgentResult:
                return AgentResult(text="")

        path = tmp_path / "run.jsonl"
        await _run(Silent(), Journal(path), _sequence("quiet"))

        assert _recorded_results(path) == [""]


class TestJournal:
    def test_a_miss_keeps_every_recorded_entry(self):
        journal = Journal()
        first = hash_request("agent", {"prompt": "a"})
        second = hash_request("agent", {"prompt": "b"})
        journal.append("agent", first, "r1")
        journal.append("agent", second, "r2")
        replaying = Journal()
        replaying._entries = list(journal._entries)

        assert replaying.try_replay("agent", hash_request("agent", {"prompt": "c"})) is _MISS

        assert replaying.entry_count == 2
        assert replaying.try_replay("agent", second) == "r2"  # out of order is fine
        assert replaying.try_replay("agent", first) == "r1"
        assert replaying.replayed_count == 2

    def test_an_entry_answers_one_call_only(self):
        source = Journal()
        request = hash_request("agent", {"prompt": "a"})
        source.append("agent", request, "r1")
        replaying = Journal()
        replaying._entries = list(source._entries)

        assert replaying.try_replay("agent", request) == "r1"
        assert replaying.try_replay("agent", request) is _MISS

    def test_an_answer_recorded_by_this_run_is_not_replayed_to_it(self):
        journal = Journal()
        request = hash_request("agent", {"prompt": "a"})

        journal.append("agent", request, "r1")

        assert journal.try_replay("agent", request) is _MISS

    def test_answers_recorded_before_replay_began_are_replayed_after_new_ones_are_added(self):
        source = Journal()
        old = hash_request("agent", {"prompt": "old"})
        source.append("agent", old, "r-old")
        journal = Journal()
        journal._entries = list(source._entries)

        journal.append("agent", hash_request("agent", {"prompt": "new"}), "r-new")

        assert journal.try_replay("agent", old) == "r-old"

    def test_the_kind_is_part_of_the_request(self):
        source = Journal()
        request = hash_request("agent", {"prompt": "a"})
        source.append("agent", request, "r1")
        replaying = Journal()
        replaying._entries = list(source._entries)

        assert replaying.try_replay("phase", request) is _MISS
        assert replaying.try_replay("agent", request) == "r1"

    def test_a_journal_loaded_from_disk_replays(self, tmp_path: Path):
        path = tmp_path / "run.jsonl"
        journal = Journal(path)
        request = hash_request("agent", {"prompt": "a"})
        journal.append("agent", request, {"text": "ok"})

        loaded = Journal.load(path)

        assert loaded.try_replay("agent", request) == {"text": "ok"}
