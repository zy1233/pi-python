"""Tests for the bash tool and its OutputAccumulator (pi semantics)."""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from pi_agent_core.adapters.langchain_convert import default_convert_to_llm
from pi_agent_core.agent_loop import run_agent_loop
from pi_agent_core.coding_tools.bash import (
    BashParams,
    ShellConfig,
    _format_output,
    _is_legacy_wsl_bash_path,
    create_bash_tool,
    get_shell_config,
)
from pi_agent_core.coding_tools.output_accumulator import OutputAccumulator
from pi_agent_core.event_stream import AssistantMessageEventStream
from pi_agent_core.messages import UserMessage
from pi_agent_core.tests.mock_stream import _base_partial
from pi_agent_core.types import (
    AgentContext,
    AgentLoopConfig,
    DoneEvent,
    Model,
    StartEvent,
)

try:
    _HAS_BASH = get_shell_config().shell != "cmd"
except ValueError:  # pragma: no cover - resolution never raises without shell_path
    _HAS_BASH = False

needs_bash = pytest.mark.skipif(not _HAS_BASH, reason="no bash shell available")


class _EventSignal:
    """Mirror of the runtime `_AbortSignal` (flag + waitable event)."""

    def __init__(self) -> None:
        self.aborted = False
        self._event = asyncio.Event()

    def abort(self) -> None:
        self.aborted = True
        self._event.set()

    async def wait_aborted(self) -> None:
        await self._event.wait()


class _FlagSignal:
    """Degraded signal: only the `.aborted` boolean, no wait_aborted()."""

    def __init__(self) -> None:
        self.aborted = False


def _text(result) -> str:
    return result.content[0]["text"]


# --- shell resolution ---


def test_legacy_wsl_bash_switches_to_stdin_transport():
    assert _is_legacy_wsl_bash_path("C:\\Windows\\System32\\bash.exe")
    assert _is_legacy_wsl_bash_path("c:/windows/sysnative/bash.exe")
    assert not _is_legacy_wsl_bash_path("C:\\Program Files\\Git\\bin\\bash.exe")


def test_custom_shell_path_missing_raises(tmp_path):
    with pytest.raises(ValueError, match="Custom shell path not found"):
        get_shell_config(str(tmp_path / "nope" / "bash"))


def test_resolved_shell_exists_or_is_platform_fallback():
    config = get_shell_config()
    assert config.shell in ("cmd", "sh") or os.path.exists(config.shell)


# --- timeout validation (pi wording) ---


