"""What happens to a goal over time (audit P7-11).

* A goal that was finished came back unfinished: the state was saved only on turns where some
  step was still open, and completing the goal was never saved at all.
* A step index that names no step made up an "Unnamed step" and reported success.
* An agent that ignored the reminder to call goal_complete was reminded on every turn, until
  the harness's turn limit ended the run with an error.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest
from pi_goal_x import (
    _check_goal_progress,
    _restore_goal_state,
    _start_goal,
    activate,
)
from pi_goal_x.goal_state import GoalState, StepStatus
from pi_goal_x.goal_tool import (
    GoalCompleteParams,
    GoalUpdateParams,
    create_goal_complete_tool,
    create_goal_update_tool,
)

from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.tests.mock_stream import _base_partial, mock_text_stream
from pi_agent_core.types import DoneEvent, Model, StartEvent
from pi_agent_harness import AgentHarness, MemorySessionStorage, Session


class FakePi:
    """Just the parts of ExtensionAPI that the hooks and the /goal command use."""

    def __init__(self, saved: list[Any] | None = None) -> None:
        self.messages: list[str] = []
        self.entries: list[tuple[str, Any]] = []
        self._saved = saved or []

    def send_message(self, text: str) -> None:
        self.messages.append(text)

    def append_entry(self, custom_type: str, data: Any = None) -> None:
        self.entries.append((custom_type, data))

    def get_custom_entries(self, custom_type: str) -> list[Any]:
        return [SimpleNamespace(data=data) for data in self._saved]


def goal(*steps: tuple[str, StepStatus], description: str = "Ship it") -> GoalState:
    """A goal with the given (description, status) steps."""
    state = GoalState()
    state.start(description)
    for text, status in steps:
        state.update_step(description=text, status=status)
    return state


class TestUpdateStep:
    @pytest.mark.parametrize("index", [2, 3, 99, -1, -2])
    def test_an_index_that_names_no_step_is_an_error_and_changes_nothing(self, index: int):
        state = goal(("one", "pending"), ("two", "pending"))

        with pytest.raises(ValueError, match="There is no step"):
            state.update_step(index, status="done")

        assert [(s.description, s.status) for s in state.steps] == [
            ("one", "pending"),
            ("two", "pending"),
        ]

    @pytest.mark.parametrize(
        ("count", "said"),
        [(0, "has no steps yet"), (1, "has 1 step, index 0"), (3, "has 3 steps, indexes 0 to 2")],
    )
    def test_the_error_says_which_steps_exist(self, count: int, said: str):
        state = goal(*[(f"step {n}", "pending") for n in range(count)])

        with pytest.raises(ValueError, match=re.escape(said) + ".*Leave step_index out"):
            state.update_step(count, status="done")

    @pytest.mark.parametrize("description", [None, ""])
    def test_a_new_step_needs_a_description(self, description: str | None):
        state = goal(("one", "pending"))

        with pytest.raises(ValueError, match="needs a description"):
            state.update_step(description=description, status="done")

        assert len(state.steps) == 1

    def test_the_first_and_the_last_step_can_be_updated(self):
        state = goal(("one", "pending"), ("two", "pending"))

        state.update_step(0, status="done")
        state.update_step(1, status="blocked", details="waiting")

        assert [s.status for s in state.steps] == ["done", "blocked"]
        assert state.steps[1].details == "waiting"

    def test_leaving_the_index_out_appends_a_step(self):
        state = goal(("one", "pending"))

        plain = state.update_step(description="two")
        noted = state.update_step(description="three", status="in_progress", details="notes")

        assert state.steps[1:] == [plain, noted]
        assert (plain.description, plain.status, plain.details) == ("two", "pending", None)
        assert (noted.description, noted.status, noted.details) == ("three", "in_progress", "notes")

    def test_a_step_can_be_renamed_without_touching_the_rest(self):
        state = goal(("one", "in_progress"))
        state.update_step(0, details="notes")

        state.update_step(0, description="uno")

        step = state.steps[0]
        assert (step.description, step.status, step.details) == ("uno", "in_progress", "notes")


class TestStatusListing:
    def test_steps_are_listed_by_the_index_goal_update_takes(self):
        # "Step 2" for the step at index 1 made a model that passed 2 change the wrong step
        state = goal(("design", "done"), ("build", "in_progress"), ("ship", "pending"))

        listed = re.findall(r"\[(\d+)\] (.+)", state.format_status())

        assert [(int(i), text) for i, text in listed] == [(0, "design"), (1, "build"), (2, "ship")]


class TestGoalUpdateTool:
    async def test_a_wrong_index_fails_the_call_and_reports_no_change(self):
        state = goal(("one", "pending"))
        changes: list[dict[str, Any]] = []
        tool = create_goal_update_tool(state, on_change=lambda: changes.append(state.to_dict()))

        with pytest.raises(ValueError, match="no step 4"):
            await tool.execute("tc-1", GoalUpdateParams(step_index=4, status="done"))

        assert state.steps[0].status == "pending"
        assert changes == []

    async def test_every_change_is_reported_after_it_was_made(self):
        state = goal(("one", "pending"))
        changes: list[dict[str, Any]] = []
        tool = create_goal_update_tool(state, on_change=lambda: changes.append(state.to_dict()))

        await tool.execute("tc-1", GoalUpdateParams(step_index=0, status="done"))
        await tool.execute("tc-2", GoalUpdateParams(description="two"))

        assert [[s["status"] for s in c["steps"]] for c in changes] == [
            ["done"],
            ["done", "pending"],
        ]

    async def test_nothing_is_reported_when_there_is_no_goal(self):
        changes: list[int] = []
        tool = create_goal_update_tool(GoalState(), on_change=lambda: changes.append(1))

        result = await tool.execute("tc-1", GoalUpdateParams(description="one"))

        assert "No active goal" in result.content[0]["text"]
        assert changes == []


class TestGoalCompleteTool:
    async def test_the_change_is_reported_after_the_goal_was_completed(self):
        state = goal(("one", "pending"))
        changes: list[dict[str, Any]] = []
        tool = create_goal_complete_tool(state, on_change=lambda: changes.append(state.to_dict()))

        await tool.execute("tc-1", GoalCompleteParams(summary="It works"))

        assert [(c["completed"], c["summary"]) for c in changes] == [(True, "It works")]

    async def test_nothing_is_reported_when_there_is_no_goal(self):
        changes: list[int] = []
        tool = create_goal_complete_tool(GoalState(), on_change=lambda: changes.append(1))

        result = await tool.execute("tc-1", GoalCompleteParams(summary="Done"))

        assert "No active goal" in result.content[0]["text"]
        assert changes == []


class TestReminders:
    def test_a_goal_with_an_open_step_is_not_reminded(self):
        pi = FakePi()

        _check_goal_progress(pi, goal(("one", "done"), ("two", "pending")), None)

        assert pi.messages == []

    def test_a_goal_without_steps_is_not_reminded(self):
        pi = FakePi()

        _check_goal_progress(pi, goal(), None)
        _check_goal_progress(pi, GoalState(), None)

        assert pi.messages == []

    def test_a_finished_goal_is_reminded_twice_and_then_left_alone(self):
        pi = FakePi()
        state = goal(("one", "done"))

        for _ in range(10):
            _check_goal_progress(pi, state, None)

        assert len(pi.messages) == 2
        assert all("Ship it" in m and "goal_complete" in m for m in pi.messages)

    def test_a_step_that_is_reopened_starts_the_count_again(self):
        pi = FakePi()
        state = goal(("one", "done"))
        for _ in range(5):
            _check_goal_progress(pi, state, None)

        state.update_step(0, status="in_progress")
        _check_goal_progress(pi, state, None)
        state.update_step(0, status="done")
        for _ in range(5):
            _check_goal_progress(pi, state, None)

        assert len(pi.messages) == 4

    def test_touching_a_done_step_does_not_start_the_count_again(self):
        # An agent that answers every reminder with a goal_update and never calls goal_complete
        pi = FakePi()
        state = goal(("one", "done"))

        for _ in range(6):
            _check_goal_progress(pi, state, None)
            state.update_step(0, details="still fine")

        assert len(pi.messages) == 2

    def test_a_new_goal_starts_the_count_again(self):
        pi = FakePi()
        state = goal(("one", "done"))
        for _ in range(5):
            _check_goal_progress(pi, state, None)

        state.start("Another goal")
        state.update_step(description="two", status="done")
        for _ in range(5):
            _check_goal_progress(pi, state, None)

        assert len(pi.messages) == 4
        assert "Another goal" in pi.messages[-1]

    def test_a_completed_goal_is_not_reminded(self):
        pi = FakePi()
        state = goal(("one", "done"))
        state.complete("It works")

        _check_goal_progress(pi, state, None)

        assert pi.messages == []

    def test_a_reminder_saves_nothing(self):
        # The state is saved when it changes, not once per turn
        pi = FakePi()
        state = goal(("one", "done"))

        _check_goal_progress(pi, state, None)
        _check_goal_progress(pi, goal(("one", "pending")), None)

        assert pi.entries == []


class TestStartGoal:
    def test_a_new_goal_is_saved(self):
        pi = FakePi()
        state = GoalState()

        _start_goal(pi, state, "  Build it  ")

        assert [(kind, data["description"]) for kind, data in pi.entries] == [
            ("goal_state", "Build it")
        ]

    def test_asking_for_the_status_changes_and_saves_nothing(self):
        pi = FakePi()
        state = goal(("one", "pending"))

        _start_goal(pi, state, "")

        assert pi.entries == []
        assert state.steps[0].description == "one"


class TestRestore:
    def test_an_unfinished_goal_comes_back_with_its_steps(self):
        saved = goal(("one", "done"), ("two", "in_progress")).to_dict()
        state = GoalState()

        _restore_goal_state(FakePi([saved]), state, None)

        assert state.active
        assert [(s.description, s.status) for s in state.steps] == [
            ("one", "done"),
            ("two", "in_progress"),
        ]

    def test_a_goal_saved_as_completed_does_not_come_back(self):
        finished = goal(("one", "pending"))
        finished.complete("It works")
        state = GoalState()

        _restore_goal_state(FakePi([goal().to_dict(), finished.to_dict()]), state, None)

        assert not state.active
        assert state.description is None  # nothing of it is brought back


# -- in a real session ------------------------------------------------------------------------


class ScriptedLlm:
    """A StreamFn that makes the tool calls in *script* (one list per call), then only answers."""

    def __init__(self, *script: list[tuple[str, dict[str, Any]]]) -> None:
        self.script = list(script)
        self.calls: list[list[Any]] = []

    async def __call__(self, model: Model, context: Any, options: Any = None) -> Any:
        self.calls.append(list(context.messages))
        index = len(self.calls) - 1
        if index >= len(self.script):
            return await mock_text_stream(model, context, options)
        content = [
            {"type": "toolCall", "id": f"call_{index}_{n}", "name": name, "arguments": arguments}
            for n, (name, arguments) in enumerate(self.script[index])
        ]
        partial = _base_partial(model, content)
        partial.stopReason = "toolUse"
        stream = AssistantMessageEventStream()
        stream.push(StartEvent(partial=partial.model_copy(deep=True)))
        stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason="toolUse"))
        stream.set_final_message(partial)
        stream.end()
        return stream


def user_texts(messages: list[Any]) -> list[str]:
    texts: list[str] = []
    for message in messages:
        if getattr(message, "role", None) != "user":
            continue
        content = message.content
        if isinstance(content, str):
            texts.append(content)
        else:
            texts.append("".join(block.get("text", "") for block in content))
    return texts


async def open_session(
    llm: ScriptedLlm, storage: MemorySessionStorage | None = None, **kwargs: Any
) -> tuple[AgentHarness, MemorySessionStorage]:
    """A harness with the goal extension, on *storage* (a new session when omitted)."""
    storage = storage or await MemorySessionStorage.create(session_id="goal")
    # The harness has no turn limit of its own, and reminders used to go on forever without one:
    # a regression has to fail here, not hang the test run.
    kwargs.setdefault("max_turns", 8)
    harness = AgentHarness(
        session=Session(storage),
        model=Model(provider="mock", model_id="m1"),
        stream_fn=llm,
        extensions=[activate],
        **kwargs,
    )
    await harness.load_extensions()
    return harness, storage


async def saved_goals(harness: AgentHarness) -> list[dict[str, Any]]:
    """The goal states saved in the session, oldest first."""
    entries = await harness.session.get_entries()
    return [
        e.data
        for e in entries
        if getattr(e, "type", None) == "custom" and e.customType == "goal_state"
    ]


async def goal_update_text(harness: AgentHarness, **arguments: Any) -> str:
    """What the goal_update tool of *harness* answers to a call with *arguments*."""
    tool = harness.extension_registry.get_tools()["goal_update"]
    result = await tool.execute("tc-later", GoalUpdateParams(**arguments))
    return result.content[0]["text"]


class TestInARealSession:
    async def test_a_finished_goal_is_saved_as_finished_and_is_not_brought_back(self):
        llm = ScriptedLlm(
            [("goal_update", {"description": "Write the code", "status": "done"})],
            [("goal_complete", {"summary": "It works"})],
        )
        harness, storage = await open_session(llm)
        await harness.prompt("/goal Ship it")
        await harness.prompt("Go")

        saved = await saved_goals(harness)
        assert (saved[-1]["completed"], saved[-1]["summary"]) == (True, "It works")

        later, _ = await open_session(ScriptedLlm(), storage)
        assert "No active goal" in await goal_update_text(later, description="More")

    async def test_a_goal_whose_steps_are_all_done_is_saved_with_them(self):
        llm = ScriptedLlm([("goal_update", {"description": "Write the code", "status": "done"})])
        harness, _ = await open_session(llm)
        await harness.prompt("/goal Ship it")
        await harness.prompt("Go")

        saved = await saved_goals(harness)

        assert [s["status"] for s in saved[-1]["steps"]] == ["done"]
        assert saved[-1]["completed"] is False

    async def test_a_goal_left_open_comes_back_with_its_steps(self):
        llm = ScriptedLlm(
            [
                ("goal_update", {"description": "Design", "status": "done"}),
                ("goal_update", {"description": "Build", "status": "in_progress"}),
            ]
        )
        harness, storage = await open_session(llm)
        await harness.prompt("/goal Ship it")
        await harness.prompt("Go")

        later, _ = await open_session(ScriptedLlm(), storage)
        text = await goal_update_text(later, step_index=1, status="done")

        assert "Build [done]" in text
        assert "Progress: 2/2 steps completed" in text

    async def test_a_turn_that_changes_nothing_saves_nothing(self):
        harness, _ = await open_session(ScriptedLlm())
        await harness.prompt("/goal Ship it")
        await harness.prompt("Hello")
        await harness.prompt("Hello again")

        assert len(await saved_goals(harness)) == 1  # the one written by /goal

    async def test_an_agent_that_ignores_the_reminders_is_reminded_twice(self):
        llm = ScriptedLlm([("goal_update", {"description": "Write the code", "status": "done"})])
        harness, _ = await open_session(llm)
        await harness.prompt("/goal Ship it")

        reply = await harness.prompt("Go")

        assert len(llm.calls) == 3  # the tool call, then one answer to each reminder
        assert reply.stopReason == "stop"
        reminders = [t for t in user_texts(llm.calls[-1]) if "All steps are done" in t]
        assert len(reminders) == 2

    async def test_a_wrong_step_index_is_reported_to_the_model_and_changes_nothing(self):
        llm = ScriptedLlm(
            [("goal_update", {"description": "Design", "status": "pending"})],
            [("goal_update", {"step_index": 1, "status": "done"})],  # only step 0 exists
        )
        harness, _ = await open_session(llm)
        await harness.prompt("/goal Ship it")
        await harness.prompt("Go")

        failed = [
            m for m in llm.calls[-1] if getattr(m, "role", None) == "toolResult" and m.isError
        ]
        assert len(failed) == 1
        assert "no step 1" in failed[0].content[0]["text"]
        assert "has 1 step, index 0" in failed[0].content[0]["text"]
        saved = await saved_goals(harness)
        assert [(s["description"], s["status"]) for s in saved[-1]["steps"]] == [
            ("Design", "pending")
        ]
