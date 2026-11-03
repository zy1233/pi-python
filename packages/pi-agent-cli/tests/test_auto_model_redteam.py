"""Round 3 of the red team: the model step of Auto (pull request #95).

Every test here failed on the head of that pull request. The strings are given to ``decide()``
and to the session with a scripted model. No command is run.

* M-01: a read-only program that runs behind ``xargs`` or ``find -exec`` is not proven read-only
  (it has unknown arguments), and the form that it shows (D-07) must still ask by the floor
  with no model.
* M-02: ``find`` may run one command for each of its ``-exec`` options. The floor reads each one.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_auto_classifier import Script
from test_auto_model_step import _bash, _session
from test_auto_wiring import _World

from pi_agent_cli.auto_reasons import Reason
from pi_agent_cli.auto_rules import (
    AutoContext,
    By,
    Outcome,
    ToolCall,
    decide,
    decide_rules_only,
)


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


@pytest.fixture
def rules(tmp_path: Path) -> SimpleNamespace:
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / ".git").mkdir()
    home = tmp_path / "home"
    pi = home / ".pi-python"
    pi.mkdir(parents=True)
    ctx = AutoContext(cwd=str(project), home_dir=str(home), pi_home=str(pi), project_trusted=True)
    return SimpleNamespace(ctx=ctx)


def _decide(rules: SimpleNamespace, command: str, **changes):
    ctx = dataclasses.replace(rules.ctx, **changes) if changes else rules.ctx
    return decide(ToolCall("bash", {"command": command}, None, True), ctx)


# M-01. The same forms as ``BARRED`` in ``test_auto_rules.py``, behind a program that gives the
# command unknown arguments, and the ``find`` forms that were not read (M-02).
DYNAMIC_BARRED = [
    ("ls | xargs rg --pre sh foo", "option"),
    ("xargs sort -o out.txt", "option"),
    ("xargs -n 1 sort --compress-program=sh", "option"),
    ("xargs wc --files0-from=names.txt", "option"),
    ("env xargs rg --pre sh foo", "option"),
    ("ls | xargs xargs sort -o out.txt", "option"),
    ("find . -name '*.txt' -exec sort -o out.txt {} \\;", "option"),
    ("find . -execdir rg --pre sh foo {} +", "option"),
    ("find . -ok tail -f app.log \\;", "option"),
    ("ls | xargs rg foo *", "lead_glob"),
    ("find . -exec rg foo * \\;", "lead_glob"),
    ("ls | xargs uniq a b", "operand"),
    ("find . -exec hostname newname \\;", "operand"),
    ("find . -exec date 010100001999 \\;", "operand"),
    # ``find`` itself: an option of its own that writes a file, or a glob that may be an option,
    # before or after a command that it runs. A ``+`` that does not follow ``{}`` is an
    # argument of the command, so ``-o`` below belongs to ``sort``.
    ("find . -fprint out.txt -exec cat {} \\;", "option"),
    ("find . -exec cat {} \\; -fprint out.txt", "option"),
    ("find . -exec cat {} + -fls out.txt", "option"),
    ("find * -exec cat {} \\;", "lead_glob"),
    ("find . -name *.py -exec cat {} \\;", "lead_glob"),
    ("find . -exec sort + -o out.txt {} \\;", "option"),
]

# M-02. A command in a later ``-exec`` clause of ``find`` is a command like any other: the
# floor of its program asks. The first clause is harmless on purpose.
FIND_LATER_CLAUSE = [
    ("find . -exec cat {} \\; -exec rm {} \\;", "bash.destructive.program"),
    ("find . -exec cat {} + -exec rm {} \\;", "bash.destructive.program"),
    ("find . -exec cat {} \\; -execdir rm {} +", "bash.destructive.program"),
    ("find . -exec cat {} \\; -ok rm {} \\;", "bash.destructive.program"),
    ("find . -exec cat {} \\; -okdir rm {} \\;", "bash.destructive.program"),
    ("find . -exec cat {} \\; -name x -exec rm {} \\;", "bash.destructive.program"),
    ("find . -exec cat {} \\; -exec cat {} \\; -exec rm {} \\;", "bash.destructive.program"),
    ("find . -exec cat {} \\; -exec env rm {} \\;", "bash.destructive.program"),
    ("find . -exec cat {} \\; -exec xargs rm \\;", "bash.destructive.program"),
    ("find . -exec cat {} \\; -exec kill -9 1 \\;", "bash.destructive.program"),
    ("find . -exec cat {} \\; -exec curl https://x.invalid/u {} \\;", "bash.network.program"),
    ("find . -exec cat {} \\; -exec git push \\;", "bash.network.git"),
    ("find . -exec cat {} \\; -exec pip install x \\;", "bash.network.package_manager"),
    ("find . -exec cat {} \\; -exec sudo ls {} \\;", "bash.privilege"),
    ("find . -exec cat {} \\; -exec sh -c x \\;", "bash.hidden.shell_inline"),
    ("find . -exec cat {} \\; -exec python -c x \\;", "bash.hidden.python_inline"),
    ("find . -exec cat {} \\; -exec eval x \\;", "bash.hidden.program"),
    ("find . -exec cat {} \\; -exec printenv {} \\;", "bash.secrets.program"),
]


@pytest.mark.parametrize(("command", "kind"), DYNAMIC_BARRED)
def test_a_barred_form_behind_xargs_or_find_is_asked_by_the_floor(rules, command, kind) -> None:
    decision = _decide(rules, command)

    assert decision.outcome is Outcome.ASK, decision
    assert decision.by is By.FLOOR
    assert decision.reason is Reason.UNCLEAR
    assert decision.rule_id == f"bash.barred.{kind}"


@pytest.mark.parametrize(("command", "rule_id"), FIND_LATER_CLAUSE)
def test_a_later_exec_clause_of_find_is_read_by_the_floor(rules, command, rule_id) -> None:
    decision = _decide(rules, command)

    assert decision.outcome is Outcome.ASK, decision
    assert decision.by is By.FLOOR
    assert decision.rule_id == rule_id


@pytest.mark.parametrize(
    "command",
    [
        "find . -name '*.py' -exec cat {} \\;",
        "find . -name '*.py' -exec cat {} +",
        "find . -exec cat {} \\; -exec wc -l {} \\;",
        "find . -exec echo + -exec rm {} \\;",
        "ls | xargs cat",
        "ls | xargs rg foo",
        "ls | xargs wc -l",
    ],
)
def test_a_harmless_command_with_unknown_arguments_is_still_left_to_the_model(
    rules, command
) -> None:
    decision = _decide(rules, command)

    assert decision.outcome is Outcome.JUDGE, decision
    assert decision.rule_id == "bash.not_proven"


@pytest.mark.parametrize("command", ["find . -name x", "find . -name '*.py' -print"])
def test_find_with_no_command_to_run_is_still_proven_read_only(rules, command) -> None:
    assert _decide(rules, command).outcome is Outcome.ALLOW


@pytest.mark.parametrize(("command", "_"), [*DYNAMIC_BARRED, *FIND_LATER_CLAUSE])
def test_these_calls_are_also_asked_when_only_the_rules_answer(rules, command, _) -> None:
    call = ToolCall("bash", {"command": command}, None, True)

    assert decide_rules_only(call, rules.ctx) == decide(call, rules.ctx)


# The step in a session: a model that says ALLOW to everything is never asked, so it cannot
# allow. This is the property that the red-team report of the rule layer asks for (D-07), and
# that the design states for the floor.
@pytest.mark.parametrize("command", [c for c, _ in [*DYNAMIC_BARRED, *FIND_LATER_CLAUSE]])
async def test_a_model_that_allows_everything_is_not_asked_about_these_calls(
    world: _World, command: str
) -> None:
    script = Script("ALLOW")
    _, session, _ = _session(world, script)
    await session.enter()

    verdict = await session.decide(_bash(command))

    assert verdict.outcome is Outcome.ASK
    assert verdict.by == "floor"
    assert verdict.classifier == "rules"
    assert script.calls == []
