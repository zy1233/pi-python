"""The pipe between a workflow script's process and the host.

Everything a script does that matters goes over it, and the host treats the other end as
hostile: what arrives is checked, what is too much is cut off, what is out of line ends the
run. These tests speak to the host the way a script that got out of its namespace could (raw
writes to the pipe through ``_wire()`` in ``sandbox_support``), and check the run's life
cycle: starting, aborting, dying, leaving nothing behind.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest
from pi_dynamic_workflows.builtin_workflows import BUILTIN_WORKFLOW_NAMES, resolve_builtin_workflow
from pi_dynamic_workflows.runtime import AgentResult, SubagentExecutor, WorkflowRuntime
from pi_dynamic_workflows.sandbox import (
    SandboxCrashed,
    SandboxLimitExceeded,
    SandboxLimits,
    SandboxProtocolError,
    SandboxUnavailable,
    WorkflowScriptError,
)
from pi_dynamic_workflows.sandbox import (
    host as sandbox_host,
)
from sandbox_support import SPIN, FakeHost, alive, run, script


def _raw(line: str) -> str:
    """Script text that puts *line* on the wire as it is (``\\n`` appended)."""
    return f'_imp("os").write(_wire(), {line!r}.encode() + b"\\n")'


# Arguments for each workflow the package ships; a new one has to get an entry here.
BUNDLED_WORKFLOW_ARGS: dict[str, dict[str, Any]] = {
    "deep-research": {"question": "What is AI?"},
    "adversarial-review": {"task": "audit this", "reviewers": 2},
    "code-review": {"diff": "diff --git a/x b/x"},
    "multi-perspective": {"topic": "Python vs Rust"},
    "codebase-audit": {"scope": "src/", "checks": ["security", "performance"]},
}


class TestWhatCrossesTheBoundary:
    async def test_args_go_in_and_the_result_comes_out_as_json(self) -> None:
        out = await run(
            "result({'echo': args, 'tuple': (1, 2), 'set': {3}})",
            args={"a": [1, 2], "b": None, "c": "ü"},
        )

        assert out.result["echo"] == {"a": [1, 2], "b": None, "c": "ü"}
        assert out.result["tuple"] == [1, 2]
        assert out.result["set"] == "{3}"  # not JSON: stringified, not an error

    async def test_the_last_result_wins_and_none_is_a_result(self) -> None:
        out = await run("result(1)\nresult(2)")
        assert out.result == 2
        assert (await run("pass")).result is None

    async def test_the_budget_is_a_view_that_follows_the_hosts_books(self) -> None:
        host = FakeHost()
        out = await run(
            """
            before = budget.spent()
            await agent("x")
            result([budget.total, before, budget.spent(), budget.remaining(), budget.exceeded()])
            """,
            host=host,
            budget_total=15,
        )

        assert out.result == [15, 0, 10, 5, False]

    async def test_the_budget_is_spent_at_the_total_and_what_remains_never_goes_below_zero(
        self,
    ) -> None:
        out = await run(
            """
            def view():
                return [budget.spent(), budget.remaining(), budget.exceeded()]

            await agent("a")
            first = view()
            await agent("b")  # now exactly at the total
            second = view()
            await agent("c")  # past it
            result([first, second, view()])
            """,
            budget_total=20,
        )

        assert out.result == [[10, 10, False], [20, 0, True], [30, 0, True]]

    async def test_a_script_cannot_add_to_the_budget(self) -> None:
        out = await run("result(attempt(lambda: budget.add(1000)))")
        assert out.result.startswith("AttributeError")

    async def test_an_unlimited_budget_has_no_remaining(self) -> None:
        out = await run("result([budget.total, budget.remaining(), budget.exceeded()])")
        assert out.result == [None, None, False]

    async def test_print_is_log_and_phase_is_phase(self) -> None:
        host = FakeHost()
        await run('print("one")\nlog("two")\nphase("Setup")\nphase("Build")', host=host)

        assert host.logs == ["one", "two"]
        assert host.phases == ["Setup", "Build"]

    async def test_calls_arrive_with_the_options_the_script_gave(self) -> None:
        host = FakeHost()
        await run('await agent("p", tier="big", label="L", schema={"type": "object"})', host=host)

        assert host.prompts == ["p"]
        assert host.opts == [{"tier": "big", "label": "L", "schema": {"type": "object"}}]

    async def test_the_prompt_must_be_a_string(self) -> None:
        host = FakeHost()
        out = await run(
            """
            try:
                await agent(["not", "text"])
            except TypeError as e:
                result(str(e))
            """,
            host=host,
        )
        assert out.result == "agent() prompt must be a string, not list"
        assert host.prompts == []  # nothing was sent

    async def test_the_namespace_is_the_listed_one(self) -> None:
        out = await run("result([attempt(lambda: open('x')), attempt(lambda: __import__('os'))])")
        assert out.result[0].startswith("NameError")
        assert out.result[1].startswith("NameError")

    async def test_a_host_error_arrives_as_the_exception_it_was(self) -> None:
        async def fail(prompt: str, opts: dict[str, Any]) -> Any:
            raise ValueError("nope")

        out = await run(
            """
            try:
                await agent("x")
            except ValueError as e:
                result("caught " + str(e))
            """,
            host=FakeHost(fail),
        )

        assert out.result == "caught nope"

    async def test_an_error_of_a_type_scripts_cannot_name_arrives_as_a_runtime_error(self) -> None:
        class Odd(Exception):
            pass

        async def fail(prompt: str, opts: dict[str, Any]) -> Any:
            raise Odd("odd")

        out = await run(
            """
            try:
                await agent("x")
            except RuntimeError as e:
                result("caught " + str(e))
            """,
            host=FakeHost(fail),
        )

        assert out.result == "caught odd"

    async def test_a_result_too_large_to_send_is_an_error_in_the_script(self) -> None:
        out = await run(
            """result(attempt(lambda: result("x" * 100000)))""",
            limits=SandboxLimits(max_message_bytes=50_000),
        )
        assert out.result.startswith("ValueError: message too large")

    async def test_an_answer_too_large_to_send_is_an_error_the_script_can_catch(self) -> None:
        async def huge(prompt: str, opts: dict[str, Any]) -> Any:
            return "y" * 100_000

        out = await run(
            """
            try:
                await agent("x")
            except ValueError as e:
                result("caught")
            """,
            host=FakeHost(huge),
            limits=SandboxLimits(max_message_bytes=50_000),
        )

        assert out.result == "caught"


class TestAScriptThatRaises:
    async def test_the_host_gets_the_scripts_message_its_type_and_its_line(self) -> None:
        text = script('x = 1\nraise ValueError("no good")', prelude=False)

        with pytest.raises(WorkflowScriptError) as err:
            await run(text, whole_script=True)

        assert str(err.value) == "no good"  # nothing added: callers show it as it is
        assert err.value.error_type == "ValueError"
        assert err.value.line == 4  # the `raise` line of the script above
        assert isinstance(err.value, RuntimeError)

    async def test_a_syntax_error_says_where(self) -> None:
        with pytest.raises(WorkflowScriptError) as err:
            await run("async def main(:\n    pass\n", whole_script=True)

        assert err.value.error_type == "SyntaxError"
        assert err.value.line == 1

    async def test_the_error_of_a_nested_call_points_at_the_scripts_line(self) -> None:
        text = (
            "def helper(x):\n"
            "    return x.missing\n"  # line 2
            "async def main():\n"
            "    helper(1)\n"  # line 4
        )
        with pytest.raises(WorkflowScriptError) as err:
            await run(text, whole_script=True)

        assert err.value.error_type == "AttributeError"
        assert err.value.line == 2  # the innermost line that is the script's own

    async def test_a_long_message_is_cut_so_that_it_can_still_be_sent(self) -> None:
        with pytest.raises(WorkflowScriptError) as err:
            await run('raise RuntimeError("x" * 100000)')

        assert str(err.value) == "x" * 20000


class TestAHostileProcess:
    """What the host does with a pipe that carries more than the protocol allows."""

    async def test_a_message_over_the_limit_ends_the_run(self) -> None:
        with pytest.raises(SandboxProtocolError, match="larger than 65536"):
            await run(
                '_imp("os").write(_wire(), b"x" * 300000 + b"\\n")\nawait agent("never")',
                limits=SandboxLimits(max_message_bytes=64 * 1024),
            )

    @pytest.mark.parametrize(
        ("line", "complaint"),
        [
            ("not json at all", "invalid JSON"),
            ("[1, 2, 3]", "malformed message"),
            ('{"no": "type"}', "malformed message"),
            ('{"t": "launch-missiles"}', "unknown message"),
            ('{"t": "log"}', "bad 'msg'"),
            ('{"t": "log", "msg": 5}', "bad 'msg'"),
            ('{"t": "phase", "title": null}', "bad 'title'"),
            ('{"t": "result"}', "bad result"),
            ('{"t": "done", "meta": [1]}', "bad meta"),
            ('{"t": "started", "layers": "all"}', "bad 'layers'"),
            ('{"t": "call", "id": "1", "fn": "agent", "prompt": "x", "opts": {}}', "bad 'id'"),
            ('{"t": "call", "id": true, "fn": "agent", "prompt": "x", "opts": {}}', "bad 'id'"),
            ('{"t": "call", "id": 1, "fn": "agent", "prompt": 7, "opts": {}}', "bad 'prompt'"),
            ('{"t": "call", "id": 1, "fn": "agent", "prompt": "x", "opts": []}', "bad 'opts'"),
            ('{"t": "call", "id": 99, "fn": "os.system", "prompt": "x", "opts": {}}', "bad call"),
            ('{"t": "cancel", "id": "one"}', "bad 'id'"),
        ],
    )
    async def test_what_the_protocol_does_not_allow_ends_the_run(
        self, line: str, complaint: str
    ) -> None:
        with pytest.raises(SandboxProtocolError, match=complaint):
            await run(_raw(line) + '\nawait agent("never")')

    async def test_a_call_id_in_use_cannot_be_used_again(self) -> None:
        async def hold(prompt: str, opts: dict[str, Any]) -> Any:
            await asyncio.sleep(3600)

        both = _raw('{"t": "call", "id": 7, "fn": "agent", "prompt": "a", "opts": {}}')
        both += "\n" + _raw('{"t": "call", "id": 7, "fn": "agent", "prompt": "b", "opts": {}}')

        with pytest.raises(SandboxProtocolError, match="bad call"):
            await run(both + '\nawait agent("hold")', host=FakeHost(hold))

    async def test_json_nested_too_deep_to_parse_is_invalid_json(self) -> None:
        with pytest.raises(SandboxProtocolError, match="invalid JSON"):
            await run(_raw("[" * 100000) + '\nawait agent("never")')

    async def test_a_line_number_that_is_not_one_is_no_line_number(self) -> None:
        forged = _raw('{"t": "error", "etype": "X", "message": "m", "line": "7"}')

        with pytest.raises(WorkflowScriptError) as err:
            await run(forged + "\nawait agent('x')")

        assert err.value.line is None

    async def test_a_message_cut_short_by_the_process_ending_is_a_crash(self) -> None:
        with pytest.raises(SandboxCrashed, match="exit code 5"):
            await run(
                '_imp("os").write(_wire(), b\'{"t":"log","msg":"cut o\')\n_imp("os")._exit(5)'
            )

    async def test_a_cancel_for_a_call_nobody_made_is_ignored(self) -> None:
        out = await run(_raw('{"t": "cancel", "id": 12345}') + '\nresult("fine")')
        assert out.result == "fine"

    async def test_the_process_cannot_end_the_run_with_a_forged_failure_it_did_not_have(
        self,
    ) -> None:
        """A forged ``error`` is the same as the script raising: the script could do that."""
        with pytest.raises(WorkflowScriptError, match="forged"):
            await run(
                _raw('{"t": "error", "etype": "X", "message": "forged"}') + "\nawait agent('x')"
            )


class TestFloods:
    async def test_log_lines_beyond_the_limit_are_dropped_with_one_note(self) -> None:
        host = FakeHost()
        await run(
            'for i in range(2000):\n    log("line " + str(i))\nresult("fine")',
            host=host,
            limits=SandboxLimits(max_log_lines=50),
        )

        assert host.logs[:50] == [f"line {i}" for i in range(50)]
        assert len(host.logs) == 51
        assert "dropped" in host.logs[50]

    async def test_log_text_beyond_the_limit_is_cut(self) -> None:
        host = FakeHost()
        await run(
            'for i in range(10):\n    log("x" * 300)',  # the fourth line crosses the limit
            host=host,
            limits=SandboxLimits(max_log_chars=1000),
        )

        assert [len(line) for line in host.logs[:-1]] == [300, 300, 300, 100]
        assert "dropped" in host.logs[-1]

    async def test_phases_beyond_the_limit_are_ignored(self) -> None:
        host = FakeHost()
        await run(
            'for i in range(100):\n    phase("phase " + str(i))',
            host=host,
            limits=SandboxLimits(max_phases=5),
        )

        assert host.phases == [f"phase {i}" for i in range(5)]

    async def test_a_phase_already_seen_can_be_returned_to_after_the_limit(self) -> None:
        host = FakeHost()
        await run(
            'phase("a")\nphase("b")\nphase("c")\nphase("a")',
            host=host,
            limits=SandboxLimits(max_phases=2),
        )

        assert host.phases == ["a", "b", "a"]  # "c" was one too many; "a" is not new

    async def test_a_phase_title_is_cut_to_a_sensible_length(self) -> None:
        host = FakeHost()
        await run('phase("t" * 100000)', host=host)

        assert host.phases == ["t" * 500]

    async def test_and_the_host_cuts_a_title_that_was_not_cut_before_it_was_sent(self) -> None:
        host = FakeHost()
        await run(_raw('{"t":"phase","title":"' + "t" * 100000 + '"}'), host=host)

        assert host.phases == ["t" * 500]

    async def test_the_host_is_not_asked_for_more_at_once_than_it_agreed_to(self) -> None:
        active = peak = 0

        async def slow(prompt: str, opts: dict[str, Any]) -> Any:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return prompt

        out = await run(
            """
            answers = await parallel([lambda i=i: agent(str(i)) for i in range(120)])
            result(answers == [str(i) for i in range(120)])
            """,
            host=FakeHost(slow),
            limits=SandboxLimits(max_inflight_calls=8),
        )

        assert out.result is True  # every call was answered, in order
        assert peak <= 8


class TestALifeCycle:
    async def test_aborting_the_run_kills_the_process_and_unwinds_what_was_in_flight(
        self,
    ) -> None:
        started = asyncio.Event()
        unwound: list[str] = []

        async def slow(prompt: str, opts: dict[str, Any]) -> Any:
            started.set()
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                await asyncio.sleep(0.1)  # unwinding takes a while, and must be waited for
                unwound.append(prompt)
                raise

        host = FakeHost(slow)
        task = asyncio.ensure_future(
            run('log(str(_imp("os").getpid()))\nawait agent("hold")', host=host)
        )
        await asyncio.wait_for(started.wait(), 30)
        pid = int(host.logs[0])
        assert alive(pid)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert unwound == ["hold"]  # finished unwinding before the cancel got through
        assert not alive(pid)

    async def test_the_wall_clock_stops_a_script_that_waits_on_the_host(self) -> None:
        unwound: list[str] = []

        async def never(prompt: str, opts: dict[str, Any]) -> Any:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                unwound.append(prompt)
                raise

        with pytest.raises(SandboxLimitExceeded, match="time limit"):
            await run(
                'await agent("hold")', host=FakeHost(never), limits=SandboxLimits(wall_seconds=1)
            )

        assert unwound == ["hold"]

    async def test_a_call_the_script_gives_up_on_is_cancelled_at_the_host(self) -> None:
        """Only an ``asyncio.wait_for`` can do this, and the namespace has none: this script
        has got out of it."""
        cancelled = asyncio.Event()

        async def host_agent(prompt: str, opts: dict[str, Any]) -> Any:
            if prompt == "blocked":
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            await asyncio.wait_for(cancelled.wait(), 20)  # set only by a cancel sent mid-run
            return "saw the cancel"

        out = await run(
            """
            try:
                await _imp("asyncio").wait_for(agent("blocked"), 0.3)
            except Exception:
                pass
            result(await agent("after"))
            """,
            host=FakeHost(host_agent),
        )

        assert out.result == "saw the cancel"

    async def test_parallel_starts_nothing_when_one_of_its_thunks_cannot_be_started(self) -> None:
        host = FakeHost()

        out = await run(
            """
            try:
                await parallel([lambda: agent("a"), 5])  # the second is not callable
            except TypeError:
                result("refused")
            await _imp("asyncio").sleep(0.3)  # time enough for "a" to be asked, if it still is
            """,
            host=host,
        )

        assert out.result == "refused"
        assert host.prompts == []

    async def test_a_finished_run_leaves_no_task_behind(self) -> None:
        before = asyncio.all_tasks()

        await run('await parallel([lambda: agent("a"), lambda: agent("b")])')
        await asyncio.sleep(0)

        assert asyncio.all_tasks() <= before

    async def test_a_process_that_ends_without_a_word_is_reported_with_its_exit_code(self) -> None:
        with pytest.raises(SandboxCrashed, match="exit code 7"):
            await run('_imp("os")._exit(7)')

    async def test_its_error_output_is_part_of_the_report(self) -> None:
        with pytest.raises(SandboxCrashed, match="trace-marker-123"):
            await run(
                '_imp("sys").stderr.write("trace-marker-123\\n")\n'
                '_imp("sys").stderr.flush()\n'
                '_imp("os")._exit(1)'
            )

    async def test_only_the_end_of_a_long_error_output_is_kept(self) -> None:
        with pytest.raises(SandboxCrashed, match="end-marker") as err:
            await run(
                '_imp("sys").stderr.write("x" * 100000 + "end-marker\\n")\n'
                '_imp("sys").stderr.flush()\n'
                '_imp("os")._exit(1)'
            )

        assert len(str(err.value)) < 3000

    @pytest.mark.skipif(sys.platform == "win32", reason="signals (POSIX)")
    async def test_a_process_killed_by_a_signal_is_reported_by_its_name(self) -> None:
        with pytest.raises(SandboxCrashed, match="killed by SIGKILL"):
            await run('_imp("os").kill(_imp("os").getpid(), 9)', audit_hook=False)

    async def test_the_process_ends_when_the_host_goes_away(self) -> None:
        """What a crashed host looks like from the other side of the pipe: end of input."""
        child = sandbox_host._Child(SandboxLimits(), audit_hook=True)
        await child.start()
        try:
            ready = await child.read_message()
            assert ready is not None and ready["t"] == "ready"

            assert child.proc is not None and child.proc.stdin is not None
            child.proc.stdin.close()

            assert await asyncio.wait_for(child.proc.wait(), 10) == 3
        finally:
            await child.close()

    async def test_a_host_that_is_killed_leaves_no_script_process_behind(self) -> None:
        """No chance to clean up: the pipe closes (POSIX) and the job is closed (Windows)."""
        tests_dir = Path(__file__).parent
        host_program = textwrap.dedent(
            f"""
            import asyncio, os, sys
            sys.path.insert(0, {str(tests_dir)!r})
            from sandbox_support import FakeHost, run

            class Host(FakeHost):
                def log(self, message):
                    print(message, flush=True)

            async def hold(prompt, opts):
                await asyncio.sleep(3600)

            print(os.getpid(), flush=True)
            asyncio.run(run(
                'log(str(_imp("os").getpid()))\\nawait agent("hold")',
                host=Host(hold),
                audit_hook=False,
            ))
            """
        )
        host = await asyncio.create_subprocess_exec(
            sys.executable, "-c", host_program, stdout=asyncio.subprocess.PIPE
        )
        assert host.stdout is not None
        script_pid = 0
        try:
            host_pid = int((await asyncio.wait_for(host.stdout.readline(), 60)).decode())
            script_pid = int((await asyncio.wait_for(host.stdout.readline(), 60)).decode())
            assert alive(script_pid)

            # The interpreter that runs the host, not the launcher that started it (Windows).
            hard_kill = getattr(signal, "SIGKILL", signal.SIGTERM)
            os.kill(host_pid, hard_kill)
            await asyncio.wait_for(host.wait(), 30)

            for _ in range(150):
                if not alive(script_pid):
                    break
                await asyncio.sleep(0.1)
            assert not alive(script_pid)
        finally:
            if script_pid and alive(script_pid):
                os.kill(script_pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            with contextlib.suppress(ProcessLookupError):
                host.kill()

    async def test_its_error_output_is_read_as_utf8_whatever_the_locale(self) -> None:
        with pytest.raises(SandboxCrashed, match="错误-marker"):
            await run(
                '_imp("sys").stderr.write("错误-marker\\n")\n'
                '_imp("sys").stderr.flush()\n'
                '_imp("os")._exit(1)'
            )

    async def test_a_process_that_cannot_be_started_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sandbox_host, "_interpreter", lambda: str(tmp_path / "no-python"))

        with pytest.raises(SandboxUnavailable, match="could not start"):
            await run("pass")

    async def test_a_script_too_large_to_send_is_refused_before_any_process_starts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sandbox_host, "_interpreter", lambda: pytest.fail("a process started"))

        with pytest.raises(SandboxLimitExceeded, match="at most 10000 can be sent"):
            await run("x = '" + "a" * 20_000 + "'", limits=SandboxLimits(max_message_bytes=10_000))

    async def test_an_event_loop_that_cannot_run_subprocesses_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def unsupported(*args: Any, **kwargs: Any) -> Any:
            raise NotImplementedError

        monkeypatch.setattr(asyncio, "create_subprocess_exec", unsupported)

        with pytest.raises(SandboxUnavailable, match="cannot start subprocesses"):
            await run("pass")

    async def test_a_process_that_never_says_ready_is_unavailable_and_is_killed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        mute = tmp_path / "mute.py"
        mute.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
        monkeypatch.setattr(sandbox_host, "CHILD_SCRIPT", mute)
        started = time.monotonic()

        with pytest.raises(SandboxUnavailable, match=r"did not start within 0\.5 s"):
            await run("pass", limits=SandboxLimits(startup_seconds=0.5))

        assert time.monotonic() - started < 20

    async def test_a_process_that_speaks_another_protocol_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        odd = tmp_path / "odd.py"
        odd.write_text(
            "import os, time\n"
            'os.write(1, b\'{"t": "ready", "protocol": 99}\\n\')\n'
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(sandbox_host, "CHILD_SCRIPT", odd)

        with pytest.raises(SandboxUnavailable, match="another protocol"):
            await run("pass")


class TestTheRuntimeUsesIt:
    class _Agents(SubagentExecutor):
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def run_agent(self, prompt: str, **kwargs: Any) -> AgentResult:
            self.calls.append((prompt, kwargs))
            return AgentResult(text="ok", tokens_used=5)

    async def test_a_script_that_raises_makes_execute_raise_its_message(self) -> None:
        runtime = WorkflowRuntime(self._Agents())

        with pytest.raises(WorkflowScriptError, match="bad script") as err:
            await runtime.execute('async def main():\n    raise RuntimeError("bad script")\n')

        assert isinstance(err.value, RuntimeError)

    async def test_the_script_is_given_the_runtimes_directory_arguments_and_budget(
        self, tmp_path: Path
    ) -> None:
        runtime = WorkflowRuntime(self._Agents(), cwd=str(tmp_path), token_budget=100)

        run = await runtime.execute(
            "async def main():\n"
            "    await agent('x')\n"
            "    result([cwd, args, budget.total, budget.spent(), budget.remaining()])\n",
            args={"k": [1]},
        )

        assert run.result == [str(tmp_path), {"k": [1]}, 100, 5, 95]

    def test_every_bundled_workflow_has_arguments_here(self) -> None:
        assert set(BUNDLED_WORKFLOW_ARGS) == set(BUILTIN_WORKFLOW_NAMES)

    @pytest.mark.parametrize("name", sorted(BUNDLED_WORKFLOW_ARGS))
    async def test_the_bundled_workflows_run(self, name: str) -> None:
        """They were written for the old in-process namespace: all they use must still be there."""
        args = BUNDLED_WORKFLOW_ARGS[name]
        invocation = resolve_builtin_workflow(name, args)
        assert invocation is not None
        agents = self._Agents()

        run = await WorkflowRuntime(agents).execute(invocation.script, args)

        assert run.result == "ok"  # the last stage's answer
        assert run.agent_count == len(agents.calls) >= 3
        assert run.phases

    async def test_a_phase_entered_again_is_listed_once(self) -> None:
        runtime = WorkflowRuntime(self._Agents())

        run = await runtime.execute(
            'async def main():\n    phase("a")\n    phase("b")\n    phase("a")\n'
        )

        assert run.phases == ["a", "b"]

    async def test_the_runtime_passes_its_limits_on(self) -> None:
        runtime = WorkflowRuntime(self._Agents(), sandbox_limits=SandboxLimits(wall_seconds=1.0))

        with pytest.raises(SandboxLimitExceeded):
            await runtime.execute("async def main():\n    " + SPIN.replace("\n", "\n    ") + "\n")

    @pytest.mark.parametrize(
        ("call", "complaint"),
        [
            ("agent('x', tier=5)", "tier must be a string"),
            ("agent('x', model=['m'])", "model must be a string"),
            ("agent('x', timeout_ms='soon')", "timeout_ms must be a number"),
            ("agent('x', timeout_ms=True)", "timeout_ms must be a number"),
            ("agent('x', schema='object')", "schema must be a dict"),
            ("agent('x', cwd=0)", "cwd must be a string"),
            ("agent('x', phase=['p'])", "phase must be a string"),
            ("agent('x', label=1.5)", "label must be a string"),
        ],
    )
    async def test_options_that_are_not_what_agent_takes_are_refused(
        self, call: str, complaint: str
    ) -> None:
        agents = self._Agents()
        runtime = WorkflowRuntime(agents)

        with pytest.raises(WorkflowScriptError, match=complaint):
            await runtime.execute(f"async def main():\n    await {call}\n")

        assert agents.calls == []
        assert runtime._agent_count == 0

    async def test_a_call_is_made_in_the_phase_the_script_was_in_when_it_made_it(self) -> None:
        """The host answers calls as tasks of its own: by the time one runs, the script may have
        gone on to the next phase (a thunk beside it), but the call belongs to the old one."""
        agents = self._Agents()
        runtime = WorkflowRuntime(agents)

        await runtime.execute(
            textwrap.dedent(
                """
                async def early():
                    return await agent("early")

                async def late():
                    phase("late")
                    return await agent("late")

                async def main():
                    phase("first")
                    await parallel([early, late])
                    await agent("after")
                    await agent("explicit", phase="mine")
                    await agent("blank", phase="")
                """
            )
        )

        assert {prompt: kwargs["phase"] for prompt, kwargs in agents.calls} == {
            "early": "first",
            "late": "late",
            "after": "late",
            "explicit": "mine",
            "blank": "late",
        }

    async def test_a_long_phase_title_is_cut_before_the_calls_made_in_it_carry_it(self) -> None:
        agents = self._Agents()

        await WorkflowRuntime(agents).execute(
            'async def main():\n    phase("t" * 1000)\n    await agent("x")\n'
        )

        assert agents.calls[0][1]["phase"] == "t" * 500

    async def test_meta_that_is_not_a_dict_is_the_default_and_odd_values_become_text(self) -> None:
        runtime = WorkflowRuntime(self._Agents())

        odd = await runtime.execute('meta = {"name": 5, "description": None, "phases": "x"}')
        none = await runtime.execute("meta = [1, 2]")

        assert (odd.meta.name, odd.meta.description, odd.meta.phases) == ("5", "None", [])
        assert none.meta.name == "unnamed"
