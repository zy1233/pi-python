"""A frozen build starts the script's process from a program of its own, not from the interpreter.

A frozen agent has no ``python`` to run ``child.py``, and its own ``sys.executable`` is the whole
agent. The build carries ``child.py`` frozen alone (standard library only) in a folder next to
the sandbox module; the host starts that program with the same options. The tests here cover
the choice of the command and, on a platform with shell scripts, a whole run through a stand-in
program. ``PI_SANDBOX_FROZEN_CHILD`` (see ``conftest``) runs the other sandbox tests against
the real frozen program.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest
from pi_dynamic_workflows.sandbox import SandboxUnavailable
from pi_dynamic_workflows.sandbox import host as sandbox_host
from sandbox_support import FakeHost, run

POSIX = os.name == "posix"


def _frozen(monkeypatch: pytest.MonkeyPatch, program: Path) -> None:
    monkeypatch.setattr(sandbox_host, "_is_frozen", lambda: True)
    monkeypatch.setattr(sandbox_host, "frozen_child_executable", lambda: program)


class TestTheCommand:
    def test_an_installed_package_runs_child_py_in_an_isolated_interpreter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sandbox_host, "_is_frozen", lambda: False)

        command = sandbox_host._child_command()

        assert command[1:6] == ["-I", "-S", "-B", "-X", "utf8"]
        assert command[6] == str(sandbox_host.CHILD_SCRIPT)

    def test_a_frozen_build_runs_its_script_process_program_alone(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        program = tmp_path / "pi-sandbox-child"
        program.write_bytes(b"")
        program.chmod(0o755)
        _frozen(monkeypatch, program)

        assert sandbox_host._child_command() == [str(program)]

    def test_a_build_without_the_program_says_so_and_runs_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _frozen(monkeypatch, tmp_path / "missing")

        with pytest.raises(SandboxUnavailable, match="does not contain the workflow script"):
            sandbox_host._child_command()

    @pytest.mark.skipif(not POSIX, reason="Windows has no execute bit")
    def test_a_program_that_lost_its_execute_bit_in_the_archive_gets_it_back(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        program = tmp_path / "pi-sandbox-child"
        program.write_bytes(b"")
        program.chmod(0o644)
        _frozen(monkeypatch, program)

        sandbox_host._child_command()

        assert program.stat().st_mode & stat.S_IXUSR

    @pytest.mark.skipif(
        bool(os.environ.get("PI_SANDBOX_FROZEN_CHILD")), reason="conftest points it elsewhere"
    )
    def test_the_program_sits_next_to_the_sandbox_module(self) -> None:
        program = sandbox_host.frozen_child_executable()

        assert program.parent == Path(sandbox_host.__file__).parent / "frozen-child"
        assert program.name == ("pi-sandbox-child.exe" if os.name == "nt" else "pi-sandbox-child")


@pytest.mark.skipif(not POSIX, reason="the stand-in program is a shell script")
class TestAFrozenRun:
    @pytest.fixture
    def standin(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        """A program that takes only the options and runs ``child.py`` as the real one does."""
        program = tmp_path / "pi-sandbox-child"
        program.write_text(
            "#!/bin/sh\n"
            f'exec "{sys.executable}" -I -S -B -X utf8 "{sandbox_host.CHILD_SCRIPT}" "$@"\n',
            encoding="utf-8",
        )
        program.chmod(0o755)
        _frozen(monkeypatch, program)
        return program

    async def test_the_script_runs_and_the_layers_are_the_same(self, standin: Path) -> None:
        host = FakeHost()

        out = await run('result(await agent("hi"))\nlog("done")', host=host)

        assert out.result == "answer:hi"
        assert host.logs == ["done"]
        assert {"process", "audit-hook"} <= set(out.layers)

    async def test_the_limits_reach_the_program_as_options(self, standin: Path) -> None:
        from dataclasses import replace

        from pi_dynamic_workflows.sandbox import SandboxLimits

        limits = replace(SandboxLimits(), open_files=33)
        out = await run(
            'result(_imp("resource").getrlimit(_imp("resource").RLIMIT_NOFILE)[0])',
            limits=limits,
            audit_hook=False,
        )

        assert out.result == 33
