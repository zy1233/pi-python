"""The user is told when an extension failed to load (audit P7-12).

The loader used to log the failure and carry on, so an extension that raised at startup (a
missing dependency, a typo) simply was not there, and the only trace was a traceback on stderr
that an ACP client never shows.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig
from pi_agent_cli.extension_notices import failed_extensions_notice
from pi_agent_cli.extension_trust import TRUST_ENV
from pi_agent_core.extensions import ExtensionAPI, ExtensionLoader, FailedExtension
from pi_agent_core.tests.mock_stream import mock_text_stream


def _failure(
    name: str = "pi-broken",
    error: str = "ImportError: no module named 'nope'",
    source: str = "entry_point",
) -> FailedExtension:
    return FailedExtension(name=name, source=source, error=error)


# ---------------------------------------------------------------------------
# The message
# ---------------------------------------------------------------------------


def test_there_is_no_notice_when_nothing_failed():
    assert failed_extensions_notice([]) is None


def test_the_notice_names_each_extension_where_it_came_from_and_why_it_failed():
    notice = failed_extensions_notice(
        [_failure(), _failure("local.py", "ValueError: bad config", "directory")]
    )

    assert notice is not None
    assert "pi-broken (entry_point): ImportError: no module named 'nope'" in notice
    assert "local.py (directory): ValueError: bad config" in notice


def test_the_notice_counts_the_failures():
    one = failed_extensions_notice([_failure()])
    two = failed_extensions_notice([_failure("a"), _failure("b")])

    assert one is not None and one.startswith("1 extension failed to load and was skipped")
    assert two is not None and two.startswith("2 extensions failed to load and were skipped")


def test_the_notice_says_where_to_look_for_more():
    notice = failed_extensions_notice([_failure()])

    assert notice is not None and "log (stderr)" in notice


def test_only_the_first_line_of_an_error_is_shown():
    """``str(exc)`` can be a whole traceback or a screenful of pip output."""
    error = "RuntimeError: first line\n" + "  File deep.py, line 1\n" * 40

    notice = failed_extensions_notice([_failure(error=error)])

    assert notice is not None
    assert "RuntimeError: first line" in notice
    assert "deep.py" not in notice


def test_a_very_long_error_line_is_cut():
    notice = failed_extensions_notice([_failure(error="E: " + "y" * 5000)])

    assert notice is not None
    assert "y" * 300 not in notice
    assert len(notice) < 600
    assert "..." in notice


def test_an_error_with_no_text_still_reads_sensibly():
    notice = failed_extensions_notice([_failure(error="")])

    assert notice is not None
    assert "\n- pi-broken (entry_point)\n" in notice  # no dangling ": "


# ---------------------------------------------------------------------------
# Delivered to the user
# ---------------------------------------------------------------------------


def broken_extension(pi: ExtensionAPI) -> None:
    raise RuntimeError("boom at startup")


def fine_extension(pi: ExtensionAPI) -> None:
    pi.register_command("fine-command", description="works", handler=lambda args: None)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TRUST_ENV, raising=False)
    # Real installed entry-point extensions are not what these tests are about.
    monkeypatch.setattr(ExtensionLoader, "discover_entry_points", lambda self: [])


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    project = tmp_path / "project"
    project.mkdir()
    return SimpleNamespace(pi_home=home / ".pi-python", project=project)


class _RecordingClient:
    def __init__(self) -> None:
        self.updates: list[Any] = []

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append(update)

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        raise AssertionError("no tool should ask for permission here")

    def agent_messages(self) -> list[str]:
        return [
            getattr(getattr(u, "content", None), "text", "") or ""
            for u in self.updates
            if getattr(u, "session_update", None) == "agent_message_chunk"
        ]


async def _open_session(world: Any, extensions: list[Any]) -> _RecordingClient:
    agent = PiAcpAgent(
        stream_fn=mock_text_stream,
        home=world.pi_home,
        config=CliConfig(permission="auto"),
        extensions=extensions,
    )
    client = _RecordingClient()
    agent.on_connect(client)
    await agent.new_session(cwd=str(world.project))
    for _ in range(4):
        await asyncio.sleep(0)
    return client


@pytest.mark.asyncio
async def test_the_acp_client_is_told_which_extension_failed_and_why(world: Any):
    client = await _open_session(world, [broken_extension, fine_extension])

    (message,) = client.agent_messages()
    assert "broken_extension" in message
    assert "RuntimeError: boom at startup" in message
    assert "fine_extension" not in message  # the healthy one is not blamed


@pytest.mark.asyncio
async def test_the_acp_client_hears_nothing_when_every_extension_loaded(world: Any):
    client = await _open_session(world, [fine_extension])

    assert client.agent_messages() == []


@pytest.mark.asyncio
async def test_an_extension_that_cannot_be_imported_is_reported_too(world: Any):
    """The commonest failure: the module itself raises as it is imported."""
    extensions = world.pi_home / "extensions"
    extensions.mkdir(parents=True)
    (extensions / "typo.py").write_text("import no_such_module_anywhere\n", encoding="utf-8")

    client = await _open_session(world, [])

    (message,) = client.agent_messages()
    assert "typo.py (directory)" in message
    assert "no_such_module_anywhere" in message


@pytest.mark.asyncio
async def test_the_untrusted_project_notice_and_the_failure_notice_both_arrive(world: Any):
    shipped = world.project / ".pi-python" / "extensions"
    shipped.mkdir(parents=True)
    (shipped / "from_repo.py").write_text("def activate(pi):\n    pass\n", encoding="utf-8")

    client = await _open_session(world, [broken_extension])

    text = "\n".join(client.agent_messages())
    assert "trusted_projects" in text  # the project's extension was not loaded
    assert "boom at startup" in text  # ...and the user's own one failed


@pytest.mark.asyncio
async def test_headless_prints_failures_to_stderr(
    world: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from pi_agent_cli.headless import run_print

    extensions = world.pi_home / "extensions"
    extensions.mkdir(parents=True)
    (extensions / "typo.py").write_text("import no_such_module_anywhere\n", encoding="utf-8")
    monkeypatch.setenv("PI_USE_MOCK", "1")

    code = await run_print("hello", cwd=world.project, home=world.pi_home)

    captured = capsys.readouterr()
    assert code == 0  # a failed extension does not fail the run
    assert "typo.py (directory)" in captured.err
    assert "typo.py" not in captured.out  # stdout carries only the assistant's answer