async def test_invalid_timeout_rejected(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    with pytest.raises(ValueError, match="Invalid timeout: must be a finite number of seconds"):
        await tool.execute("t1", BashParams(command="echo hi", timeout=0))
    with pytest.raises(ValueError, match=r"Invalid timeout: maximum is 2147483\.647 seconds"):
        await tool.execute("t1", BashParams(command="echo hi", timeout=3e9))


# --- output formatting (footer wording, all three truncation branches) ---


def _snapshot(acc: OutputAccumulator):
    return acc.snapshot(persist_if_truncated=False)


def test_format_output_untruncated_and_empty_placeholder():
    acc = OutputAccumulator()
    acc.append(b"hello\n")
    acc.finish()
    text, details = _format_output(_snapshot(acc), acc.get_last_line_bytes())
    assert text == "hello\n"
    assert details is None

    empty = OutputAccumulator()
    empty.finish()
    text, details = _format_output(_snapshot(empty), 0)
    assert text == "(no output)"
    assert details is None


def test_format_output_lines_footer():
    acc = OutputAccumulator(max_lines=3, max_bytes=10_000)
    acc.append(b"1\n2\n3\n4\n5\n")
    acc.finish()
    snapshot = acc.snapshot(persist_if_truncated=True)
    text, details = _format_output(snapshot, acc.get_last_line_bytes())
    assert text.startswith("3\n4\n5")
    assert f"[Showing lines 3-5 of 5. Full output: {snapshot.full_output_path}]" in text
    assert details is not None
    assert details["truncation"]["truncatedBy"] == "lines"
    assert details["fullOutputPath"] == snapshot.full_output_path
    acc.close_temp_file()
    os.remove(snapshot.full_output_path)


def test_format_output_bytes_footer_hardcodes_default_limit():
    acc = OutputAccumulator(max_lines=100, max_bytes=8)
    acc.append(b"aa\nbb\ncc\ndd\n")
    acc.finish()
    snapshot = acc.snapshot(persist_if_truncated=True)
    text, details = _format_output(snapshot, acc.get_last_line_bytes())
    # pi's byte-limit footer always cites the default 50KB, by design.
    assert "(50.0KB limit). Full output:" in text
    assert details is not None
    assert details["truncation"]["truncatedBy"] == "bytes"
    acc.close_temp_file()
    os.remove(snapshot.full_output_path)


def test_format_output_partial_last_line_footer():
    acc = OutputAccumulator(max_lines=100, max_bytes=10)
    acc.append(b"x" * 40)  # single 40B line, no newline
    acc.finish()
    snapshot = acc.snapshot(persist_if_truncated=True)
    text, _ = _format_output(snapshot, acc.get_last_line_bytes())
    assert "[Showing last 10B of line 1 (line is 40B). Full output:" in text
    acc.close_temp_file()
    os.remove(snapshot.full_output_path)


# --- OutputAccumulator internals ---


def test_accumulator_small_output_needs_no_temp_file():
    acc = OutputAccumulator()
    acc.append(b"one\ntwo\n")
    acc.finish()
    snapshot = acc.snapshot(persist_if_truncated=True)
    assert snapshot.full_output_path is None
    assert snapshot.truncation.truncated is False
    assert snapshot.content == "one\ntwo\n"


def test_accumulator_spills_full_output_to_temp_file():
    acc = OutputAccumulator(max_lines=5, max_bytes=10_000, temp_file_prefix="pi-test")
    payload = "".join(f"line{i}\n" for i in range(10))
    acc.append(payload.encode())
    acc.finish()
    snapshot = acc.snapshot(persist_if_truncated=True)
    acc.close_temp_file()

    assert snapshot.truncation.truncated is True
    assert snapshot.truncation.total_lines == 10
    assert snapshot.content == "line5\nline6\nline7\nline8\nline9"
    assert snapshot.full_output_path is not None
    with open(snapshot.full_output_path, encoding="utf-8") as f:
        assert f.read() == payload
    os.remove(snapshot.full_output_path)


def test_accumulator_rolling_tail_starts_on_line_boundary():
    acc = OutputAccumulator(max_lines=10_000, max_bytes=64)
    full = "".join(f"row-{i:04d}\n" for i in range(200))
    for i in range(0, len(full), 33):  # odd chunk size to cross line boundaries
        acc.append(full[i : i + 33].encode())
    acc.finish()
    snapshot = acc.snapshot()
    assert snapshot.truncation.total_lines == 200
    # The retained tail must be a clean suffix made of whole lines only
    # (truncate_tail joins lines, so the trailing newline is dropped).
    assert full.endswith(snapshot.content + "\n")
    assert all(line.startswith("row-") for line in snapshot.content.split("\n"))


def test_accumulator_multibyte_utf8_split_across_chunks():
    acc = OutputAccumulator()
    encoded = "héllo wörld\n".encode()
    acc.append(encoded[:3])  # split inside "é" (0xC3 0xA9)
    acc.append(encoded[3:])
    acc.finish()
    assert acc.snapshot().content == "héllo wörld\n"


def test_accumulator_tracks_last_line_bytes():
    acc = OutputAccumulator()
    acc.append(b"first\nsecond-longer")
    acc.finish()
    assert acc.get_last_line_bytes() == len(b"second-longer")


def test_accumulator_append_after_finish_raises():
    acc = OutputAccumulator()
    acc.finish()
    with pytest.raises(RuntimeError, match="finished output accumulator"):
        acc.append(b"x")


# --- bash end-to-end (requires a bash shell) ---


@needs_bash
async def test_bash_echo_roundtrip(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    result = await tool.execute("t1", BashParams(command="echo hello"))
    assert _text(result) == "hello\n"
    assert result.details is None


@needs_bash
async def test_bash_runs_in_bound_cwd(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    await tool.execute("t1", BashParams(command="echo hi > out.txt"))
    assert (tmp_path / "out.txt").read_text(encoding="utf-8").strip() == "hi"


@needs_bash
async def test_bash_merges_stderr_in_arrival_order(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    result = await tool.execute("t1", BashParams(command="echo out; echo err 1>&2; echo late"))
    text = _text(result)
    assert text.index("out") < text.index("err") < text.index("late")


@needs_bash
async def test_bash_no_output_placeholder(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    result = await tool.execute("t1", BashParams(command="exit 0"))
    assert _text(result) == "(no output)"


@needs_bash
async def test_bash_unicode_output(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    result = await tool.execute("t1", BashParams(command="printf 'h\\xc3\\xa9llo\\n'"))
    assert _text(result) == "héllo\n"


@needs_bash
async def test_bash_nonzero_exit_raises_with_output_prefix(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    with pytest.raises(RuntimeError) as excinfo:
        await tool.execute("t1", BashParams(command="echo boom; exit 3"))
    message = str(excinfo.value)
    assert "boom" in message
    assert message.endswith("Command exited with code 3")


@needs_bash
async def test_bash_nonzero_exit_without_output(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    with pytest.raises(RuntimeError) as excinfo:
        await tool.execute("t1", BashParams(command="exit 7"))
    # pi's exit-code path keeps the "(no output)" placeholder as the prefix.
    assert str(excinfo.value) == "(no output)\n\nCommand exited with code 7"


@needs_bash
async def test_bash_timeout_wording_and_promptness(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    start = time.monotonic()
    with pytest.raises(RuntimeError, match=r"Command timed out after 0\.5 seconds$"):
        await tool.execute("t1", BashParams(command="sleep 5", timeout=0.5))
    assert time.monotonic() - start < 4


@needs_bash
async def test_bash_timeout_kills_process_tree(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    command = "(sleep 1 && echo x > marker.txt) & wait"
    with pytest.raises(RuntimeError, match="Command timed out"):
        await tool.execute("t1", BashParams(command=command, timeout=0.3))
    # If the subshell survived the kill it would create the marker at ~1s.
    await asyncio.sleep(1.5)
    assert not (tmp_path / "marker.txt").exists()


@needs_bash
async def test_bash_abort_kills_and_uses_pi_wording(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    signal = _EventSignal()
    task = asyncio.create_task(
        tool.execute("t1", BashParams(command="echo before; sleep 10"), signal)
    )
    await asyncio.sleep(0.4)
    signal.abort()
    start = time.monotonic()
    with pytest.raises(RuntimeError) as excinfo:
        await task
    assert time.monotonic() - start < 4
    message = str(excinfo.value)
    assert message.endswith("Command aborted")
    assert "before" in message  # captured output prefixes the status


@needs_bash
async def test_bash_abort_with_plain_flag_signal_polls(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    signal = _FlagSignal()
    task = asyncio.create_task(tool.execute("t1", BashParams(command="sleep 10"), signal))
    await asyncio.sleep(0.3)
    signal.aborted = True
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="Command aborted"):
        await task
    assert time.monotonic() - start < 4


@needs_bash
async def test_bash_pre_aborted_signal_short_circuits(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    signal = _EventSignal()
    signal.abort()
    with pytest.raises(RuntimeError, match=r"^Command aborted$"):
        await tool.execute("t1", BashParams(command="echo never"), signal)


@needs_bash
async def test_bash_tail_truncation_spills_full_output(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    result = await tool.execute("t1", BashParams(command="seq 1 3000"))
    text = _text(result)
    assert text.startswith("1001\n")
    assert "[Showing lines 1001-3000 of 3000. Full output:" in text

    assert result.details is not None
    truncation = result.details["truncation"]
    assert truncation["truncatedBy"] == "lines"
    assert truncation["totalLines"] == 3000
    assert truncation["outputLines"] == 2000

    full_path = result.details["fullOutputPath"]
    with open(full_path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert lines[0] == "1" and lines[-1] == "3000" and len(lines) == 3000
    os.remove(full_path)


@needs_bash
async def test_bash_on_update_streams_during_execution(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    updates: list = []

    def on_update(partial) -> None:
        updates.append(partial)

    result = await tool.execute(
        "t1", BashParams(command="echo first; sleep 0.6; echo second"), None, on_update
    )
    assert _text(result) == "first\nsecond\n"

    # Initial empty snapshot fires before the process spawns.
    assert updates[0].content == []
    texts = [u.content[0]["text"] for u in updates[1:]]
    # "first" was delivered while the command was still sleeping (B3 timing).
    assert any("first" in t and "second" not in t for t in texts)


@needs_bash
async def test_bash_missing_cwd_raises(tmp_path):
    tool = create_bash_tool(str(tmp_path / "gone"))
    with pytest.raises(ValueError, match="Working directory does not exist"):
        await tool.execute("t1", BashParams(command="echo hi"))


def test_shell_config_is_frozen_dataclass():
    config = ShellConfig(shell="sh", args=("-c",))
    assert config.command_transport == "argv"
    with pytest.raises(AttributeError):
        config.shell = "bash"  # type: ignore[misc]


# --- agent-loop integration (create_coding_tools group incl. bash) ---


async def _bash_tool_stream(model, context, options=None):
    stream = AssistantMessageEventStream()
    if any(getattr(m, "role", None) == "toolResult" for m in context.messages):
        partial = _base_partial(model, [{"type": "text", "text": "done"}])
        reason = "stop"
    else:
        partial = _base_partial(
            model,
            [
                {
                    "type": "toolCall",
                    "id": "call_bash",
                    "name": "bash",
                    "arguments": {"command": "echo from-loop"},
                }
            ],
        )
        partial.stopReason = "toolUse"
        reason = "toolUse"
    stream.push(StartEvent(partial=partial.model_copy(deep=True)))
    stream.push(DoneEvent(partial=partial.model_copy(deep=True), reason=reason))
    stream.set_final_message(partial)
    stream.end()
    return stream


@needs_bash
async def test_bash_in_agent_loop(tmp_path):
    from pi_agent_core.coding_tools import create_coding_tools

    ctx = AgentContext(system_prompt="", messages=[], tools=create_coding_tools(str(tmp_path)))
    config = AgentLoopConfig(
        model=Model(provider="mock", model_id="mock-1"),
        convert_to_llm=default_convert_to_llm,
    )
    prompt = UserMessage(content="run echo", timestamp=int(time.time() * 1000))

    events: list = []

    async def emit(e) -> None:
        events.append(e)

    await run_agent_loop([prompt], ctx, config, emit, stream_fn=_bash_tool_stream)
    types = [e.type for e in events]
    assert types[0] == "agent_start"
    assert types[-1] == "agent_end"

    tool_end = next(e for e in events if e.type == "tool_execution_end")
    assert tool_end.is_error is False
    assert tool_end.result.content == [{"type": "text", "text": "from-loop\n"}]
    # The initial empty on_update snapshot surfaced as a stream update event.
    assert "tool_execution_update" in types
