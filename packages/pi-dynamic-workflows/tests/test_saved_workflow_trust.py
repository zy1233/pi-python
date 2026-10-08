"""Saved workflows: the project's own need the user's trust, and a bad ``meta`` is not fatal
(audit F7-01, P7-17).

A saved workflow is a script the repository (``<project>/.pi-python/workflows``) or the user
(``<pi home>/workflows``) wrote. The extension turns each into a slash command, and the
command's handler tells the model to run the script. The user's own are theirs to run; the
repository's are only that once the user vouched for the project (``pi.project_trusted``).

The ``meta`` of a script is text somebody else wrote: whatever it holds, one script must not
take the other commands down, and a ``description`` that is not text must not get as far as the
client (where it made the whole ``available_commands_update`` invalid).
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pi_dynamic_workflows import _register_saved_workflows, activate
from pi_dynamic_workflows.builtin_workflows import BUILTIN_WORKFLOW_NAMES
from pi_dynamic_workflows.store import WorkflowStore

from pi_agent_core.extensions import ExtensionLoader, ExtensionRegistry


def _write(directory: Path, name: str, meta: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.py"
    path.write_text(f"meta = {meta}\nasync def main():\n    pass\n", encoding="utf-8")
    return path


@pytest.fixture()
def dirs(tmp_path: Path) -> Any:
    """A project and a pi home, side by side, each with room for saved workflows."""
    project = tmp_path / "project"
    project.mkdir()
    pi_home = tmp_path / "pi-home"
    return SimpleNamespace(
        project=project,
        pi_home=pi_home,
        project_dir=project / ".pi-python" / "workflows",
        user_dir=pi_home / "workflows",
    )


def _store(dirs: Any, **kwargs: Any) -> WorkflowStore:
    return WorkflowStore(cwd=str(dirs.project), pi_home=dirs.pi_home, **kwargs)


# ---------------------------------------------------------------------------
# The store can leave the project's directory out
# ---------------------------------------------------------------------------


class TestStoreWithoutTheProject:
    def test_the_project_directory_is_read_by_default(self, dirs: Any):
        _write(dirs.project_dir, "theirs", '{"name": "theirs"}')
        _write(dirs.user_dir, "mine", '{"name": "mine"}')

        assert {wf.name for wf in _store(dirs).scan()} == {"theirs", "mine"}

    def test_it_can_be_left_out(self, dirs: Any):
        _write(dirs.project_dir, "theirs", '{"name": "theirs"}')
        _write(dirs.user_dir, "mine", '{"name": "mine"}')

        names = {wf.name for wf in _store(dirs, include_project=False).scan()}

        assert names == {"mine"}

    def test_resolve_follows_the_same_rule(self, dirs: Any):
        _write(dirs.project_dir, "theirs", '{"name": "theirs"}')

        assert _store(dirs, include_project=False).resolve("theirs") is None
        assert _store(dirs).resolve("theirs") is not None

    def test_the_users_copy_is_what_is_found_when_the_project_is_left_out(self, dirs: Any):
        _write(dirs.project_dir, "same", '{"name": "same", "description": "theirs"}')
        _write(dirs.user_dir, "same", '{"name": "same", "description": "mine"}')

        (wf,) = _store(dirs, include_project=False).scan()

        assert (wf.source, wf.description) == ("user", "mine")


# ---------------------------------------------------------------------------
# P7-17: ``meta`` is somebody else's text
# ---------------------------------------------------------------------------

NOT_TEXT = ["5", "True", "None", "['a']", "{'x': 1}", "1.5"]


class TestMetaTypes:
    @pytest.mark.parametrize("odd", NOT_TEXT)
    def test_a_description_that_is_not_text_is_dropped(self, dirs: Any, odd: str):
        _write(dirs.user_dir, "odd", f'{{"name": "odd", "description": {odd}}}')

        (wf,) = _store(dirs).scan()

        assert wf.name == "odd"
        assert wf.description == ""

    @pytest.mark.parametrize("odd", NOT_TEXT)
    def test_a_when_to_use_that_is_not_text_is_dropped(self, dirs: Any, odd: str):
        _write(dirs.user_dir, "odd", f'{{"name": "odd", "when_to_use": {odd}}}')

        (wf,) = _store(dirs).scan()

        assert wf.when_to_use is None

    def test_text_is_kept(self, dirs: Any):
        _write(dirs.user_dir, "fine", '{"name": "fine", "description": "D", "when_to_use": "W"}')

        (wf,) = _store(dirs).scan()

        assert (wf.description, wf.when_to_use) == ("D", "W")

    def test_a_script_without_a_description_has_an_empty_one(self, dirs: Any):
        _write(dirs.user_dir, "bare", '{"name": "bare"}')

        (wf,) = _store(dirs).scan()

        assert (wf.description, wf.when_to_use) == ("", None)


# ---------------------------------------------------------------------------
# The extension registers what it may
# ---------------------------------------------------------------------------


_MISSING = object()  # a ``pi`` without ``project_trusted`` at all


class _Pi:
    """What ``_register_saved_workflows`` uses of the extension API."""

    def __init__(self, dirs: Any, *, trusted: Any = True, fail_on: tuple[str, ...] = ()) -> None:
        self.cwd = str(dirs.project)
        self.home = dirs.pi_home
        if trusted is not _MISSING:
            self.project_trusted = trusted
        self.fail_on = fail_on
        self.commands: dict[str, dict[str, Any]] = {}
        self.sent: list[str] = []

    def register_command(
        self, name: str, *, description: str = "", handler: Any, passthrough: bool = False
    ) -> None:
        if name in self.fail_on:
            raise ValueError(f"cannot register {name}")
        self.commands[name] = {"description": description, "handler": handler}

    def send_message(self, text: str) -> None:
        self.sent.append(text)


class TestRegistration:
    def test_a_trusted_project_and_the_user_both_contribute(self, dirs: Any):
        _write(dirs.project_dir, "theirs", '{"name": "theirs", "description": "T"}')
        _write(dirs.user_dir, "mine", '{"name": "mine", "description": "M"}')
        pi = _Pi(dirs, trusted=True)

        _register_saved_workflows(pi)  # type: ignore[arg-type]

        assert {n: c["description"] for n, c in pi.commands.items()} == {
            "theirs": "T",
            "mine": "M",
        }

    def test_an_untrusted_project_contributes_nothing(self, dirs: Any):
        _write(dirs.project_dir, "theirs", '{"name": "theirs"}')
        _write(dirs.user_dir, "mine", '{"name": "mine"}')
        pi = _Pi(dirs, trusted=False)

        _register_saved_workflows(pi)  # type: ignore[arg-type]

        assert set(pi.commands) == {"mine"}

    def test_an_untrusted_project_cannot_replace_the_users_workflow_by_name(self, dirs: Any):
        _write(dirs.project_dir, "same", '{"name": "same", "description": "theirs"}')
        _write(dirs.user_dir, "same", '{"name": "same", "description": "mine"}')
        pi = _Pi(dirs, trusted=False)

        _register_saved_workflows(pi)  # type: ignore[arg-type]

        assert pi.commands["same"]["description"] == "mine"

    def test_a_trusted_project_replaces_the_users_workflow_by_name(self, dirs: Any):
        """Kept as it was: the project's copy wins once the user has vouched for the project."""
        _write(dirs.project_dir, "same", '{"name": "same", "description": "theirs"}')
        _write(dirs.user_dir, "same", '{"name": "same", "description": "mine"}')
        pi = _Pi(dirs, trusted=True)

        _register_saved_workflows(pi)  # type: ignore[arg-type]

        assert pi.commands["same"]["description"] == "theirs"

    @pytest.mark.parametrize("answer", [None, 0, "", "yes", 1, object()])
    def test_only_an_actual_yes_counts(self, dirs: Any, answer: Any):
        """A gate that treats whatever is truthy as a yes lets a mistaken value open it."""
        _write(dirs.project_dir, "theirs", '{"name": "theirs"}')
        pi = _Pi(dirs, trusted=answer)

        _register_saved_workflows(pi)  # type: ignore[arg-type]

        assert "theirs" not in pi.commands

    def test_a_pi_that_cannot_say_leaves_the_project_out_and_says_so(
        self, dirs: Any, caplog: pytest.LogCaptureFixture
    ):
        """An older core has no ``project_trusted``: the safe reading of "cannot tell" is no."""
        _write(dirs.project_dir, "theirs", '{"name": "theirs"}')
        _write(dirs.user_dir, "mine", '{"name": "mine"}')
        pi = _Pi(dirs, trusted=_MISSING)

        with caplog.at_level(logging.WARNING, logger="pi_dynamic_workflows"):
            _register_saved_workflows(pi)  # type: ignore[arg-type]

        assert set(pi.commands) == {"mine"}
        assert any("project_trusted" in r.getMessage() for r in caplog.records)

    def test_a_pi_whose_answer_raises_leaves_the_project_out(self, dirs: Any):
        class _Raising(_Pi):
            @property
            def project_trusted(self) -> bool:  # type: ignore[override]
                raise RuntimeError("not connected to a harness")

        _write(dirs.project_dir, "theirs", '{"name": "theirs"}')
        _write(dirs.user_dir, "mine", '{"name": "mine"}')
        pi = _Raising(dirs, trusted=_MISSING)

        _register_saved_workflows(pi)  # type: ignore[arg-type]

        assert set(pi.commands) == {"mine"}

    @pytest.mark.parametrize("name", ["workflows", *BUILTIN_WORKFLOW_NAMES])
    @pytest.mark.parametrize("where", ["project_dir", "user_dir"])
    def test_the_extensions_own_commands_are_not_a_saved_workflows_to_replace(
        self, dirs: Any, name: str, where: str
    ):
        _write(getattr(dirs, where), name, f'{{"name": "{name}"}}')
        _write(dirs.user_dir, "other", '{"name": "other"}')
        pi = _Pi(dirs, trusted=True)

        _register_saved_workflows(pi)  # type: ignore[arg-type]

        assert set(pi.commands) == {"other"}  # the extension registers its own elsewhere

    def test_a_workflow_that_cannot_be_registered_does_not_stop_the_others(
        self, dirs: Any, caplog: pytest.LogCaptureFixture
    ):
        for name in ("a-first", "boom", "z-last"):
            _write(dirs.user_dir, name, f'{{"name": "{name}"}}')
        pi = _Pi(dirs, trusted=True, fail_on=("boom",))

        with caplog.at_level(logging.WARNING, logger="pi_dynamic_workflows"):
            _register_saved_workflows(pi)  # type: ignore[arg-type]

        assert set(pi.commands) == {"a-first", "z-last"}
        assert any(
            "boom" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records
        )

    def test_a_scan_that_fails_leaves_no_saved_workflow_commands_and_says_so(
        self, dirs: Any, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ):
        """It must not take the extension's activation (the ``workflow`` tool, the ``workflows``
        command) down with it."""

        def broken_scan(self: WorkflowStore) -> list[Any]:
            raise OSError("the disk went away")

        monkeypatch.setattr(WorkflowStore, "scan", broken_scan)
        pi = _Pi(dirs, trusted=True)

        with caplog.at_level(logging.WARNING, logger="pi_dynamic_workflows"):
            _register_saved_workflows(pi)  # type: ignore[arg-type]

        assert pi.commands == {}
        assert any("scan failed" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("odd", NOT_TEXT)
    def test_a_description_that_is_not_text_becomes_the_default_one(self, dirs: Any, odd: str):
        _write(dirs.user_dir, "odd", f'{{"name": "odd", "description": {odd}}}')
        pi = _Pi(dirs, trusted=True)

        _register_saved_workflows(pi)  # type: ignore[arg-type]

        assert pi.commands["odd"]["description"] == "Run saved workflow: odd"

    def test_the_command_tells_the_model_where_the_script_is(self, dirs: Any):
        path = _write(dirs.user_dir, "mine", '{"name": "mine", "description": "Mine"}')
        pi = _Pi(dirs, trusted=True)
        _register_saved_workflows(pi)  # type: ignore[arg-type]

        pi.commands["mine"]["handler"]("")

        (message,) = pi.sent
        assert "**mine**" in message
        assert str(path) in message
        assert "Description: Mine" in message


# ---------------------------------------------------------------------------
# Through the real loader: ``pi.project_trusted`` is the harness's answer
# ---------------------------------------------------------------------------


class _Bridge:
    """Just enough bridge for ``activate``."""

    stream_fn: Any = None
    model: Any = None
    get_api_key_fn: Any = None
    tool_call_gate: Any = None
    session_id = ""

    def __init__(self, cwd: Path, *, project_trusted: bool) -> None:
        self.cwd = str(cwd)
        self.project_trusted = project_trusted

    def register_cleanup(self, callback: Any) -> None:
        pass

    def inject_tool(self, definition: Any) -> None:
        pass


def _load(dirs: Any, *, project_trusted: bool) -> ExtensionRegistry:
    registry = ExtensionRegistry()
    ExtensionLoader(registry, home=dirs.pi_home).load_callable(
        activate, name="wf", bridge=_Bridge(dirs.project, project_trusted=project_trusted)
    )
    return registry


class TestThroughTheLoader:
    def test_a_trusted_project_s_workflows_become_commands(self, dirs: Any):
        _write(dirs.project_dir, "theirs", '{"name": "theirs"}')

        assert "theirs" in _load(dirs, project_trusted=True).get_commands()

    def test_an_untrusted_project_s_workflows_do_not(self, dirs: Any):
        _write(dirs.project_dir, "theirs", '{"name": "theirs"}')
        _write(dirs.user_dir, "mine", '{"name": "mine"}')

        commands = _load(dirs, project_trusted=False).get_commands()

        assert "theirs" not in commands
        assert "mine" in commands
        assert "workflows" in commands  # the extension's own

    def test_the_extensions_own_workflows_command_survives_a_script_of_that_name(self, dirs: Any):
        _write(dirs.project_dir, "workflows", '{"name": "workflows", "description": "SHADOW"}')

        commands = _load(dirs, project_trusted=True).get_commands()

        assert commands["workflows"].description == "List and manage dynamic workflows"
