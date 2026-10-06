"""How the loader names, replaces, reports and homes extensions (audit P7-12, P7-13, P7-16)."""

from __future__ import annotations

import importlib.metadata
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from pi_agent_core.extensions import ExtensionAPI, ExtensionLoader, ToolDefinition
from pi_agent_core.extensions.loader import ENTRY_POINT_GROUP, extension_module_names
from pi_agent_core.home import HOME_ENV
from pi_agent_core.types import AgentToolResult


class _NoParams(BaseModel):
    pass


async def _run(tool_call_id: str, params: Any, signal: Any = None, on_update: Any = None):
    return AgentToolResult(content=[{"type": "text", "text": "ok"}])


def _tool(name: str) -> ToolDefinition:
    return ToolDefinition(name=name, description="d", parameters=_NoParams, execute=_run)


def ext_a(pi: ExtensionAPI) -> None:
    pi.register_tool(_tool("tool_from_a"))
    pi.register_command("cmd_a", description="a", handler=lambda args: None)


def ext_b(pi: ExtensionAPI) -> None:
    pi.register_tool(_tool("tool_from_b"))
    pi.register_command("cmd_b", description="b", handler=lambda args: None)


def ext_broken(pi: ExtensionAPI) -> None:
    pi.register_tool(_tool("tool_from_broken"))
    raise ValueError("boom")


class _EntryPoint:
    """The two members of ``importlib.metadata.EntryPoint`` the loader uses."""

    def __init__(self, name: str, load: Callable[[], Any]) -> None:
        self.name = name
        self.load = load


def _entry_points(*declared: _EntryPoint) -> Callable[..., Any]:
    """A stand-in for ``importlib.metadata.entry_points`` (3.12 selects by ``group=``;
    3.11 returns a mapping of groups)."""

    def entry_points(group: str | None = None) -> Any:
        if group is None:
            return {ENTRY_POINT_GROUP: list(declared)}
        return list(declared) if group == ENTRY_POINT_GROUP else []

    return entry_points


# ---------------------------------------------------------------------------
# P7-12: identity
# ---------------------------------------------------------------------------


class TestExtensionIdentity:
    def test_two_extensions_defined_in_one_module_are_both_kept(self):
        """The identity used to be ``activate.__module__``, so the second silently replaced
        the first: only ``tool_from_b`` was left and ``cmd_a`` vanished."""
        loader = ExtensionLoader()

        apis = loader.load_all(extra=[ext_a, ext_b], auto_discover=False)

        assert len(apis) == 2
        assert set(loader.registry.get_tools()) == {"tool_from_a", "tool_from_b"}
        assert set(loader.registry.get_commands()) == {"cmd_a", "cmd_b"}
        assert apis[0].extension_name != apis[1].extension_name

    def test_a_module_level_activate_is_named_after_its_module(self):
        """Entry points and directory extensions are modules that define ``activate``;
        their name stays the module's."""

        def activate(pi: ExtensionAPI) -> None:
            pass

        activate.__module__ = "my_pkg"
        activate.__qualname__ = "activate"

        assert ExtensionLoader().load_callable(activate).extension_name == "my_pkg"

    def test_any_other_function_is_named_after_its_module_and_qualified_name(self):
        api = ExtensionLoader().load_callable(ext_a)

        assert api.extension_name == f"{__name__}.ext_a"

    def test_an_explicit_name_is_used_as_given(self):
        assert ExtensionLoader().load_callable(ext_a, name="mine").extension_name == "mine"

    def test_loading_the_same_function_again_is_a_quiet_reload(
        self, caplog: pytest.LogCaptureFixture
    ):
        loader = ExtensionLoader()
        loader.load_callable(ext_a)

        with caplog.at_level(logging.WARNING, logger="pi_agent_core.extensions.loader"):
            loader.load_callable(ext_a)

        assert len(loader.apis) == 1
        assert caplog.records == []

    def test_a_different_extension_under_a_taken_name_replaces_it_and_says_so(
        self, caplog: pytest.LogCaptureFixture
    ):
        loader = ExtensionLoader()
        loader.load_callable(ext_a, name="shared")

        with caplog.at_level(logging.WARNING, logger="pi_agent_core.extensions.loader"):
            loader.load_callable(ext_b, name="shared")

        assert set(loader.registry.get_tools()) == {"tool_from_b"}  # replaced, as documented
        assert [r.levelno for r in caplog.records] == [logging.WARNING]
        message = caplog.records[0].getMessage()
        assert "shared" in message and "replaces" in message

    def test_a_failed_replacement_keeps_the_original_and_claims_no_replacement(
        self, caplog: pytest.LogCaptureFixture
    ):
        loader = ExtensionLoader()
        loader.load_callable(ext_a, name="shared")

        with caplog.at_level(logging.WARNING, logger="pi_agent_core.extensions.loader"):
            with pytest.raises(ValueError, match="boom"):
                loader.load_callable(ext_broken, name="shared")
            claimed = [r for r in caplog.records if "replaces" in r.getMessage()]

            assert claimed == []  # it did not replace anything: the original was restored
            assert set(loader.registry.get_tools()) == {"tool_from_a"}
            assert [api.extension_name for api in loader.apis] == ["shared"]

            # ...and a later reload of the survivor is still recognised as a reload of it
            caplog.clear()
            loader.load_callable(ext_a, name="shared")
            assert caplog.records == []


