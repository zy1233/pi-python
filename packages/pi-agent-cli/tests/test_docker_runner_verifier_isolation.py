"""The verifier's tests must not be in the container while the agent works.

Before this check existed, ``DockerContainerSession.setup`` copied ``tests/`` to ``/tests``
right after the container started. The agent has a shell in that container, so it could
read ``test.patch``, ``grader.py`` or ``test_outputs.py`` (it did, in real runs).
The tests now enter the container only inside ``run_verifier``.

These tests use a fake ``run_docker_sync``. They do not need Docker.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pi_agent_cli.benchmarks import docker_runner
from pi_agent_cli.benchmarks.docker_runner import DockerContainerSession
from pi_agent_cli.benchmarks.models import BenchmarkTask


def _targets_container_tests(args: list[str]) -> bool:
    """True if a docker call reads, writes or runs ``/tests`` inside the container."""
    for arg in args:
        if arg == "/tests" or arg.startswith("/tests/") or arg.endswith(":/tests/"):
            return True
        if "/tests/*" in arg:  # the CRLF-strip script
            return True
    return False


class _FakeDocker:
    """Records every docker call. All calls succeed unless a test changes ``copy_tests_rc``."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.copy_tests_rc = 0

    def __call__(
        self, args: list[str], *, timeout: float = 120.0, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        stdout, stderr, rc = "", "", 0
        if args[0] == "cp" and args[-1].endswith(":/tests/"):
            rc = self.copy_tests_rc
            stderr = "boom" if rc else ""
        elif args[-2:] == ["cat", "/logs/verifier/reward.json"]:
            stdout = '{"reward": 1}'
        return subprocess.CompletedProcess(args, rc, stdout, stderr)

    def index_of(self, predicate) -> int:
        for i, call in enumerate(self.calls):
            if predicate(call):
                return i
        raise AssertionError(f"no docker call matched; calls were: {self.calls}")


@pytest.fixture
def fake_docker(monkeypatch: pytest.MonkeyPatch) -> _FakeDocker:
    fake = _FakeDocker()
    monkeypatch.setattr(docker_runner, "run_docker_sync", fake)
    return fake


def _make_session(tmp_path: Path, *, with_tests: bool = True) -> DockerContainerSession:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    if with_tests:
        (task_dir / "tests").mkdir()
        (task_dir / "tests" / "test.sh").write_text("echo hidden\n", encoding="utf-8")
        (task_dir / "tests" / "test.patch").write_text("hidden tests\n", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    task = BenchmarkTask(
        id="demo-task",
        name="demo-task",
        suite="datacurve",
        instruction="Do the thing.",
        task_dir=task_dir,
        metadata={"environment": {"docker_image": "example/img:1"}},
    )
    return DockerContainerSession(task, workspace, egress_mode="no-network")


def test_setup_keeps_verifier_files_out_of_the_container(
    tmp_path: Path, fake_docker: _FakeDocker
) -> None:
    session = _make_session(tmp_path)

    session.setup()

    assert session.is_running
    assert any(call[:2] == ["run", "-d"] for call in fake_docker.calls)
    leaked = [call for call in fake_docker.calls if _targets_container_tests(call)]
    assert leaked == []


def test_run_verifier_installs_tests_just_before_running_them(
    tmp_path: Path, fake_docker: _FakeDocker
) -> None:
    session = _make_session(tmp_path)
    session.setup()
    name = session.container_name

    passed, output = session.run_verifier()

    assert passed, output
    i_clean = fake_docker.index_of(lambda c: c == ["exec", name, "rm", "-rf", "/tests"])
    i_copy = fake_docker.index_of(lambda c: c[0] == "cp" and c[-1] == f"{name}:/tests/")
    i_run = fake_docker.index_of(lambda c: c == ["exec", name, "bash", "/tests/test.sh"])
    assert i_clean < i_copy < i_run


def test_run_verifier_fails_clearly_when_tests_cannot_be_copied(
    tmp_path: Path, fake_docker: _FakeDocker
) -> None:
    session = _make_session(tmp_path)
    session.setup()
    fake_docker.copy_tests_rc = 1

    passed, output = session.run_verifier()

    assert passed is False
    assert "Failed to copy tests" in output
    assert "boom" in output
    assert not any(call[-2:] == ["bash", "/tests/test.sh"] for call in fake_docker.calls)


def test_run_verifier_without_tests_dir_copies_nothing(
    tmp_path: Path, fake_docker: _FakeDocker
) -> None:
    session = _make_session(tmp_path, with_tests=False)
    session.setup()

    passed, output = session.run_verifier()

    assert passed is False
    assert "No verifier found" in output
    assert [call for call in fake_docker.calls if _targets_container_tests(call)] == []
