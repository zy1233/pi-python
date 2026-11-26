"""A path that the shell reads through a mount point: ``/c/work/app`` is ``C:\\work\\app``.

Found in the VS Code extension on Windows. The Bash tool runs Git Bash, and the model writes the
project folder as that shell reads it (``cd /c/msys64/home/me/app && git log``). The rules read the
text as the host does. Windows reads ``/c/msys64/...`` as ``\\c\\msys64\\...``, a folder of the
current drive that is not there, so every path of the project looked like a path outside it, and
84 of 85 decisions of one session were asks (``path.outside_project``).

``AutoContext.shell_mounts`` says which mount points the shell has, and the rules turn a path that
starts with one into the folder of the mount. The mount here leads to a folder of the test
(``("/c", <tmp>/drive)``), so the same tests run on Windows and on Linux. ``shell_mounts()`` of
``auto_mode`` (which shell has which mount points) is tested in ``test_auto_mode_checks.py``, and
the wiring of the session in ``test_auto_wiring.py``.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from pi_agent_cli.auto_reasons import Reason
from pi_agent_cli.auto_rules import (
    AutoContext,
    By,
    Outcome,
    RealFs,
    ToolCall,
    _Env,
    _Floor,
    decide,
)


class _OfflineFs(RealFs):
    """The real file system, except that a path of two leading slashes is not looked up. Windows
    reads it as a network share and sends the name of the host to the network."""

    def realpath(self, path: str) -> str:
        if path.replace("\\", "/").startswith("//"):
            return os.path.normpath(path)
        return super().realpath(path)


@pytest.fixture
def world(tmp_path: Path) -> SimpleNamespace:
    """A drive (the folder that ``/c`` leads to) with a project, a home and another folder."""
    drive = tmp_path / "drive"
    project = drive / "work" / "app"
    (project / "src").mkdir(parents=True)
    (project / ".git").mkdir()
    (project / "src" / "a.py").write_text("x = 1\n")
    (drive / "work" / "other").mkdir()
    home = drive / "users" / "me"
    pi = home / ".pi-python"
    pi.mkdir(parents=True)
    ctx = AutoContext(
        cwd=str(project),
        home_dir=str(home),
        pi_home=str(pi),
        project_trusted=True,
        fs=_OfflineFs(),
        shell_mounts=(("/c", str(drive)),),
    )
    return SimpleNamespace(drive=drive, project=project, ctx=ctx)


def run_bash(world: SimpleNamespace, command: str, **changes):
    ctx = dataclasses.replace(world.ctx, **changes) if changes else world.ctx
    return decide(ToolCall("bash", {"command": command}, None, True), ctx)


def assert_ask(decision, reason: Reason, rule_id: str) -> None:
    assert decision.outcome is Outcome.ASK, decision
    assert decision.by is By.FLOOR
    assert decision.reason is reason, decision
    assert decision.rule_id == rule_id, decision


def assert_allow(decision) -> None:
    assert decision.outcome is Outcome.ALLOW, decision
    assert decision.rule_id == "allow.bash.read_only"


# ---------------------------------------------------------------- the folder of the project

IN_THE_PROJECT = [
    "ls /c/work/app",
    "ls /c/work/app/",
    "ls /c/work/app/src",
    "cat /c/work/app/src/a.py",
    "cd /c/work/app && ls",
    "cd /c/work/app && git status",
    "cd /c/work/app/src && cat a.py",
    "cd /c/work/app/src && cd .. && ls",
    "git -C /c/work/app status",
    "git -C /c/work/app log --oneline -3",
    "git -C /c/work/app -C src status",
    "git -C /c/work/app -C ../app status",
    "ls /c/work/./app//src",
    "ls /c/work/other/../app",
    "ls /C/work/app",
]


@pytest.mark.parametrize("command", IN_THE_PROJECT)
def test_a_path_of_the_project_through_a_mount_point_is_in_the_project(world, command: str):
    assert_allow(run_bash(world, command))


@pytest.mark.parametrize("command", IN_THE_PROJECT)
def test_without_mount_points_the_same_text_is_read_as_the_host_reads_it(world, command: str):
    """The old behaviour, and the default: a context with no mounts changes nothing."""
    decision = run_bash(world, command, shell_mounts=())

    assert_ask(decision, Reason.OUTSIDE_PROJECT, "path.outside_project")


def test_the_paths_of_write_and_edit_are_not_read_through_a_mount_point(world):
    """These tools resolve a path in their own way (the host's), so the rules must too."""
    for tool in ("write", "edit"):
        call = ToolCall(tool, {"path": "/c/work/app/src/new.py", "content": "x"}, None, True)

        assert_ask(decide(call, world.ctx), Reason.OUTSIDE_PROJECT, "path.outside_project")


# ------------------------------------------------------------------------- the floor stays

OUTSIDE = [
    "ls /c",
    "ls /c/",
    "ls /c/users/me",
    "ls /c/work/other",
    "ls /c/work",
    "cd /c/work && ls",
    "cd /c/work/other && ls",
    "git -C /c/work/other status",
    "git -C /c/work/app -C ../other status",
    "git -C /c/work -C app status",  # each -C word is also tried alone, as for a host path
    "cat /c/work/other/x.txt",
    "ls /c/work/app/../other",
    "ls /c/work/app/../..",
    "cd /c/work/app/../.. && ls",
]


@pytest.mark.parametrize("command", OUTSIDE)
def test_a_path_through_a_mount_point_that_is_outside_the_project_asks(world, command: str):
    assert_ask(run_bash(world, command), Reason.OUTSIDE_PROJECT, "path.outside_project")


@pytest.mark.parametrize(
    "command",
    [
        "ls /d/work/app",  # no mount point for ``d``: the host reads the text
        "ls /cc/work/app",  # a mount point is the whole first name
        "ls //c/work/app",  # not a mount point (a UNC path to a host called ``c``)
        "ls /tmp/c/work/app",
    ],
)
def test_text_that_is_not_a_mount_point_is_read_as_the_host_reads_it(world, command: str):
    assert_ask(run_bash(world, command), Reason.OUTSIDE_PROJECT, "path.outside_project")


@pytest.mark.parametrize(
    ("command", "reason", "rule_id"),
    [
        ("cat /c/work/app/.env", Reason.SECRETS, "path.env_file"),
        ("cat /c/work/app/.git/config", Reason.PROTECTED_PATH, "path.git_dir"),
        ("cat /c/work/app/server.pem", Reason.SECRETS, "path.secret_name"),
        ("cat /c/work/app/.ssh/id_rsa", Reason.SECRETS, "path.secret_dir"),
        ("git -C /c/work/app/.git status", Reason.PROTECTED_PATH, "path.git_dir"),
        ("cd /c/work/app/.git && ls", Reason.PROTECTED_PATH, "path.git_dir"),
        ("cat /c/work/app/*.pem", Reason.SECRETS, "path.secret_name"),
    ],
)
def test_the_protected_set_applies_to_a_path_through_a_mount_point(
    world, command: str, reason: Reason, rule_id: str
):
    assert_ask(run_bash(world, command), reason, rule_id)


@pytest.mark.parametrize(
    "command",
    [
        "ls /c/..",
        "ls /c/../c/work/app",
        "ls /c/work/app/../../../..",
        "ls /c/work/app/src/../../../../x",
        "cd /c/.. && ls",
        "git -C /c/.. status",
        "ls /c/work/app/../../../c/work/app",
    ],
)
def test_a_dotdot_above_the_mount_point_asks_because_the_rules_cannot_follow_it(
    world, command: str
):
    """The shell goes to its own root (``/``). The host would stop at the root of the drive."""
    assert_ask(run_bash(world, command), Reason.OUTSIDE_PROJECT, "path.mount_dotdot")


def test_a_dotdot_that_stays_below_the_mount_point_is_not_a_floor_hit(world):
    """It is read by the host after the join, as every other ``..`` of a path is."""
    assert_allow(run_bash(world, "ls /c/work/app/src/../../app/src"))


@pytest.mark.parametrize(
    "command",
    [
        "echo hi > /c/users/me/x.txt",
        "echo hi >> /c/work/other/x.txt",
        "cp /c/work/app/src/a.py /c/work/other/a.py",
        "cp /c/work/other/a.py /c/work/app/src/a.py",
    ],
)
def test_a_write_through_a_mount_point_outside_the_project_asks(world, command: str):
    assert_ask(run_bash(world, command), Reason.OUTSIDE_PROJECT, "path.outside_project")


def test_a_write_through_a_mount_point_inside_the_project_is_left_to_the_judge(world):
    """The floor is passed. A write is not read-only, so no rule allows it: the model decides."""
    decision = run_bash(world, "echo hi > /c/work/app/src/new.txt")

    assert decision.outcome is Outcome.JUDGE


def test_the_untrusted_project_rule_still_applies(world):
    decision = run_bash(world, "git -C /c/work/app status", project_trusted=False)

    assert_ask(decision, Reason.UNTRUSTED_PROJECT_CODE, "bash.untrusted_project_code")


def test_a_relative_path_after_a_cd_through_a_mount_point_is_tracked(world):
    assert_allow(run_bash(world, "cd /c/work/app && cd src && ls ../src"))
    assert_ask(
        run_bash(world, "cd /c/work/app && cd src && ls ../.."),
        Reason.OUTSIDE_PROJECT,
        "path.outside_project",
    )


# ----------------------------------------------------------------------- the other mounts


def test_every_mount_point_of_the_context_is_used(world):
    other = world.drive.parent / "other-drive"
    (other / "stuff").mkdir(parents=True)
    changes = {
        "extra_roots": (str(other / "stuff"),),
        "shell_mounts": (("/c", str(world.drive)), ("/d", str(other))),
    }

    assert_allow(run_bash(world, "ls /d/stuff", **changes))
    assert_allow(run_bash(world, "ls /c/work/app", **changes))
    assert_ask(run_bash(world, "ls /d", **changes), Reason.OUTSIDE_PROJECT, "path.outside_project")


@pytest.mark.parametrize("prefix", ["", "/", "c", "/c/", "/c/d", "//", "/c/."])
@pytest.mark.parametrize("command", ["ls /c/work/app", "ls //work/app", "ls /c/d/work/app"])
def test_a_mount_entry_that_is_not_a_slash_and_one_name_is_not_used(
    world, prefix: str, command: str
):
    """A malformed entry never reads a path in a new way: the text is read as the host reads it,
    and that asks. The commands are the ones that such an entry could catch: a path that starts
    with the name of the entry, and one that starts with a slash and no name."""
    decision = run_bash(world, command, shell_mounts=((prefix, str(world.drive)),))

    assert_ask(decision, Reason.OUTSIDE_PROJECT, "path.outside_project")


def test_a_mount_point_that_leads_to_a_relative_folder_makes_every_call_ask(world):
    decision = run_bash(world, "ls", shell_mounts=(("/c", "drive"),))

    assert_ask(decision, Reason.UNCLEAR, "floor.context_not_absolute")


def test_a_mount_entry_that_is_broken_is_an_internal_ask(world):
    """The rules fail closed on a context that they cannot read (invariant 9b)."""
    decision = run_bash(world, "ls", shell_mounts=(5,))

    assert_ask(decision, Reason.INTERNAL_ERROR, "pipeline.internal_error")


# ------------------------------------------------------------------------------ host_path


@pytest.fixture
def env(world: SimpleNamespace) -> _Env:
    return _Env(world.ctx)


def test_host_path_turns_a_path_in_a_mount_into_the_folder_of_the_mount(world, env: _Env):
    drive = str(world.drive)

    assert env.host_path("/c") == drive
    assert env.host_path("/c/") == drive
    assert env.host_path("/c/work") == os.path.join(drive, "work")
    assert env.host_path("/C/work") == os.path.join(drive, "work")
    assert env.host_path("/c/work/./app//src") == os.path.join(drive, "work", "app", "src")
    assert env.host_path("/c/a/../b") == os.path.join(drive, "a", "..", "b")


@pytest.mark.parametrize(
    "text",
    ["", "x", "c/work", "/d/work", "/cc/work", "//c/work", "~/x", "./c", "C:\\work", "C:/work"],
)
def test_host_path_returns_any_other_text_as_it_is(env: _Env, text: str):
    assert env.host_path(text) == text


@pytest.mark.parametrize("text", ["/c/..", "/c/a/../..", "/c/./../x", "/c/a/b/../../.."])
def test_host_path_refuses_a_dotdot_above_the_mount_point(env: _Env, text: str):
    with pytest.raises(_Floor) as hit:
        env.host_path(text)

    assert hit.value.rule_id == "path.mount_dotdot"
    assert hit.value.reason is Reason.OUTSIDE_PROJECT


def test_host_path_without_mounts_returns_every_text_as_it_is(world):
    env = _Env(dataclasses.replace(world.ctx, shell_mounts=()))

    assert env.host_path("/c/work/app") == "/c/work/app"
    assert env.host_path("/c/..") == "/c/.."