# ---------------------------------------------------------------------------
# P7-12: failures are recorded, not swallowed
# ---------------------------------------------------------------------------


class TestLoadFailures:
    def test_load_all_records_an_extension_that_failed_and_loads_the_rest(
        self, caplog: pytest.LogCaptureFixture
    ):
        loader = ExtensionLoader()

        with caplog.at_level(logging.WARNING, logger="pi_agent_core.extensions.loader"):
            apis = loader.load_all(extra=[ext_broken, ext_b], auto_discover=False)

        assert [api.extension_name for api in apis] == [f"{__name__}.ext_b"]
        assert loader.registry.get_tools().keys() == {"tool_from_b"}  # rolled back
        ((failure),) = loader.failed
        assert failure.name == f"{__name__}.ext_broken"
        assert failure.source == "programmatic"
        assert "ValueError" in failure.error and "boom" in failure.error
        assert any("ext_broken" in r.getMessage() for r in caplog.records)

    def test_nothing_is_recorded_when_every_extension_loads(self):
        loader = ExtensionLoader()

        loader.load_all(extra=[ext_a, ext_b], auto_discover=False)

        assert loader.failed == []

    def test_an_extension_that_registers_a_bad_tool_name_fails_as_a_whole(self):
        def bad_name(pi: ExtensionAPI) -> None:
            pi.register_tool(_tool("fine_name"))
            pi.register_tool(_tool("not a valid name"))

        loader = ExtensionLoader()

        loader.load_all(extra=[bad_name], auto_discover=False)

        assert loader.registry.tool_count == 0  # not even the good one
        ((failure),) = loader.failed
        assert "not a valid name" in failure.error

    def test_a_directory_extension_that_cannot_be_imported_is_recorded(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        """The commonest way for an extension to fail is not ``activate()`` raising but the
        module raising as it is imported (a missing dependency, a typo). That used to be a
        log line and nothing else: the extension just was not there."""
        (tmp_path / "healthy.py").write_text("def activate(pi):\n    pass\n", encoding="utf-8")
        (tmp_path / "broken.py").write_text("import no_such_module_anywhere\n", encoding="utf-8")
        loader = ExtensionLoader()

        with caplog.at_level(logging.WARNING, logger="pi_agent_core.extensions.loader"):
            found = loader.discover_directory(tmp_path)

        assert len(found) == 1  # the healthy one is still found
        ((failure),) = loader.failed
        assert (failure.name, failure.source) == ("broken.py", "directory")
        assert "ModuleNotFoundError" in failure.error
        assert "no_such_module_anywhere" in failure.error
        assert any("broken.py" in r.getMessage() for r in caplog.records)  # still logged

    def test_a_package_extension_that_cannot_be_imported_is_recorded_by_its_directory(
        self, tmp_path: Path
    ):
        package = tmp_path / "my_ext"
        package.mkdir()
        source = "raise RuntimeError('half-written')\n"
        (package / "__init__.py").write_text(source, encoding="utf-8")
        loader = ExtensionLoader()

        assert loader.discover_directory(tmp_path) == []

        ((failure),) = loader.failed
        assert (failure.name, failure.source) == ("my_ext", "directory")
        assert failure.error == "RuntimeError: half-written"

    def test_an_entry_point_that_cannot_be_loaded_is_recorded(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        def missing_dependency() -> Any:
            raise ImportError("no module named 'nope'")

        monkeypatch.setattr(
            importlib.metadata,
            "entry_points",
            _entry_points(
                _EntryPoint("pi-broken", missing_dependency),
                _EntryPoint("pi-fine", lambda: ext_a),
            ),
        )
        loader = ExtensionLoader()

        found = loader.discover_entry_points()

        assert found == [ext_a]
        ((failure),) = loader.failed
        assert (failure.name, failure.source) == ("pi-broken", "entry_point")
        assert failure.error == "ImportError: no module named 'nope'"

    def test_an_entry_point_that_is_not_an_extension_is_recorded(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Declaring an entry point is an explicit claim to be an extension, so one that
        resolves to something with no ``activate`` must not be dropped without a word."""
        monkeypatch.setattr(
            importlib.metadata, "entry_points", _entry_points(_EntryPoint("pi-odd", lambda: 42))
        )
        loader = ExtensionLoader()

        assert loader.discover_entry_points() == []

        ((failure),) = loader.failed
        assert (failure.name, failure.source) == ("pi-odd", "entry_point")
        assert "activate" in failure.error

    def test_import_and_activation_failures_are_reported_together(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        extensions = tmp_path / "home" / "extensions"
        extensions.mkdir(parents=True)
        (extensions / "broken.py").write_text("raise RuntimeError('at import')\n", encoding="utf-8")
        monkeypatch.setattr(
            importlib.metadata,
            "entry_points",
            _entry_points(_EntryPoint("pi-broken", lambda: ext_broken)),
        )
        loader = ExtensionLoader(home=tmp_path / "home")

        loader.load_all(extra=[ext_b], auto_discover=True)

        assert {(f.source, f.error.split(":")[0]) for f in loader.failed} == {
            ("entry_point", "ValueError"),  # activate() raised
            ("directory", "RuntimeError"),  # the module raised as it was imported
        }
        assert set(loader.registry.get_tools()) == {"tool_from_b"}


# ---------------------------------------------------------------------------
# P7-16: tool names
# ---------------------------------------------------------------------------


class TestToolNames:
    @pytest.mark.parametrize(
        "name",
        ["a", "web_search", "my-tool-2", "Mixed_Case-9", "x" * 128],
    )
    def test_names_providers_accept_are_registered(self, name: str):
        loader = ExtensionLoader()

        def activate(pi: ExtensionAPI) -> None:
            pi.register_tool(_tool(name))

        loader.load_callable(activate, name="ext")

        assert name in loader.registry.get_tools()

    @pytest.mark.parametrize(
        "name",
        ["", "has space", "dot.ted", "slash/ed", "x" * 129, "名字", "trailing\n", "tab\t", "a:b"],
    )
    def test_names_providers_reject_are_refused_at_registration(self, name: str):
        """One such name makes every later request fail server-side, for the whole session."""
        loader = ExtensionLoader()

        def activate(pi: ExtensionAPI) -> None:
            pi.register_tool(_tool(name))

        with pytest.raises(ValueError, match="tool name"):
            loader.load_callable(activate, name="ext")

        assert loader.registry.tool_count == 0

    @pytest.mark.parametrize("name", [None, 7, b"bytes"])
    def test_a_name_that_is_not_text_is_refused_the_same_way(self, name: Any):
        """Not a ``TypeError`` out of the regex: the message names the extension and the rule."""
        loader = ExtensionLoader()

        def activate(pi: ExtensionAPI) -> None:
            pi.register_tool(_tool(name))

        with pytest.raises(ValueError, match="tool name"):
            loader.load_callable(activate, name="ext")

        assert loader.registry.tool_count == 0

    def test_the_error_names_the_extension_and_the_rule(self):
        def activate(pi: ExtensionAPI) -> None:
            pi.register_tool(_tool("no good"))

        with pytest.raises(ValueError) as caught:
            ExtensionLoader().load_callable(activate, name="ext-under-test")

        text = str(caught.value)
        assert "'no good'" in text and "ext-under-test" in text and "a-zA-Z0-9_-" in text


# ---------------------------------------------------------------------------
# P7-13: one notion of "home"
# ---------------------------------------------------------------------------


def _write_command_extension(directory: Path, command: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{command}.py").write_text(
        "def activate(pi):\n"
        f"    pi.register_command({command!r}, description='x', handler=lambda args: None)\n",
        encoding="utf-8",
    )


class TestExtensionHome:
    @pytest.fixture()
    def homes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
        from types import SimpleNamespace

        user = tmp_path / "user"
        user.mkdir()
        monkeypatch.delenv(HOME_ENV, raising=False)
        monkeypatch.setenv("HOME", str(user))
        monkeypatch.setenv("USERPROFILE", str(user))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
        return SimpleNamespace(
            default_ext=user / ".pi-python" / "extensions",
            env_home=tmp_path / "env-home",
            explicit_home=tmp_path / "explicit-home",
        )

    def test_the_user_directory_follows_pi_home(self, homes: Any, monkeypatch: pytest.MonkeyPatch):
        _write_command_extension(homes.default_ext, "from_default_home")
        _write_command_extension(homes.env_home / "extensions", "from_pi_home")
        monkeypatch.setenv(HOME_ENV, str(homes.env_home))
        loader = ExtensionLoader()
        monkeypatch.setattr(loader, "discover_entry_points", lambda: [])

        loader.load_all(auto_discover=True)

        assert "from_pi_home" in loader.registry.get_commands()
        assert "from_default_home" not in loader.registry.get_commands()

    def test_an_explicit_home_beats_the_environment(
        self, homes: Any, monkeypatch: pytest.MonkeyPatch
    ):
        _write_command_extension(homes.env_home / "extensions", "from_pi_home")
        _write_command_extension(homes.explicit_home / "extensions", "from_explicit_home")
        monkeypatch.setenv(HOME_ENV, str(homes.env_home))
        loader = ExtensionLoader(home=homes.explicit_home)
        monkeypatch.setattr(loader, "discover_entry_points", lambda: [])

        loader.load_all(auto_discover=True)

        commands = loader.registry.get_commands()
        assert "from_explicit_home" in commands
        assert "from_pi_home" not in commands

    def test_extensions_learn_the_home_they_were_loaded_with(self, homes: Any):
        seen: list[Path] = []

        def activate(pi: ExtensionAPI) -> None:
            seen.append(pi.home)

        ExtensionLoader(home=homes.explicit_home).load_callable(activate, name="probe")
        ExtensionLoader().load_callable(activate, name="probe")

        assert seen == [homes.explicit_home, homes.default_ext.parent]

    def test_the_project_directory_stays_gated_when_the_home_is_elsewhere(
        self, homes: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        project = tmp_path / "project"
        _write_command_extension(project / ".pi-python" / "extensions", "from_project")
        monkeypatch.setenv(HOME_ENV, str(homes.env_home))
        loader = ExtensionLoader()
        monkeypatch.setattr(loader, "discover_entry_points", lambda: [])

        loader.load_all(cwd=str(project), auto_discover=True)

        assert "from_project" not in loader.registry.get_commands()
        assert [s.names for s in loader.skipped] == [("from_project.py",)]


# ---------------------------------------------------------------------------
# What a scan of a directory would import (the trust prompt names exactly this)
# ---------------------------------------------------------------------------


class TestExtensionModuleNames:
    def test_it_lists_the_modules_and_packages_a_scan_imports(self, tmp_path: Path):
        (tmp_path / "b.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "pack").mkdir()
        (tmp_path / "pack" / "__init__.py").write_text("x = 1\n", encoding="utf-8")

        assert extension_module_names(tmp_path) == ("a.py", "b.py", "pack")

    def test_it_leaves_out_what_a_scan_ignores(self, tmp_path: Path):
        (tmp_path / "_private.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")
        (tmp_path / "not_a_package").mkdir()
        (tmp_path / "not_a_package" / "mod.py").write_text("x = 1\n", encoding="utf-8")

        assert extension_module_names(tmp_path) == ()

    def test_a_missing_directory_has_none(self, tmp_path: Path):
        assert extension_module_names(tmp_path / "nope") == ()

    def test_it_imports_nothing(self, tmp_path: Path):
        marker = tmp_path / "ran"
        (tmp_path / "boom.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('x')\n", encoding="utf-8"
        )

        assert extension_module_names(tmp_path) == ("boom.py",)
        assert not marker.exists()

    def test_it_names_what_the_untrusted_report_names(self, tmp_path: Path):
        project_ext = tmp_path / "project" / ".pi-python" / "extensions"
        project_ext.mkdir(parents=True)
        (project_ext / "one.py").write_text("x = 1\n", encoding="utf-8")
        (project_ext / "_two.py").write_text("x = 1\n", encoding="utf-8")
        loader = ExtensionLoader(home=tmp_path / "home")

        loader.discover_default_dirs(str(tmp_path / "project"))

        assert [s.names for s in loader.skipped] == [extension_module_names(project_ext)]
