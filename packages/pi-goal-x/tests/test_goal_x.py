"""Tests for pi-goal-x extension."""

from __future__ import annotations

from pi_goal_x.goal_state import GoalState
from pi_goal_x.goal_tool import (
    GoalCompleteParams,
    GoalUpdateParams,
    create_goal_complete_tool,
    create_goal_update_tool,
)

# ---------------------------------------------------------------------------
# GoalState
# ---------------------------------------------------------------------------


class TestGoalState:
    def test_start(self) -> None:
        state = GoalState()
        assert not state.active
        state.start("Build a CLI tool")
        assert state.active
        assert state.description == "Build a CLI tool"
        assert state.steps == []

    def test_update_step_append(self) -> None:
        state = GoalState()
        state.start("Test")
        step = state.update_step(description="Step 1", status="pending")
        assert len(state.steps) == 1
        assert step.description == "Step 1"
        assert step.status == "pending"

    def test_update_step_modify(self) -> None:
        state = GoalState()
        state.start("Test")
        state.update_step(description="Step 1")
        state.update_step(0, status="done", details="Finished")
        assert state.steps[0].status == "done"
        assert state.steps[0].details == "Finished"

    def test_complete(self) -> None:
        state = GoalState()
        state.start("Test")
        state.update_step(description="Step 1", status="in_progress")
        state.complete("All done!")
        assert state.completed
        assert state.summary == "All done!"
        assert state.steps[0].status == "done"
        assert not state.active

    def test_progress(self) -> None:
        state = GoalState()
        state.start("Test")
        state.update_step(description="Step 1", status="done")
        state.update_step(description="Step 2", status="pending")
        assert state.progress == "1/2 steps completed"

    def test_all_done(self) -> None:
        state = GoalState()
        state.start("Test")
        state.update_step(description="Step 1", status="done")
        state.update_step(description="Step 2", status="done")
        assert state.all_done

    def test_all_done_false(self) -> None:
        state = GoalState()
        state.start("Test")
        state.update_step(description="Step 1", status="done")
        state.update_step(description="Step 2", status="pending")
        assert not state.all_done

    def test_to_dict_from_dict_roundtrip(self) -> None:
        state = GoalState()
        state.start("Build something")
        state.update_step(description="Design", status="done", details="Done design")
        state.update_step(description="Implement", status="in_progress")
        d = state.to_dict()

        restored = GoalState.from_dict(d)
        assert restored.description == "Build something"
        assert len(restored.steps) == 2
        assert restored.steps[0].status == "done"
        assert restored.steps[1].status == "in_progress"

    def test_format_status(self) -> None:
        state = GoalState()
        state.start("My Goal")
        state.update_step(description="Step 1", status="done")
        state.update_step(description="Step 2", status="in_progress")
        fmt = state.format_status()
        assert "My Goal" in fmt
        assert "✅" in fmt
        assert "🔄" in fmt

    def test_format_status_inactive(self) -> None:
        state = GoalState()
        assert state.format_status() == ""


# ---------------------------------------------------------------------------
# goal_update tool
# ---------------------------------------------------------------------------


class TestGoalUpdateTool:
    async def test_no_active_goal(self) -> None:
        state = GoalState()
        tool = create_goal_update_tool(state)
        result = await tool.execute(
            "tc-1",
            GoalUpdateParams(description="Step 1", status="pending"),
        )
        assert "No active goal" in result.content[0]["text"]

    async def test_append_step(self) -> None:
        state = GoalState()
        state.start("Test goal")
        tool = create_goal_update_tool(state)
        result = await tool.execute(
            "tc-1",
            GoalUpdateParams(description="Write tests", status="in_progress"),
        )
        text = result.content[0]["text"]
        assert "Write tests" in text
        assert "in_progress" in text
        assert len(state.steps) == 1

    async def test_update_existing_step(self) -> None:
        state = GoalState()
        state.start("Test goal")
        state.update_step(description="Step 1", status="pending")
        tool = create_goal_update_tool(state)
        await tool.execute(
            "tc-1",
            GoalUpdateParams(step_index=0, status="done"),
        )
        assert state.steps[0].status == "done"


# ---------------------------------------------------------------------------
# goal_complete tool
# ---------------------------------------------------------------------------


class TestGoalCompleteTool:
    async def test_no_active_goal(self) -> None:
        state = GoalState()
        tool = create_goal_complete_tool(state)
        result = await tool.execute(
            "tc-1",
            GoalCompleteParams(summary="Done"),
        )
        assert "No active goal" in result.content[0]["text"]

    async def test_complete_goal(self) -> None:
        state = GoalState()
        state.start("Test goal")
        state.update_step(description="Step 1", status="done")
        tool = create_goal_complete_tool(state)
        result = await tool.execute(
            "tc-1",
            GoalCompleteParams(summary="Successfully completed"),
        )
        assert state.completed
        assert result.terminate is True
        assert "Successfully completed" in result.content[0]["text"]


# ---------------------------------------------------------------------------
# activate() entry point
# ---------------------------------------------------------------------------


class TestActivate:
    def test_activate_registers_tools_and_commands(self) -> None:
        from pi_goal_x import activate

        from pi_agent_core.extensions import ExtensionAPI, ExtensionRegistry
        from pi_agent_core.extensions.types import ExtensionMeta

        reg = ExtensionRegistry()
        api = ExtensionAPI(registry=reg, meta=ExtensionMeta(name="pi-goal-x"))
        activate(api)

        tools = reg.get_tools()
        assert "goal_update" in tools
        assert "goal_complete" in tools

        commands = reg.get_commands()
        assert "goal" in commands

        handlers = reg.get_all_event_handlers()
        assert "turn_end" in handlers
        assert "session_start" in handlers
