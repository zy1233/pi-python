"""The checks and the texts of ``auto_mode`` that need no session.

The checks are what the rules cannot do themselves (red-team report, W-2): a ``PATH`` with an
entry that is not absolute, a Bash tool that does not run bash, a context with a folder that is
not absolute or not there. The texts are what the user reads: the entry message, the problems,
the log.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from pi_agent_cli.auto_mode import (
    ENTRY_MESSAGE,
    BashLimitRegistry,
    config_notes_message,
    context_problems,
    entry_message,
    format_log,
    path_problems,
    problems_message,
    shell_problems,
)
from pi_agent_cli.auto_rules import AutoContext
from pi_agent_core.coding_tools.bash import BashLimits


def _path(*entries: str) -> str:
    return os.pathsep.join(entries)


# ----------------------------------------------------------------------------- PATH


def test_a_path_of_absolute_entries_is_fine(tmp_path: Path):
    assert path_problems(_path(str(tmp_path), str(tmp_path / "bin"))) == []


def test_a_path_that_is_not_set_is_the_default_of_the_shell_and_is_fine():
    assert path_problems(None) == []


@pytest.mark.parametrize(
    "entries",
    [
        ("{abs}", ""),  # an empty entry means the current folder
        ("", "{abs}"),
        ("{abs}", "."),
        (".", "{abs}"),
        ("{abs}", "bin"),
        ("{abs}", "./bin"),
        ("{abs}", "../bin"),
        ("{abs}", "~/bin"),  # the shell expands it, a rule reads it as a name
        ("",),  # an empty PATH is the current folder too
    ],
    ids=[
        "empty-last",
        "empty-first",
        "dot-last",
        "dot-first",
        "name",
        "dot-slash",
        "up",
        "tilde",
        "all-empty",
    ],
)
def test_a_path_with_an_entry_that_is_not_absolute_is_a_problem(tmp_path: Path, entries):
    path = _path(*(entry.replace("{abs}", str(tmp_path)) for entry in entries))

    (problem,) = path_problems(path)

    assert "PATH" in problem


# ---------------------------------------------------------------------------- shell


def _shell(path: str):
    return lambda _shell_path: SimpleNamespace(shell=path)


@pytest.mark.parametrize(
    "shell",
    ["/bin/bash", "/usr/bin/bash", "bash", r"C:\Program Files\Git\bin\bash.exe", "/opt/BASH"],
)
def test_a_bash_tool_that_runs_bash_is_fine(shell: str):
    assert shell_problems(_shell(shell)) == []


@pytest.mark.parametrize(
    ("shell", "name"),
    [
        ("/bin/sh", "sh"),
        ("/usr/bin/zsh", "zsh"),
        (r"C:\Windows\System32\cmd.exe", "cmd"),
        ("/usr/bin/dash", "dash"),
        ("", "an unnamed shell"),
    ],
)
def test_a_bash_tool_that_runs_another_shell_is_a_problem(shell: str, name: str):
    (problem,) = shell_problems(_shell(shell))

    assert name in problem


def test_a_bash_tool_that_cannot_find_a_shell_is_a_problem():
    def none_found(_shell_path):
        raise OSError("no shell")

    assert shell_problems(none_found) != []


# -------------------------------------------------------------------------- context


def _context(tmp_path: Path, **changes) -> AutoContext:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    fields = {
        "cwd": str(project),
        "home_dir": str(tmp_path / "home"),
        "pi_home": str(tmp_path / "pi"),
        "project_trusted": False,
    }
    fields.update(changes)
    return AutoContext(**fields)


def test_a_context_that_is_in_order_has_no_problem(tmp_path: Path):
    context = _context(
        tmp_path,
        extra_roots=(str(tmp_path / "more"),),
        protected_paths=(str(tmp_path / "project" / "x"),),
    )

    assert context_problems(context) == []


def test_a_project_folder_that_is_not_there_is_a_problem(tmp_path: Path):
    (problem,) = context_problems(_context(tmp_path, cwd=str(tmp_path / "gone")))

    assert "does not exist" in problem


def test_a_project_folder_that_is_a_file_is_a_problem(tmp_path: Path):
    a_file = tmp_path / "file"
    a_file.write_text("x", encoding="utf-8")

    (problem,) = context_problems(_context(tmp_path, cwd=str(a_file)))

    assert "does not exist" in problem


@pytest.mark.parametrize("cwd", ["", ".", "relative/project"])
def test_a_project_folder_that_is_not_absolute_is_a_problem(tmp_path: Path, cwd: str):
    (problem,) = context_problems(_context(tmp_path, cwd=cwd))

    assert "not an absolute path" in problem


@pytest.mark.parametrize("home", ["", "~", "home"])
def test_a_home_folder_that_is_not_absolute_is_a_problem(tmp_path: Path, home: str):
    (problem,) = context_problems(_context(tmp_path, home_dir=home))

    assert "home folder" in problem


def test_an_agent_home_that_is_not_absolute_is_a_problem(tmp_path: Path):
    (problem,) = context_problems(_context(tmp_path, pi_home="relative"))

    assert "PI_HOME" in problem


def test_an_extra_root_that_is_not_absolute_is_named(tmp_path: Path):
    (problem,) = context_problems(_context(tmp_path, extra_roots=("relative/dir",)))

    assert "relative/dir" in problem
    assert "extra_roots" in problem


def test_a_protected_path_that_is_not_absolute_is_a_problem(tmp_path: Path):
    (problem,) = context_problems(_context(tmp_path, protected_paths=("secret.txt",)))

    assert "protected" in problem


# --------------------------------------------------------------------------- texts


def test_the_entry_message_says_what_auto_does_not_guard():
    """W-4: the user is told, at the start, what Auto does not protect."""
    for tool in ("read", "grep", "find", "ls", "fetch_url", "web_search"):
        assert tool in ENTRY_MESSAGE
    assert "sandbox" in ENTRY_MESSAGE
    assert "/auto-log" in ENTRY_MESSAGE


def test_the_entry_message_of_a_setting_from_agent_toml_says_what_it_meant_before():
    plain = entry_message()
    from_file = entry_message(from_config=True)

    assert from_file.startswith(plain)
    assert 'permission = "always-approve"' in from_file


def test_the_problems_message_lists_each_problem_on_a_line_of_its_own():
    text = problems_message(["first problem.", "second problem."])

    assert text.splitlines()[1:] == ["- first problem.", "- second problem."]
    assert "asks about every call" in text


def test_the_notes_message_lists_each_note():
    text = config_notes_message(["one", "two"])

    assert text.splitlines()[1:] == ["- one", "- two"]


def _record(outcome: str, by: str = "rule", tool: str = "bash", **extra) -> dict:
    return {
        "outcome": outcome,
        "by": by,
        "tool": tool,
        "reason": "read_only_command",
        "rule_id": "allow.bash.read_only",
        "ms": 3,
        **extra,
    }


def test_the_log_with_no_decisions_says_so():
    assert format_log([]) == "Auto has made no decisions in this session."
    assert format_log([None, "text", 3]) == "Auto has made no decisions in this session."


def test_the_log_counts_and_shows_the_last_twenty():
    records = [_record("allow") for _ in range(25)] + [_record("ask", by="floor")]

    lines = format_log(records).splitlines()

    assert lines[0] == "Auto decisions in this session: 26 (allowed 25, asked 1)."
    assert "rule 25" in lines[1]
    assert "floor 1" in lines[1]
    assert lines[3] == "Last 20, oldest first:"
    assert len(lines) == 4 + 20
    assert lines[-1].lstrip().startswith("ask")


def test_the_log_survives_a_record_with_missing_fields():
    text = format_log([{"outcome": "allow"}, _record("ask")])

    assert "Auto decisions in this session: 2" in text


# --------------------------------------------------------------------- the limit registry


def test_a_limit_is_taken_once():
    registry = BashLimitRegistry()
    limits = BashLimits(timeout_seconds=5.0, max_output_bytes=100)
    registry.put("call-1", limits)

    assert registry.take("call-1") is limits
    assert registry.take("call-1") is None
    assert registry.take("never-put") is None


def test_the_registry_keeps_the_newest_limits_when_calls_are_never_taken():
    registry = BashLimitRegistry()
    for index in range(600):
        registry.put(f"call-{index}", BashLimits(timeout_seconds=1.0, max_output_bytes=1))

    assert registry.take("call-0") is None
    assert registry.take("call-599") is not None


def test_clearing_the_registry_drops_what_is_held():
    registry = BashLimitRegistry()
    registry.put("a", BashLimits(timeout_seconds=1.0, max_output_bytes=1))

    registry.clear()

    assert registry.take("a") is None
