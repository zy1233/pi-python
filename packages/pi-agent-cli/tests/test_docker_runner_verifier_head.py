"""The DeepSWE verifier must see HEAD where the container started, not where the agent left it.

The task text tells the agent to work on a new branch and to commit. DeepSWE's ``grader.py``
resets the files that ``test.patch`` touches with ``git checkout HEAD -- <file>`` and deletes
a file only when HEAD does not have it. An agent that committed a test file with the name of
a hidden test file (``tests/test_multipart_response.py`` in ``httpx-multipart-response-parsing``)
made ``git apply test.patch`` fail with "already exists in working directory"; the run scored
-1, although its code was not judged at all.

``run_verifier`` now moves HEAD back (``git reset --mixed``: HEAD and index only, the files stay)
for tasks that use ``grader.py``. These tests use a fake ``run_docker_sync``; they need no Docker.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pi_agent_cli.benchmarks import docker_runner
from pi_agent_cli.benchmarks.docker_runner import DockerContainerSession
from pi_agent_cli.benchmarks.models import BenchmarkTask

START = "a" * 40
AGENT = "b" * 40


class _HeadFake:
    """A fake docker. ``head`` is what ``git rev-parse HEAD`` prints in the container."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.head = START
        self.reset_rc = 0

    def __call__(
        self, args: list[str], *, timeout: float = 120.0, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        stdout, stderr, rc = "", "", 0
        if args[-4:] == ["-C", "/app", "rev-parse", "HEAD"]:
            stdout = self.head + "\n"
        elif "reset" in args:  # git reset ... <commit>
            rc = self.reset_rc
            if rc:
                stderr = "fatal: could not reset"
            else:
                self.head = args[-1]
        elif args[-2:] == ["cat", "/logs/verifier/reward.json"]:
            stdout = '{"reward": 1}'
        return subprocess.CompletedProcess(args, rc, stdout, stderr)

    def index_of(self, predicate) -> int:
        for i, call in enumerate(self.calls):
            if predicate(call):
                return i
        raise AssertionError(f"no docker call matched; calls were: {self.calls}")

    def resets(self) -> list[list[str]]:
        return [call for call in self.calls if "reset" in call]


@pytest.fixture
def fake_docker(monkeypatch: pytest.MonkeyPatch) -> _HeadFake:
    fake = _HeadFake()
    monkeypatch.setattr(docker_runner, "run_docker_sync", fake)
    return fake


def _make_session(tmp_path: Path, *, deepswe_grader: bool) -> DockerContainerSession:
    task_dir = tmp_path / "task"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "tests" / "test.sh").write_text("echo hidden\n", encoding="utf-8")
    if deepswe_grader:
        (task_dir / "tests" / "grader.py").write_text("# grader\n", encoding="utf-8")
        (task_dir / "tests" / "config.json").write_text('{"base_commit": "x"}', encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    task = BenchmarkTask(
        id="demo-task",
        name="demo-task",
        suite="datacurve" if deepswe_grader else "terminal-bench",
        instruction="Do the thing.",
        task_dir=task_dir,
        metadata={"environment": {"docker_image": "example/img:1"}},
    )
    return DockerContainerSession(task, workspace, egress_mode="no-network")


def test_setup_records_the_head_the_container_started_with(
    tmp_path: Path, fake_docker: _HeadFake
) -> None:
    session = _make_session(tmp_path, deepswe_grader=True)

    session.setup()

    assert session._start_head == START


def test_setup_without_a_repository_records_no_head(tmp_path: Path, fake_docker: _HeadFake) -> None:
    fake_docker.head = "fatal: not a git repository"  # not a commit id
    session = _make_session(tmp_path, deepswe_grader=True)

    session.setup()

    assert session._start_head is None


def test_run_verifier_moves_head_back_after_the_agent_committed(
    tmp_path: Path, fake_docker: _HeadFake
) -> None:
    session = _make_session(tmp_path, deepswe_grader=True)
    session.setup()
    name = session.container_name
    fake_docker.head = AGENT  # the agent made a commit

    passed, output = session.run_verifier()

    assert passed, output
    i_reset = fake_docker.index_of(
        lambda c: c == ["exec", name, "git", "-C", "/app", "reset", "--mixed", "-q", START]
    )
    i_run = fake_docker.index_of(lambda c: c == ["exec", name, "bash", "/tests/test.sh"])
    assert i_reset < i_run
    assert fake_docker.head == START


def test_run_verifier_keeps_the_files_of_the_working_tree(
    tmp_path: Path, fake_docker: _HeadFake
) -> None:
    """Only HEAD and the index move. ``--hard`` would delete the agent's work."""
    session = _make_session(tmp_path, deepswe_grader=True)
    session.setup()
    fake_docker.head = AGENT

    session.run_verifier()

    for call in fake_docker.resets():
        assert "--hard" not in call
        assert "--mixed" in call


def test_run_verifier_leaves_head_alone_when_the_agent_did_not_commit(
    tmp_path: Path, fake_docker: _HeadFake
) -> None:
    session = _make_session(tmp_path, deepswe_grader=True)
    session.setup()

    passed, output = session.run_verifier()

    assert passed, output
    assert fake_docker.resets() == []


def test_run_verifier_leaves_head_alone_for_tasks_without_the_deepswe_grader(
    tmp_path: Path, fake_docker: _HeadFake
) -> None:
    """For ``sanitize-git-repo`` the rewritten history is the answer; do not undo it."""
    session = _make_session(tmp_path, deepswe_grader=False)
    session.setup()
    name = session.container_name
    fake_docker.head = AGENT

    passed, output = session.run_verifier()

    assert passed, output
    assert fake_docker.resets() == []
    assert fake_docker.head == AGENT
    assert ["exec", name, "bash", "/tests/test.sh"] in fake_docker.calls


def test_run_verifier_goes_on_when_head_cannot_be_moved(
    tmp_path: Path, fake_docker: _HeadFake, capsys: pytest.CaptureFixture[str]
) -> None:
    session = _make_session(tmp_path, deepswe_grader=True)
    session.setup()
    name = session.container_name
    fake_docker.head = AGENT
    fake_docker.reset_rc = 1

    passed, output = session.run_verifier()

    assert passed, output
    assert ["exec", name, "bash", "/tests/test.sh"] in fake_docker.calls
    assert "could not move HEAD back" in capsys.readouterr().out
