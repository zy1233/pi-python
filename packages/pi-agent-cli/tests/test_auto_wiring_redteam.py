"""Round 2 of the red team on Auto: the wiring (PR 1b) and the rules, together.

Every test here is a call that got through, or a question that was missing, before its fix.
Each one names the finding (``W-n`` for the wiring, ``R2-n`` for the rules) in its comment.
The strings are only given to ``decide`` and to fake tools; nothing here runs a command that
could do harm, and the project is a temporary folder with placeholder files.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acp.schema import AllowedOutcome, DeniedOutcome, RequestPermissionResponse
from test_auto_wiring import (
    _calls_stream,
    _Client,
    _event,
    _fake_session,
    _FakeHarness,
    _open,
    _prompt,
    _spy_on_run,
    _tool,
    _World,
)

from pi_agent_cli.auto_mode import AutoSession
from pi_agent_cli.auto_reasons import Reason
from pi_agent_cli.auto_rules import (
    AutoContext,
    By,
    Outcome,
    ToolCall,
    decide,
    default_protected_globs,
)
from pi_agent_cli.config import AutoConfig, CliConfig
from pi_agent_cli.permissions import outcome_allows

# ----------------------------------------------------------------------------- helpers


@pytest.fixture
def world(tmp_path: Path) -> SimpleNamespace:
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / ".git").mkdir()
    (project / "src" / "a.py").write_text("x = 1\n")
    home = tmp_path / "home"
    pi = home / ".pi-python"
    pi.mkdir(parents=True)
    ctx = AutoContext(
        cwd=str(project),
        home_dir=str(home),
        pi_home=str(pi),
        project_trusted=True,
        protected_globs=default_protected_globs(),
    )
    return SimpleNamespace(project=project, home=home, pi=pi, ctx=ctx, tmp=tmp_path)


def run_bash(world: SimpleNamespace, command: str, **changes: Any):
    ctx = dataclasses.replace(world.ctx, **changes) if changes else world.ctx
    return decide(ToolCall("bash", {"command": command}, None, True), ctx)


def run_write(world: SimpleNamespace, path: str, **changes: Any):
    ctx = dataclasses.replace(world.ctx, **changes) if changes else world.ctx
    return decide(ToolCall("write", {"path": path, "content": "x"}, None, True), ctx)


def assert_ask(decision, reason: Reason | None = None, rule_id: str | None = None) -> None:
    assert decision.outcome is Outcome.ASK, decision
    assert decision.by is By.FLOOR, decision
    if reason is not None:
        assert decision.reason is reason, decision
    if rule_id is not None:
        assert decision.rule_id == rule_id, decision


# ------------------------------------------------- R2-1 (W-5): a way out of the Bash limits
#
# The Bash tool ends a command at the time limit by killing the process group of the shell.
# A job in the background of the shell is still in that group, but the tool does not wait for
# it either way; ``setsid`` and ``timeout`` put the command in a group of its own. Before the
# fix every one of these got ALLOW (they are all "read-only"), and ran on after the limit.

BACKGROUND = [
    "sleep 5 &",
    "cat src/a.py &",
    "nohup cat src/a.py &",
    "ls & ls",
    "ls &\nls",
    "ls & wait",
    "sleep 5 & disown",
    "seq 1 999999999 | sort -u > /dev/null &",
    "rg x src &> /dev/null &",
    "echo a && sleep 5 &",
    "env sleep 5 &",
]


@pytest.mark.parametrize("command", BACKGROUND)
def test_a_background_job_asks(world, command) -> None:
    assert_ask(run_bash(world, command), Reason.PERSISTENCE, "bash.persistence.background")


@pytest.mark.parametrize(
    "command",
    ["setsid sleep 5", "env setsid cat src/a.py", "nohup setsid cat src/a.py", "setsid -- cat x"],
)
def test_a_command_in_a_new_session_asks(world, command) -> None:
    assert_ask(run_bash(world, command), Reason.PERSISTENCE, "bash.persistence.setsid")


@pytest.mark.parametrize("command", ["setsid -f cat src/a.py", "setsid --fork cat src/a.py"])
def test_setsid_with_an_option_asks_too(world, command) -> None:
    assert_ask(run_bash(world, command))


@pytest.mark.parametrize(
    "command",
    [
        "timeout 100000 sleep 5",
        "timeout 301 cat src/a.py",
        "timeout 6m cat src/a.py",
        "timeout 1h cat src/a.py",
        "timeout 2d cat src/a.py",
        "timeout 0 cat src/a.py",
        "timeout 0s cat src/a.py",
        "timeout -k 400 10 cat src/a.py",
        "timeout --kill-after=400 10 cat src/a.py",
        "timeout -s KILL 99999 cat src/a.py",
        "env timeout 9999 cat src/a.py",
        "echo a | xargs timeout 9999 cat",
    ],
)
def test_a_timeout_that_outlives_the_limit_asks(world, command) -> None:
    assert_ask(run_bash(world, command), Reason.PERSISTENCE, "bash.persistence.timeout")


@pytest.mark.parametrize(
    "command",
    [
        "timeout inf cat src/a.py",
        "timeout 0x10 cat src/a.py",
        "timeout 1e9 cat src/a.py",
        "timeout -k inf 10 cat src/a.py",
        "timeout --kill-after=0x5 10 cat src/a.py",
    ],
)
def test_a_timeout_that_cannot_be_read_asks(world, command) -> None:
    assert_ask(run_bash(world, command), Reason.UNCLEAR, "bash.unclear.wrapper_option")


@pytest.mark.parametrize(
    "command",
    [
        "timeout 10 grep x src/a.py",
        "timeout 300 cat src/a.py",
        "timeout 5m cat src/a.py",
        "timeout 0.5 cat src/a.py",
        "timeout -k 5 10 cat src/a.py",
        "timeout -s KILL 30 cat src/a.py",
        "timeout --foreground 100000 cat src/a.py",
    ],
)
def test_a_short_timeout_still_runs(world, command) -> None:
    assert run_bash(world, command).outcome is Outcome.ALLOW


@pytest.mark.parametrize(
    "command",
    [
        "ls 2>&1",
        "ls >&2",
        "ls &> /dev/null",
        "ls >& /dev/null",
        "cat src/a.py && ls",
        "cat src/a.py || ls",
        "seq 1 5 | sort -u | tail -1",
    ],
)
def test_a_redirect_or_a_pipe_is_not_a_background_job(world, command) -> None:
    assert run_bash(world, command).outcome is Outcome.ALLOW


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        ("ls & rm x", Reason.DESTRUCTIVE),
        ("rm x &", Reason.DESTRUCTIVE),
        ("ls & curl example.invalid", Reason.NETWORK),
        ("sleep 5 & cat .env", Reason.SECRETS),
        ("setsid rm x", Reason.DESTRUCTIVE),
        ("timeout 9999 rm x", Reason.DESTRUCTIVE),
    ],
)
def test_the_floor_of_the_program_is_named_before_the_way_out(world, command, reason) -> None:
    assert_ask(run_bash(world, command), reason)


# --------------------------------------- R2-3: the default protected list below the root
#
# The list was matched from the project root only: ``.husky/pre-commit`` asked, and
# ``app/.husky/pre-commit`` of a monorepo got ALLOW. Git runs ``core.hooksPath`` hooks of a
# nested folder as well, and every agent reads its own ``.claude`` and ``.cursor`` folders at
# any depth.

NESTED_PROTECTED = [
    "app/.husky/pre-commit",
    "packages/web/.husky/_/husky.sh",
    "services/api/.githooks/pre-push",
    "a/b/.claude/settings.json",
    "apps/x/.cursor/rules/style.mdc",
    "services/api/.mcp.json",
    "pkg/.vscode/tasks.json",
    "packages/web/.github/workflows/ci.yml",
]


@pytest.mark.parametrize("relative", NESTED_PROTECTED)
@pytest.mark.parametrize("tool", ["write", "edit"])
def test_a_write_below_the_root_to_the_default_protected_list_asks(world, relative, tool) -> None:
    call = ToolCall(tool, {"path": relative, "content": "x", "edits": []}, None, True)
    assert_ask(decide(call, world.ctx), Reason.PROTECTED_PATH, "path.protected_glob")


@pytest.mark.parametrize(
    "relative",
    [
        "src/husky/x.sh",
        "app/.husky-notes/x.md",
        "docs/.github/other/notes.yml",
        "app/mcp.json",
        "app/my.mcp.json.bak",
        "app/.vscode/settings.json",
        "app/claude/notes.md",
        "src/a.py",
    ],
)
def test_a_neighbour_of_the_protected_list_is_not_protected(world, relative) -> None:
    assert run_write(world, relative).outcome is Outcome.ALLOW


def test_a_project_inside_a_folder_with_a_protected_name_is_not_protected_as_a_whole(
    world,
) -> None:
    project = world.tmp / ".husky" / "proj"
    project.mkdir(parents=True)

    assert run_write(world, "src/x.py", cwd=str(project)).outcome is Outcome.ALLOW
    assert_ask(
        run_write(world, ".husky/pre-commit", cwd=str(project)),
        Reason.PROTECTED_PATH,
        "path.protected_glob",
    )


def test_a_glob_of_the_user_that_starts_with_two_stars_reaches_every_depth(world) -> None:
    ctx = {"protected_globs": ("**/secrets/*", "**/deploy.sh")}
    for relative in ("secrets/key", "a/secrets/key", "a/b/secrets/key", "deploy.sh", "a/deploy.sh"):
        assert_ask(run_write(world, relative, **ctx), Reason.PROTECTED_PATH)
    assert run_write(world, "src/a.py", **ctx).outcome is Outcome.ALLOW


# -------------------------------------- R2-4: the project is the home folder (or above it)
#
# When the project is the home folder, a new file in ``~/.local/bin`` or ``~/.vimrc`` is
# "inside the project". The shell, an editor or the system runs it later, with no Auto.

HOME_PERSISTENCE = [
    ".local/bin/git",
    ".local/share/applications/x.desktop",
    ".vimrc",
    ".xprofile",
    ".xinitrc",
    ".claude.json",
    ".npmrc",
    ".emacs.d/init.el",
    ".cargo/bin/x",
    ".config/autostart/x.desktop",
    "bin/ls",
    "go/bin/x",
    "Library/LaunchAgents/x.plist",
    "Applications/X.command",
    "AppData/Roaming/x",
]


@pytest.fixture
def home_world(tmp_path: Path) -> SimpleNamespace:
    home = tmp_path / "home"
    pi = home / ".pi-python"
    pi.mkdir(parents=True)
    (home / "notes").mkdir()
    ctx = AutoContext(
        cwd=str(home),
        home_dir=str(home),
        pi_home=str(pi),
        project_trusted=True,
        protected_globs=default_protected_globs(),
    )
    return SimpleNamespace(home=home, ctx=ctx, tmp=tmp_path)


@pytest.mark.parametrize("relative", HOME_PERSISTENCE)
@pytest.mark.parametrize("tool", ["write", "edit"])
def test_a_write_that_the_system_runs_later_asks_when_the_project_is_the_home_folder(
    home_world, relative, tool
) -> None:
    call = ToolCall(tool, {"path": relative, "content": "x", "edits": []}, None, True)
    decision = decide(call, home_world.ctx)
    assert decision.outcome is Outcome.ASK, decision
    assert decision.by is By.FLOOR


@pytest.mark.parametrize(
    "relative", [".vimrc", ".xprofile", ".local/bin/git", "bin/ls", "Library/LaunchAgents/x.plist"]
)
def test_those_writes_are_named_persistence(home_world, relative) -> None:
    decision = decide(
        ToolCall("write", {"path": relative, "content": "x"}, None, True), home_world.ctx
    )
    assert_ask(decision, Reason.PERSISTENCE, "path.home_persistence")


@pytest.mark.parametrize(
    "relative", ["notes/todo.txt", "Documents/plan.md", "src/a.py", "binder/x", "Libraries/x"]
)
def test_an_ordinary_file_in_the_home_folder_still_runs(home_world, relative) -> None:
    decision = decide(
        ToolCall("write", {"path": relative, "content": "x"}, None, True), home_world.ctx
    )
    assert decision.outcome is Outcome.ALLOW, decision


@pytest.mark.parametrize(
    ("project", "relative"),
    [
        ("work/app", ".hidden/x"),
        ("work/app", ".config/x"),
        ("work/app", "bin/tool"),
        ("work/app", ".local/bin/tool"),
        ("work/app", "Library/x"),
        (".dotfiles", "vimrc"),
        (".dotfiles", ".vimrc"),
        ("bin/tools", "run.sh"),
        ("Library/app", "x.txt"),
    ],
)
def test_a_project_below_the_home_folder_keeps_its_own_hidden_and_bin_folders(
    tmp_path, project, relative
) -> None:
    """The rule is for a project that is the home folder. A dotfiles repository (``~/.dotfiles``)
    or a project that lives in ``~/bin`` is a project like any other."""
    home = tmp_path / "home"
    root = home / project
    pi = home / ".pi-python"
    root.mkdir(parents=True)
    pi.mkdir(parents=True)
    ctx = AutoContext(
        cwd=str(root),
        home_dir=str(home),
        pi_home=str(pi),
        project_trusted=True,
        protected_globs=default_protected_globs(),
    )

    decision = decide(ToolCall("write", {"path": relative, "content": "x"}, None, True), ctx)

    assert decision.outcome is Outcome.ALLOW, decision


def test_the_home_rule_covers_a_folder_that_the_user_added_as_a_root(world) -> None:
    ctx = {"extra_roots": (str(world.home),)}
    assert_ask(
        run_write(world, str(world.home / ".local" / "bin" / "x"), **ctx),
        Reason.PERSISTENCE,
        "path.home_persistence",
    )
    assert run_write(world, str(world.home / "notes" / "x.txt"), **ctx).outcome is Outcome.ALLOW


# ------------------------------------------ W-1: two calls of one message with one id
#
# The id of a tool call comes from the model. ``_open_calls`` was a set of ids, so a second
# call with the same id looked like "the same call": a batch that is prepared while Auto is
# switched on ran both calls together, with one question for the two of them.


class _SwitchingClient(_Client):
    """Allows every question; at the first one it puts the session in Auto."""

    def __init__(self) -> None:
        super().__init__(allow=True)
        self.agent: Any = None
        self.switched = False

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        self.requests.append(tool_call)
        if not self.switched:
            self.switched = True
            await self.agent.set_config_option(
                config_id="mode", session_id=session_id, value="auto"
            )
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id="allow-once")
        )


@pytest.mark.parametrize("ids", [("c1", "c2"), ("dup", "dup")])
async def test_a_batch_that_meets_auto_half_way_asks_for_the_second_call(
    tmp_path: Path, ids: tuple[str, str]
) -> None:
    world = _World(tmp_path)
    (world.project / "a.txt").write_text("a\n")
    (world.project / "b.txt").write_text("b\n")
    stream = _calls_stream(
        (ids[0], "bash", {"command": "cat a.txt"}),
        (ids[1], "bash", {"command": "cat b.txt"}),
    )
    client = _SwitchingClient()
    agent, client, session_id = await _open(world, stream, client=client, permission="ask")
    client.agent = agent
    log: list[str] = []
    _spy_on_run(agent._harnesses[session_id].get_tool("bash"), "bash", log)

    await _prompt(agent, session_id)

    # One question from the mode "ask" (the first call), one from Auto (the second call, which
    # came while the first was still open): the same for two ids and for one id twice.
    assert len(client.requests) == 2, client.titles()
    assert "Auto:" in client.titles()[1]
    assert log.count("run:bash:start") == 2


async def test_two_calls_with_one_id_that_end_one_after_the_other_are_not_counted_open(
    tmp_path: Path,
) -> None:
    world = _World(tmp_path)
    (world.project / "a.txt").write_text("a\n")
    stream = _calls_stream(
        ("dup", "bash", {"command": "cat a.txt"}),
        ("dup", "bash", {"command": "cat a.txt"}),
    )
    agent, client, session_id = await _open(world, stream, client=_Client(allow=False))
    log: list[str] = []
    _spy_on_run(agent._harnesses[session_id].get_tool("bash"), "bash", log)

    await _prompt(agent, session_id)

    assert client.requests == []
    assert log == ["run:bash:start", "run:bash:end", "run:bash:start", "run:bash:end"]


async def test_one_id_started_twice_is_open_until_it_has_ended_twice(tmp_path: Path) -> None:
    harness, session = _fake_session(_World(tmp_path))
    await session.enter()
    harness.emit("tool_execution_start", "dup")
    harness.emit("tool_execution_start", "dup")
    call = _event("write", {"path": "b.txt", "content": ""}, "dup")

    assert (await session.decide(call)).rule_id == "session.parallel_batch"
    harness.emit("tool_execution_end", "dup")
    assert (await session.decide(call)).outcome is Outcome.ALLOW  # one is left: this call
    harness.emit("tool_execution_end", "dup")
    assert (await session.decide(call)).outcome is Outcome.ALLOW


async def test_an_end_that_has_no_start_is_not_taken_from_the_next_start(tmp_path: Path) -> None:
    """A count that went below zero would hide the next call with that id."""
    harness, session = _fake_session(_World(tmp_path))
    await session.enter()
    harness.emit("tool_execution_end", "a")
    harness.emit("tool_execution_start", "a")

    other = await session.decide(_event("write", {"path": "b.txt", "content": ""}, "b"))

    assert other.rule_id == "session.parallel_batch"


# ------------------------------------- W-2: a value of [auto] that the agent reads later
#
# The agent reads ``agent.toml`` again when it could not run a turn (``--setup`` while it was
# up). The values of ``[auto]`` that Auto cannot work with were checked once, when Auto
# started, so a new file could bring one and Auto would go on and answer yes.


def _session_with_a_config_that_changes(world: _World):
    harness = _FakeHarness(world.project, [_tool("write")])
    current = {"config": CliConfig(permission="auto", auto=AutoConfig())}
    session = AutoSession(
        harness=harness,
        get_config=lambda: current["config"],
        home=world.home,
        trusted=lambda: False,
        environ={"PATH": os.pathsep.join(["/usr/bin", "/bin"])},
        resolve_shell=lambda _path: SimpleNamespace(shell="/bin/bash"),
    )
    return current, session


async def test_a_value_of_auto_that_a_new_configuration_brings_makes_every_call_ask(
    tmp_path: Path,
) -> None:
    current, session = _session_with_a_config_that_changes(_World(tmp_path))
    assert await session.enter() == ()
    write = {"path": "b.txt", "content": ""}
    assert (await session.decide(_event("write", write, "c1"))).outcome is Outcome.ALLOW

    problem = "An entry of protected in [auto] is not text."
    current["config"] = CliConfig(permission="auto", auto=AutoConfig(problems=(problem,)))
    later = await session.decide(_event("write", write, "c2"))

    assert later.outcome is Outcome.ASK
    assert later.rule_id == "session.check_failed"
    assert problem in session.problems


async def test_a_value_of_auto_that_is_mended_by_a_new_configuration_asks_no_more(
    tmp_path: Path,
) -> None:
    problem = "An entry of protected in [auto] is not text."
    current, session = _session_with_a_config_that_changes(_World(tmp_path))
    current["config"] = CliConfig(permission="auto", auto=AutoConfig(problems=(problem,)))
    assert await session.enter() == (problem,)
    write = {"path": "b.txt", "content": ""}
    assert (await session.decide(_event("write", write, "c1"))).outcome is Outcome.ASK

    current["config"] = CliConfig(permission="auto", auto=AutoConfig())

    assert session.problems == ()
    assert (await session.decide(_event("write", write, "c2"))).outcome is Outcome.ALLOW


# ------------------------------------------------ W-5 through the wiring: a job that stays


async def test_a_background_job_asks_through_the_agent_and_nothing_runs(tmp_path: Path) -> None:
    world = _World(tmp_path)
    marker = world.project / "marker.txt"
    stream = _calls_stream(("c1", "bash", {"command": f"sleep 30 & touch {marker}"}))
    agent, client, session_id = await _open(world, stream, client=_Client(allow=False))

    await asyncio.wait_for(_prompt(agent, session_id), timeout=15)

    assert len(client.requests) == 1
    assert not marker.exists()


async def test_a_protected_file_below_the_root_asks_through_the_agent(tmp_path: Path) -> None:
    world = _World(tmp_path)
    target = world.project / "app" / ".husky" / "pre-commit"
    stream = _calls_stream(("c1", "write", {"path": str(target), "content": "x\n"}))
    agent, client, session_id = await _open(world, stream, client=_Client(allow=False))

    await _prompt(agent, session_id)

    assert len(client.requests) == 1
    assert not target.exists()


async def test_the_default_list_reaches_the_rules_with_two_stars_in_front(tmp_path: Path) -> None:
    world = _World(tmp_path)
    agent, _, session_id = await _open(world, auto=AutoConfig(protected=("secrets/*",)))
    ctx = agent._autos[session_id]._make_context()

    assert ctx.protected_globs[-1] == "secrets/*"
    assert all(glob.startswith("**/") for glob in ctx.protected_globs[:-1])
    assert len(ctx.protected_globs) == len(default_protected_globs()) + 1


# ------------------------------------------- W-9: the answer to a question of Auto
#
# Auto asks, and the call runs when ``outcome_allows`` reads the answer as yes. It read an
# answer of a shape that it did not know, and an option id that was empty, as yes: the last
# line of the reader was ``not option_id.startswith("reject")`` for anything. The reader is not
# strict: the pager sends ids of its own for yes. But an odd answer is no (invariant 9b).

NOT_A_CLEAR_YES: list[Any] = [
    None,
    "allow-once",
    42,
    object(),
    {},
    {"optionId": "allow-once"},
    {"outcome": "cancelled", "optionId": "allow-once"},
    {"outcome": "selected"},
    {"outcome": "selected", "optionId": ""},
    {"outcome": "selected", "optionId": "   "},
    {"outcome": "selected", "optionId": None},
    {"outcome": "selected", "optionId": 1},
    {"outcome": "selected", "optionId": ["allow-once"]},
    {"outcome": "selected", "optionId": "reject-once"},
    {"outcome": "selected", "option_id": "reject-once"},
    {"outcome": "selected", "optionId": "REJECT-ONCE"},
    {"outcome": "selected", "optionId": " reject-once"},
    SimpleNamespace(outcome="selected", option_id=""),
    SimpleNamespace(outcome="selected", option_id=None),
    SimpleNamespace(outcome="cancelled", option_id="allow-once"),
    SimpleNamespace(option_id="allow-once"),
    AllowedOutcome(outcome="selected", option_id=""),
    AllowedOutcome(outcome="selected", option_id="reject-once"),
    AllowedOutcome(outcome="selected", option_id="Reject-Once"),
    AllowedOutcome.model_construct(outcome="cancelled", option_id="allow-once"),
    DeniedOutcome(outcome="cancelled"),
]


@pytest.mark.parametrize("outcome", NOT_A_CLEAR_YES, ids=repr)
def test_an_answer_that_is_not_a_clear_yes_is_no(outcome: Any) -> None:
    assert outcome_allows(outcome) is False


@pytest.mark.parametrize(
    "option_id", ["allow-once", "enable-always-approve", "allow-edits-session"]
)
def test_the_ids_that_a_client_sends_for_yes_are_still_yes(option_id: str) -> None:
    assert outcome_allows(AllowedOutcome(outcome="selected", option_id=option_id)) is True
    assert outcome_allows({"outcome": "selected", "optionId": option_id}) is True
    assert outcome_allows({"outcome": "selected", "option_id": option_id}) is True
    assert outcome_allows(SimpleNamespace(outcome="selected", option_id=option_id)) is True


class _EmptyIdClient(_Client):
    """Answers every question with an option that has no id."""

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        self.requests.append(tool_call)
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=""))


async def test_a_question_that_gets_an_answer_without_an_id_does_not_run_the_call(
    tmp_path: Path,
) -> None:
    world = _World(tmp_path)
    target = world.project / "new.txt"
    stream = _calls_stream(("c1", "bash", {"command": f"echo x > {target}"}))
    agent, client, session_id = await _open(world, stream, client=_EmptyIdClient())

    await _prompt(agent, session_id)

    assert len(client.requests) == 1
    assert not target.exists()
