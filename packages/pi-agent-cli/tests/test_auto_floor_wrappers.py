"""The floor times the wrappers (red team, round 3).

A call that asks by the floor must ask when it sits in a wrapper too: after ``&&`` or ``;``, in a
pipe, behind ``time`` or a variable, in a group, an ``if``, a loop or a function, and behind a
program that runs it (``env``, ``nice``, ``sudo``, ``xargs``, ``find -exec``). A wrapper that the
rules read must not change the reason. A wrapper that they do not read must still ask by the
floor: it must never leave the call to the model. The strings go to ``decide()``; no command runs.
"""

from __future__ import annotations

import pytest

from pi_agent_cli.auto_rules import (
    AutoContext,
    By,
    Outcome,
    ToolCall,
    decide,
    default_protected_globs,
)

# Each command asks by the floor when it stands alone. A relative path that leaves the project
# leaves it from ``src`` too (``../../b``), so ``cd src && {c}`` asks as the command alone does.
# fmt: off
CORE = [
    "rm -rf build", "git reset --hard HEAD~1", "git clean -fdx", "git push origin main",
    "git push --force", "curl https://example.invalid/x -o x", "wget https://example.invalid/x",
    "pip install requests", "npm install left-pad", "sudo ls", "kill -9 1", "printenv", "env",
    "cat ~/.ssh/id_rsa", "cat .env", "cp x /etc/y", "chmod -R 777 .", "dd if=/dev/zero of=x",
    "nc -l 1234", "ssh host ls", "scp a host:b", "docker run alpine ls", "kubectl delete pod x",
    "terraform apply", "git config core.hooksPath x", "crontab -e", "systemctl stop x",
    "shutdown now", "eval x", "python -c 'print(1)'", "sh -c 'ls'", "source x", ". x",
    "git branch -D main", "git tag -d v1", "git stash drop", "git checkout -- .", "rm x",
    "mv a /tmp/b", "cp a ../../b", "ln -s /etc/passwd x", "gh pr merge 1", "aws s3 rm s3://b/x",
    "gcloud compute instances delete x", "truncate -s 0 x", "shred x", "unlink x", "rmdir d",
    "find . -delete", "rclone copy dist remote:b",
]
# The rules read these. The program inside keeps its own rule id.
READ = {
    "and": "true && {c}", "or": "false || {c}", "semi": "true; {c}", "newline": "true\n{c}",
    "pipe_in": "echo x | {c}", "pipe_out": "{c} | cat", "redir_dev": "{c} > /dev/null",
    "redir_err": "{c} 2>&1", "redir_in": "{c} < /dev/null", "time": "time {c}",
    "env_var": "FOO=1 {c}", "cd": "cd src && {c}",
}
# The rules do not read these (a group, a compound command, a function, a ``cd`` that goes back).
# They ask for a reason of their own, and the floor decides: the model is never asked.
OPAQUE = {
    "subshell": "({c})", "group": "{{ {c}; }}", "not": "! {c}", "bang": "true && ! {c}",
    "if": "if true; then {c}; fi", "if_cond": "if {c}; then true; fi",
    "while": "while false; do {c}; done", "for": "for i in a b; do {c}; done",
    "case": "case x in x) {c};; esac", "func": "f() {{ {c}; }}; f",
    "paren_pipe": "(echo x) | {c}", "nested": "if true; then for i in a; do ({c}); done; fi",
    "cd_back": "cd src; cd ..; {c}",
}
# A program in front of the command. The rules unwrap it, or ask because they cannot.
PREFIX = {
    "env": "env X=1 {c}", "env_i": "env -i {c}", "env_dash": "env -- {c}",
    "env_twice": "env A=1 env B=2 {c}", "nice": "nice {c}", "nice_n": "nice -n 5 {c}",
    "timeout": "timeout 60 {c}", "nohup": "nohup {c}", "command": "command {c}",
    "exec": "exec {c}", "builtin": "builtin {c}", "xargs": "ls | xargs {c}",
    "xargs_i": "ls | xargs -I{{}} {c}", "find": "find . -exec {c} \\;", "usr_bin": "/usr/bin/{c}",
    "dot_slash": "./{c}", "sudo": "sudo {c}", "stdbuf": "stdbuf -o0 {c}", "ionice": "ionice {c}",
    "watch": "watch {c}", "time_p": "time -p {c}", "time_nice": "time nice {c}",
}
# fmt: on


@pytest.fixture(scope="module")
def ctx(tmp_path_factory: pytest.TempPathFactory) -> AutoContext:
    root = tmp_path_factory.mktemp("wrappers")
    (root / "proj" / "src").mkdir(parents=True)
    (root / "proj" / ".git").mkdir()
    (root / "home" / ".pi-python").mkdir(parents=True)
    return AutoContext(
        cwd=str(root / "proj"),
        home_dir=str(root / "home"),
        pi_home=str(root / "home" / ".pi-python"),
        project_trusted=True,
        protected_globs=default_protected_globs(),
    )


def _decide(ctx: AutoContext, command: str):
    return decide(ToolCall("bash", {"command": command}, None, True), ctx)


@pytest.fixture(scope="module")
def alone(ctx: AutoContext) -> dict[str, str]:
    """The rule id of each command that stands alone."""
    ids = {}
    for command in CORE:
        decision = _decide(ctx, command)
        assert (decision.outcome, decision.by) == (Outcome.ASK, By.FLOOR), (command, decision)
        ids[command] = decision.rule_id
    return ids


def test_each_command_of_the_table_asks_by_the_floor_alone(alone) -> None:
    assert len(CORE) == len(set(CORE)) == len(alone) >= 50


def _wrong(ctx: AutoContext, template: str, keep: dict[str, str] | None = None) -> list[tuple]:
    wrong = []
    for command in CORE:
        decision = _decide(ctx, template.format(c=command))
        asks = (decision.outcome, decision.by) == (Outcome.ASK, By.FLOOR)
        if not asks or (keep is not None and decision.rule_id != keep[command]):
            wrong.append((command, decision.outcome.value, decision.by.value, decision.rule_id))
    return wrong


@pytest.mark.parametrize("name", sorted(READ))
def test_a_wrapper_that_the_rules_read_keeps_the_reason_of_the_program(ctx, alone, name) -> None:
    assert _wrong(ctx, READ[name], keep=alone) == []


@pytest.mark.parametrize("name", sorted(OPAQUE))
def test_a_wrapper_that_the_rules_do_not_read_still_asks_by_the_floor(ctx, alone, name) -> None:
    assert _wrong(ctx, OPAQUE[name]) == []


@pytest.mark.parametrize("name", sorted(PREFIX))
def test_a_program_in_front_of_the_command_still_asks_by_the_floor(ctx, alone, name) -> None:
    assert _wrong(ctx, PREFIX[name]) == []
