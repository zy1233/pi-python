"""What ``ExtensionAPI`` accepts from an extension (audit F7-03, F7-04)."""

from __future__ import annotations

import inspect
import warnings
from typing import Any

import pytest

from pi_agent_core.extensions import ExtensionAPI, ExtensionLoader, ExtensionRegistry
from pi_agent_core.extensions.types import ExtensionMeta


def _api(name: str = "test-ext") -> tuple[ExtensionAPI, ExtensionRegistry]:
    registry = ExtensionRegistry()
    return ExtensionAPI(registry=registry, meta=ExtensionMeta(name=name)), registry


# ---------------------------------------------------------------------------
# F7-03: run_async needs a running loop
# ---------------------------------------------------------------------------


class TestRunAsync:
    """``run_async`` starts a task on the running loop. With no running loop there is nothing to
    start it on. It used to make a loop that never ran, so the task never ran: with a
    ``DeprecationWarning`` on Python 3.12, and an error on 3.14."""

    async def test_it_starts_the_task_on_the_running_loop(self) -> None:
        api, _ = _api()

        async def work() -> int:
            return 42

        task = api.run_async(work())

        assert await task == 42

    def test_without_a_running_loop_it_raises_at_once(self) -> None:
        api, _ = _api()

        async def work() -> None:
            return None

        coroutine = work()
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # a DeprecationWarning is a failure too
            with pytest.raises(RuntimeError, match="no running event loop"):
                api.run_async(coroutine)

        # The coroutine never started. It is closed, so that Python does not add a
        # "was never awaited" warning to the error when it collects it.
        assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED


# ---------------------------------------------------------------------------
# F7-04: a command's name can be typed and found, or the extension does not load
# ---------------------------------------------------------------------------


def _nothing(*_args: Any) -> None:
    return None


class TestCommandName:
    """The harness finds a command by the text between the ``/`` and the first white space
    (``AgentHarness._try_slash_dispatch``). A name that this can never give was still sent to
    the clients as a command, and it could not be run."""

    @pytest.mark.parametrize(
        "bad",
        ["", " ", "two words", "tab\tbed", "line\nbreak", "\u3000wide", "/hello", "//", None, 5],
    )
    def test_a_name_that_cannot_be_dispatched_is_refused(self, bad: Any) -> None:
        api, registry = _api()

        with pytest.raises(ValueError, match="command name"):
            api.register_command(bad, description="x", handler=_nothing)

        assert registry.command_count == 0

    def test_the_error_names_the_extension_and_the_rule(self) -> None:
        api, _ = _api("test-ext")

        with pytest.raises(ValueError) as raised:
            api.register_command("two words", description="x", handler=_nothing)

        text = str(raised.value)
        assert "'two words'" in text
        assert "test-ext" in text
        assert "white space" in text

    @pytest.mark.parametrize(
        "fine", ["hello", "web_search", "cmd-a", "v1.2", "mcp:status", "Hello", "说你好"]
    )
    def test_an_ordinary_name_is_accepted(self, fine: str) -> None:
        api, registry = _api()

        api.register_command(fine, description="x", handler=_nothing)

        assert fine in registry.get_commands()

    def test_the_name_is_checked_before_the_description(self) -> None:
        """Both are mistakes of the extension; the name comes first, as in the signature."""
        api, _ = _api()

        with pytest.raises(ValueError, match="command name"):
            api.register_command("two words", description=5, handler=_nothing)  # type: ignore[arg-type]

    def test_an_extension_that_does_it_fails_its_load_and_the_others_still_load(self) -> None:
        def bad_extension(pi: ExtensionAPI) -> None:
            pi.register_command("fine-one", description="ok", handler=_nothing)
            pi.register_command("not fine", description="ok", handler=_nothing)

        def good_extension(pi: ExtensionAPI) -> None:
            pi.register_command("hello", description="ok", handler=_nothing)

        loader = ExtensionLoader()
        loader.load_all(extra=[bad_extension, good_extension], auto_discover=False)

        commands = loader.registry.get_commands()
        assert "hello" in commands  # the other extension
        assert "fine-one" not in commands  # rolled back with the rest of its extension
        assert "not fine" not in commands
        (failure,) = loader.failed
        assert "command name" in failure.error
        assert "'not fine'" in failure.error
