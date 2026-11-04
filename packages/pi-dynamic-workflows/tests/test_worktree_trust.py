"""Worktree isolation runs ``git`` in the project, so it needs a trusted project.

``git worktree add`` runs the project's ``post-checkout`` hook, and a repository's own
``.git/config`` can name more programs that ``checkout``, ``diff``, ``add`` and ``apply`` run
(``core.fsmonitor``, a checkout or clean filter, ``diff.external``). A project that ships its
``.git`` (an unpacked archive) could so run code the moment a ``workflow`` call asked for
``isolation``. Invariant 9: what the project supplies is used only once the user vouched for
the project (``pi.project_trusted is True``).

The tests use a real ``git`` and programs that only create a marker file in a temporary
directory.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pi_dynamic_workflows import activate
from pi_dynamic_workflows import worktree as worktree_module
from pi_dynamic_workflows.runtime import AgentResult, SubagentExecutor
from pi_dynamic_workflows.subagent import HarnessSubagentExecutor
from pi_dynamic_workflows.workflow_tool import WorkflowParams, create_workflow_tool
from pi_dynamic_workflows.worktree import WorktreeManager

from pi_agent_core.tests.mock_stream import mock_text_stream
from pi_agent_core.types import Model

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "the hook and the config programs are POSIX shell scripts that need the executable "
        "bit; git for Windows would run them through its own sh, which was not measured"
    ),
)

ONE_AGENT = 'meta = {"name": "one"}\nasync def main():\n    await agent("go")\n    result("done")\n'


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _program(root: Path, name: str, *, passthrough: bool = False) -> tuple[Path, Path]:
    """A script that creates ``m-<name>`` in *root* and does nothing else."""
    marker = root / f"m-{name}"
    body = f": > {shlex.quote(str(marker))}\n" + ("cat\n" if passthrough else "")
    script = root / f"{name}.sh"
    script.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    script.chmod(0o755)
    return script, marker


@pytest.fixture()
def world(tmp_path: Path) -> Any:
    """A repository that names three programs of its own: a ``post-checkout`` hook, a
    ``core.fsmonitor`` program and a clean filter (``*.dat`` in ``.gitattributes``)."""
    root = tmp_path / "markers"
    root.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("original\n", encoding="utf-8")
    (repo / "f.dat").write_text("data\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("*.dat filter=hostile\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")

    hook, hook_marker = _program(root, "hook")
    hook_target = repo / ".git" / "hooks" / "post-checkout"
    hook_target.write_text(hook.read_text(encoding="utf-8"), encoding="utf-8")
    hook_target.chmod(0o755)
    fsmonitor, fsmonitor_marker = _program(root, "fsmonitor")
    _git(repo, "config", "core.fsmonitor", shlex.quote(str(fsmonitor)))
    clean, clean_marker = _program(root, "clean", passthrough=True)
    _git(repo, "config", "filter.hostile.clean", shlex.quote(str(clean)))

    markers = {"hook": hook_marker, "fsmonitor": fsmonitor_marker, "clean": clean_marker}
    for marker in markers.values():
        marker.unlink(missing_ok=True)  # setup may have run the programs
    return SimpleNamespace(
        repo=repo,
        tmp=tmp_path,
        markers=markers,
        fired=lambda: sorted(name for name, marker in markers.items() if marker.exists()),
    )


class _Bridge:
    stream_fn = None
    model = Model(provider="mock", model_id="m1")

    def __init__(self, stream_fn: Any) -> None:
        self.stream_fn = stream_fn

    def register_cleanup(self, callback: Any) -> None:
        return None


class _Pi:
    """What ``activate`` uses of the extension API. ``trusted`` is what ``project_trusted``
    answers, or raises when it is an exception."""

    def __init__(self, world: Any, trusted: Any, stream_fn: Any = mock_text_stream) -> None:
        self.cwd = str(world.repo)
        self.home = world.tmp / "pi-home"
        self._trusted = trusted
        self._bridge = _Bridge(stream_fn)
        self.tools: list[Any] = []

    @property
    def project_trusted(self) -> Any:
        if isinstance(self._trusted, Exception):
            raise self._trusted
        return self._trusted

    def _require_bridge(self) -> Any:
        return self._bridge

    def register_tool(self, tool: Any) -> None:
        self.tools.append(tool)

    def register_command(self, *args: Any, **kwargs: Any) -> None:
        return None

    def workflow_tool(self) -> Any:
        (tool,) = [t for t in self.tools if t.name == "workflow"]
        return tool


class _CountingStream:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, model: Any, context: Any, options: Any = None) -> Any:
        self.calls += 1
        return await mock_text_stream(model, context, options)


async def _isolated_call(world: Any, trusted: Any, stream: Any = None) -> str:
    pi = _Pi(world, trusted, stream or mock_text_stream)
    activate(pi)  # type: ignore[arg-type]
    result = await pi.workflow_tool().execute(
        "tc", WorkflowParams(script=ONE_AGENT, isolation=True)
    )
    return result.content[0]["text"]


# ---------------------------------------------------------------------------
# The tool, as the extension builds it
# ---------------------------------------------------------------------------


async def test_isolation_in_an_untrusted_project_runs_no_program_of_the_repository(world: Any):
    stream = _CountingStream()

    text = await _isolated_call(world, trusted=False, stream=stream)

    assert world.fired() == []
    assert stream.calls == 0  # nothing ran, not even without isolation
    assert "isolation" in text and "not run" in text


async def test_isolation_in_a_trusted_project_runs_as_before(world: Any):
    """The control: the programs fire when the user vouched for the project."""
    stream = _CountingStream()

    text = await _isolated_call(world, trusted=True, stream=stream)

    assert "completed" in text
    assert stream.calls == 1
    assert {"hook", "fsmonitor"} <= set(world.fired())


@pytest.mark.parametrize(
    "answer",
    [False, None, 1, "yes", [True], RuntimeError("no bridge"), AttributeError("older core")],
)
async def test_anything_but_true_is_no(world: Any, answer: Any):
    text = await _isolated_call(world, trusted=answer)

    assert world.fired() == []
    assert "not run" in text


async def test_a_run_without_isolation_does_not_need_trust(world: Any):
    pi = _Pi(world, trusted=False)
    activate(pi)  # type: ignore[arg-type]

    result = await pi.workflow_tool().execute("tc", WorkflowParams(script=ONE_AGENT))

    assert "completed" in result.content[0]["text"]
    assert world.fired() == []


async def test_a_background_run_with_isolation_is_refused_before_it_starts(world: Any):
    pi = _Pi(world, trusted=False)
    activate(pi)  # type: ignore[arg-type]

    result = await pi.workflow_tool().execute(
        "tc", WorkflowParams(script=ONE_AGENT, isolation=True, background=True)
    )

    assert "not run" in result.content[0]["text"]
    assert world.fired() == []
    assert not result.terminate


async def test_the_refusal_says_how_to_get_isolation(world: Any):
    text = await _isolated_call(world, trusted=False)

    for part in (
        "not trusted",
        "trusted_projects",
        "--trust-project-extensions",
        "without isolation",
    ):
        assert part in text, part


async def test_isolation_is_refused_when_the_executor_cannot_isolate(world: Any):
    """It used to be dropped without a word: the agents then ran in the project itself."""

    class Plain(SubagentExecutor):
        calls = 0

        async def run_agent(self, prompt: str, **kwargs: Any) -> AgentResult:
            Plain.calls += 1
            return AgentResult(text="done")

    tool = create_workflow_tool(
        executor=Plain(), cwd=str(world.repo), home=world.tmp / "pi-home", project_trusted=True
    )

    result = await tool.execute("tc", WorkflowParams(script=ONE_AGENT, isolation=True))

    assert Plain.calls == 0
    assert "cannot" in result.content[0]["text"] and "not run" in result.content[0]["text"]


async def test_the_tool_made_without_a_word_about_trust_refuses_isolation(world: Any):
    tool = create_workflow_tool(
        executor=HarnessSubagentExecutor(
            stream_fn=mock_text_stream,
            parent_model=Model(provider="mock", model_id="m1"),
            cwd=str(world.repo),
        ),
        cwd=str(world.repo),
        home=world.tmp / "pi-home",
    )

    result = await tool.execute("tc", WorkflowParams(script=ONE_AGENT, isolation=True))

    assert "not run" in result.content[0]["text"]
    assert world.fired() == []


# ---------------------------------------------------------------------------
# The manager: git does not start for a project nobody vouched for
# ---------------------------------------------------------------------------


async def test_a_trusted_manager_runs_the_programs_the_repository_names(world: Any):
    """The control: the markers are a real signal."""
    manager = WorktreeManager(str(world.repo), project_trusted=True)

    wt = await manager.create()
    (Path(wt) / "a.txt").write_text("agent\n", encoding="utf-8")
    await manager.apply_changes(wt)
    await manager.cleanup(wt)

    assert world.fired() == ["clean", "fsmonitor", "hook"]


async def test_a_manager_is_untrusted_unless_told_otherwise(world: Any):
    manager = WorktreeManager(str(world.repo))

    with pytest.raises(worktree_module.UntrustedProjectError, match="not trusted"):
        await manager.create()

    assert world.fired() == []
    assert not list((world.tmp).glob("**/pi-wt-*"))


@pytest.mark.parametrize("step", ["collect_diff", "apply_changes", "cleanup"])
async def test_no_step_of_an_untrusted_manager_starts_git(world: Any, step: str):
    manager = WorktreeManager(str(world.repo), project_trusted=False)

    with pytest.raises(worktree_module.UntrustedProjectError):
        await getattr(manager, step)(str(world.repo))

    assert world.fired() == []


async def test_a_sub_agent_of_an_untrusted_manager_does_not_run(world: Any):
    """The failure path of ``subagent.py``: the agent is not run without its isolation."""
    stream = _CountingStream()
    executor = HarnessSubagentExecutor(
        stream_fn=stream,
        parent_model=Model(provider="mock", model_id="m1"),
        cwd=str(world.repo),
    ).with_worktree_manager(WorktreeManager(str(world.repo)))

    result = await executor.run_agent("go")

    assert result.error is not None and "worktree creation failed" in result.error
    assert stream.calls == 0
    assert world.fired() == []
