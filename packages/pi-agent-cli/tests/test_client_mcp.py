"""The MCP servers a client passes, on their way to the extensions (design: docs/specs/
2026-10-23-mcp-extension-design.md).

The CLI has no MCP client. ``client_mcp`` turns what ACP carries (``session/new`` / ``load`` /
``resume``) into plain records and hands them to the harness before the extensions load; an
extension (``pi-mcp-py``) may claim and connect them. These tests use probe extensions in place
of it. They cover the conversion, the hand-over on every path that opens a session, what the user
is told, and the three things the CLI does so that an extension can work: it holds lines for the
user until the client knows the session, it lets a turn wait for the extension's barriers (and
``session/cancel`` end that wait), and it closes the harness that a re-opened session replaces.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acp import text_block
from acp.schema import EnvVariable, HttpHeader, HttpMcpServer, McpServerStdio, SseMcpServer

from pi_agent_cli import client_mcp
from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.client_mcp import (
    MCP_EXTENSION_ENTRY_POINT,
    client_mcp_servers,
    mcp_extension_installed,
)
from pi_agent_cli.config import CliConfig
from pi_agent_cli.events import project_event
from pi_agent_cli.extension_trust import TRUST_ENV
from pi_agent_core.extensions import ClientMcpServer, ExtensionAPI, ExtensionLoader
from pi_agent_core.extensions.loader import ENTRY_POINT_GROUP
from pi_agent_core.tests.mock_stream import mock_text_stream
from pi_agent_harness.types import NoticeEvent

SECRET = "SECRET-TOKEN-1234"
WAIT = 10  # seconds: a broken test fails instead of hanging


def stdio(name: str = "files", **kwargs: Any) -> McpServerStdio:
    fields: dict[str, Any] = {
        "name": name,
        "command": "npx",
        "args": ["-y", "server", f"--token={SECRET}"],
        "env": [EnvVariable(name="API_KEY", value=SECRET), EnvVariable(name="MODE", value="x")],
    }
    fields.update(kwargs)
    return McpServerStdio(**fields)


def http(name: str = "search", **kwargs: Any) -> HttpMcpServer:
    fields: dict[str, Any] = {
        "type": "http",
        "name": name,
        "url": f"https://example.invalid/mcp?key={SECRET}",
        "headers": [HttpHeader(name="Authorization", value=f"Bearer {SECRET}")],
    }
    fields.update(kwargs)
    return HttpMcpServer(**fields)


def sse(name: str = "events", **kwargs: Any) -> SseMcpServer:
    fields: dict[str, Any] = {"type": "sse", "name": name, "url": "https://example.invalid/sse"}
    fields.update(kwargs, headers=kwargs.get("headers", []))
    return SseMcpServer(**fields)


# ---------------------------------------------------------------------------
# The conversion
# ---------------------------------------------------------------------------


class TestConversion:
    def test_nothing_gives_nothing(self):
        assert client_mcp_servers(None) == []
        assert client_mcp_servers([]) == []
        assert client_mcp_servers(()) == []

    def test_a_stdio_server(self):
        [record] = client_mcp_servers([stdio()])

        assert record == ClientMcpServer(
            name="files",
            transport="stdio",
            command="npx",
            args=("-y", "server", f"--token={SECRET}"),
            env=(("API_KEY", SECRET), ("MODE", "x")),
        )

    def test_an_http_and_an_sse_server(self):
        http_record, sse_record = client_mcp_servers([http(), sse(headers=[])])

        assert http_record.transport == "http"
        assert http_record.url == f"https://example.invalid/mcp?key={SECRET}"
        assert http_record.headers == (("Authorization", f"Bearer {SECRET}"),)
        assert (http_record.command, http_record.args, http_record.env) == ("", (), ())
        assert sse_record.transport == "sse"
        assert sse_record.url == "https://example.invalid/sse"
        assert sse_record.headers == ()

    def test_the_order_is_the_order_the_client_gave(self):
        records = client_mcp_servers([sse("c"), stdio("a"), http("b")])

        assert [r.name for r in records] == ["c", "a", "b"]

    def test_plain_dicts_work_like_the_models(self):
        records = client_mcp_servers(
            [
                {
                    "name": "s",
                    "command": "uvx",
                    "args": ["x"],
                    "env": [{"name": "K", "value": "v"}],
                },
                {"type": "http", "name": "h", "url": "https://x.invalid/", "headers": {"A": "b"}},
                {"type": "stdio", "name": "t", "command": "node", "env": {"N": 1}},
            ]
        )

        assert records[0] == ClientMcpServer(
            name="s", transport="stdio", command="uvx", args=("x",), env=(("K", "v"),)
        )
        assert records[1].headers == (("A", "b"),)  # a mapping is accepted too
        assert records[2].env == (("N", "1"),)  # and values become text

    def test_surrounding_space_is_cut_from_the_command_and_the_url(self):
        records = client_mcp_servers(
            [{"name": "a", "command": "  npx \n"}, {"type": "sse", "name": "b", "url": " u://x "}]
        )

        assert (records[0].command, records[1].url) == ("npx", "u://x")

    def test_an_entry_without_a_name_is_given_one_from_its_position(self):
        records = client_mcp_servers(
            [stdio(""), http("   "), {"command": "npx"}, {"command": "npx", "name": 5}]
        )

        assert [r.name for r in records] == ["server-1", "server-2", "server-3", "server-4"]

    def test_a_name_that_is_taken_gets_a_number(self):
        records = client_mcp_servers([stdio("x"), http("x"), sse("x"), stdio("x-2")])

        assert [r.name for r in records] == ["x", "x-2", "x-3", "x-2-2"]

    def test_names_are_one_line_of_printable_text(self):
        [spaced, controls] = client_mcp_servers(
            [stdio("two\nlines  and\ttabs"), stdio("bell\x07 and\x1b[31m red\x00")]
        )

        assert spaced.name == "two lines and tabs"
        assert controls.name == "bell and [31m red"
        for record in (spaced, controls):
            assert record.name.isprintable()

    def test_a_server_that_cannot_be_used_is_skipped_and_the_others_stay(
        self, caplog: pytest.LogCaptureFixture
    ):
        servers = [
            stdio("good"),
            {"name": "no-command"},
            {"type": "stdio", "name": "blank-command", "command": "   "},
            {"type": "http", "name": "no-url", "headers": []},
            {"type": "sse", "name": "blank-url", "url": " "},
            {"type": "websocket", "name": "odd-type", "url": f"ws://x/?k={SECRET}"},
            "not a server at all",
            None,
            http("also-good"),
        ]

        with caplog.at_level(logging.WARNING, logger="pi_agent_cli.client_mcp"):
            records = client_mcp_servers(servers)

        assert [r.name for r in records] == ["good", "also-good"]
        for skipped in ("no-command", "blank-command", "no-url", "blank-url", "odd-type"):
            assert skipped in caplog.text
        assert SECRET not in caplog.text

    def test_a_skipped_server_does_not_use_up_its_name(self):
        records = client_mcp_servers([{"name": "x"}, stdio("x")])

        assert [r.name for r in records] == ["x"]

    def test_nothing_that_may_hold_a_secret_is_logged_or_shown(
        self, caplog: pytest.LogCaptureFixture
    ):
        with caplog.at_level(logging.DEBUG, logger="pi_agent_cli.client_mcp"):
            records = client_mcp_servers([stdio(), http(), sse(), {"type": "x", "name": "bad"}])

        assert SECRET not in caplog.text
        assert SECRET not in repr(records)

    def test_malformed_pairs_are_left_out_and_the_server_stays(self):
        [record] = client_mcp_servers(
            [
                {
                    "name": "a",
                    "command": "x",
                    "env": [
                        {"name": "", "value": "v"},
                        {"name": "K"},
                        {"value": "v"},
                        7,
                        None,
                        {"name": "OK", "value": "fine"},
                    ],
                    "args": "not a list",
                }
            ]
        )

        assert record.env == (("OK", "fine"),)
        assert record.args == ()


# ---------------------------------------------------------------------------
# Is the extension there?
# ---------------------------------------------------------------------------


def fake_entry_points(*names: str) -> Callable[..., list[Any]]:
    def entry_points(**kwargs: Any) -> list[Any]:
        assert kwargs == {"group": ENTRY_POINT_GROUP}  # the one group extensions use
        return [SimpleNamespace(name=name) for name in names]

    return entry_points


class TestExtensionInstalled:
    def test_the_group_is_the_one_the_loader_reads(self):
        assert client_mcp._EXTENSION_GROUP == ENTRY_POINT_GROUP

    def test_it_is_found_by_the_name_of_its_entry_point(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            client_mcp, "entry_points", fake_entry_points("pi-web", MCP_EXTENSION_ENTRY_POINT)
        )

        assert mcp_extension_installed() is True

    def test_other_extensions_do_not_count(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(client_mcp, "entry_points", fake_entry_points("pi-web", "pi-goal"))

        assert mcp_extension_installed() is False

    def test_no_extension_at_all(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(client_mcp, "entry_points", fake_entry_points())

        assert mcp_extension_installed() is False

    def test_a_failure_to_read_the_metadata_means_not_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        def broken(**kwargs: Any) -> list[Any]:
            raise OSError("metadata unreadable")

        monkeypatch.setattr(client_mcp, "entry_points", broken)

        assert mcp_extension_installed() is False

    def test_it_reads_metadata_and_imports_nothing(self, monkeypatch: pytest.MonkeyPatch):
        # A broken extension must still count as installed, so the entry point is not loaded.
        loaded: list[str] = []

        class Point:
            name = MCP_EXTENSION_ENTRY_POINT

            def load(self) -> Any:
                loaded.append("loaded")
                raise ImportError("broken")

        monkeypatch.setattr(client_mcp, "entry_points", lambda **kwargs: [Point()])

        assert mcp_extension_installed() is True
        assert loaded == []


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TRUST_ENV, raising=False)
    # Real installed entry-point extensions are not what these tests are about.
    monkeypatch.setattr(ExtensionLoader, "discover_entry_points", lambda self: [])
    # The environment may or may not have ``pi-mcp-py``: say it has not, unless a test says so.
    monkeypatch.setattr("pi_agent_cli.agent.mcp_extension_installed", lambda: False)


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


class Client:
    def __init__(self) -> None:
        self.updates: list[Any] = []

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append(update)

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        raise AssertionError("no tool should ask for permission here")

    def kinds(self) -> list[str]:
        return [getattr(u, "session_update", "?") for u in self.updates]

    def agent_messages(self) -> list[str]:
        return [
            getattr(getattr(u, "content", None), "text", "") or ""
            for u in self.updates
            if getattr(u, "session_update", None) == "agent_message_chunk"
        ]


class Model:
    """A ``stream_fn`` that counts the turns it is asked for."""

    def __init__(self) -> None:
        self.turns = 0

    async def __call__(self, model: Any, context: Any, options: Any = None) -> Any:
        self.turns += 1
        return await mock_text_stream(model, context, options)


def make_agent(
    world: Any, *extensions: Callable[[ExtensionAPI], None], model: Model | None = None
) -> tuple[PiAcpAgent, Client]:
    agent = PiAcpAgent(
        stream_fn=model or mock_text_stream,
        home=world.pi_home,
        config=CliConfig(permission="auto", provider="mock", model_id="mock"),  # type: ignore[arg-type]
        extensions=list(extensions),
    )
    client = Client()
    agent.on_connect(client)
    return agent, client


async def settle(agent: PiAcpAgent) -> None:
    # The deferred setup of a session runs after the response has flushed (Zed drops earlier
    # updates).
    await asyncio.gather(*agent._background_tasks)


async def until(condition: Callable[[], Any], *, timeout: float = WAIT) -> None:
    async def poll() -> None:
        while not condition():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


def passed(agent: PiAcpAgent, session_id: str) -> list[ClientMcpServer]:
    return list(agent._harnesses[session_id].client_mcp_servers)


# -- initialize ------------------------------------------------------------


class TestInitialize:
    async def test_without_the_extension_remote_servers_are_not_offered(self, world: Any):
        agent, _ = make_agent(world)

        response = await agent.initialize(protocol_version=1)

        caps = response.agent_capabilities.mcp_capabilities
        assert caps is not None and (caps.http, caps.sse) == (False, False)
        # What the wire carries is what it was before: an object, all false.
        wire = response.model_dump(by_alias=True)["agentCapabilities"]["mcpCapabilities"]
        assert wire == {"http": False, "sse": False, "acp": False, "_meta": None}

    async def test_with_the_extension_http_and_sse_are_offered(
        self, world: Any, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr("pi_agent_cli.agent.mcp_extension_installed", lambda: True)
        agent, _ = make_agent(world)

        response = await agent.initialize(protocol_version=1)

        caps = response.agent_capabilities.mcp_capabilities
        assert caps is not None and (caps.http, caps.sse, caps.acp) == (True, True, False)


# -- the hand-over ---------------------------------------------------------


class TestHandOver:
    async def test_new_session_gives_the_harness_the_servers(self, world: Any):
        agent, _ = make_agent(world)

        created = await agent.new_session(
            cwd=str(world.project), mcp_servers=[stdio(), http(), sse()]
        )
        await settle(agent)

        assert [(s.name, s.transport) for s in passed(agent, created.session_id)] == [
            ("files", "stdio"),
            ("search", "http"),
            ("events", "sse"),
        ]

    async def test_no_servers_is_an_empty_list_not_an_error(self, world: Any):
        agent, _ = make_agent(world)

        none = await agent.new_session(cwd=str(world.project))
        empty = await agent.new_session(cwd=str(world.project), mcp_servers=[])
        await settle(agent)

        assert passed(agent, none.session_id) == passed(agent, empty.session_id) == []

    async def test_load_and_resume_of_a_saved_session_pass_them_too(self, world: Any):
        agent, _ = make_agent(world)
        created = await agent.new_session(cwd=str(world.project))
        await settle(agent)
        sid = created.session_id
        await agent.prompt(session_id=sid, prompt=[text_block("hi")])  # saves the session
        await agent.close_session(session_id=sid)

        await agent.load_session(cwd=str(world.project), session_id=sid, mcp_servers=[stdio("l")])
        await settle(agent)
        assert [s.name for s in passed(agent, sid)] == ["l"]

        await agent.close_session(session_id=sid)
        await agent.resume_session(cwd=str(world.project), session_id=sid, mcp_servers=[http("r")])
        await settle(agent)
        assert [s.name for s in passed(agent, sid)] == ["r"]

    @pytest.mark.parametrize("method", ["load_session", "resume_session"])
    async def test_a_session_nobody_typed_in_gets_them_as_well(self, world: Any, method: str):
        # No session file (plan decision 17): the client asks for the id again, and the session
        # is opened empty. The servers it passes with that request count like any others.
        agent, _ = make_agent(world)
        sid = str(uuid.uuid4())

        await getattr(agent, method)(
            cwd=str(world.project), session_id=sid, mcp_servers=[stdio("again"), sse("more")]
        )
        await settle(agent)

        assert [s.name for s in passed(agent, sid)] == ["again", "more"]

    async def test_a_server_the_client_gets_wrong_does_not_fail_the_session(self, world: Any):
        agent, _ = make_agent(world)

        created = await agent.new_session(
            cwd=str(world.project), mcp_servers=[{"name": "broken"}, stdio("fine")]
        )
        await settle(agent)

        assert [s.name for s in passed(agent, created.session_id)] == ["fine"]

    async def test_the_extension_sees_them_as_it_activates(self, world: Any):
        seen: list[tuple[str, str, tuple[tuple[str, str], ...]]] = []

        def probe(pi: ExtensionAPI) -> None:
            seen.extend((s.name, s.command, s.env) for s in pi.client_mcp_servers)

        agent, _ = make_agent(world, probe)

        await agent.new_session(cwd=str(world.project), mcp_servers=[stdio()])
        await settle(agent)

        assert seen == [("files", "npx", (("API_KEY", SECRET), ("MODE", "x")))]


# -- what the user is told -------------------------------------------------


def claiming(*names: str) -> Callable[[ExtensionAPI], None]:
    def extension(pi: ExtensionAPI) -> None:
        pi.claim_client_mcp_servers(names)

    return extension


class TestIgnoredServersNotice:
    async def test_a_claimed_server_is_not_called_ignored(self, world: Any):
        agent, client = make_agent(world, claiming("files"))

        await agent.new_session(cwd=str(world.project), mcp_servers=[stdio("files")])
        await settle(agent)

        assert client.agent_messages() == []

    async def test_only_the_unclaimed_servers_are_named(self, world: Any):
        agent, client = make_agent(world, claiming("files"))

        await agent.new_session(
            cwd=str(world.project), mcp_servers=[stdio("files"), http("search"), sse("events")]
        )
        await settle(agent)

        (message,) = client.agent_messages()
        assert "2 MCP servers (search, events)" in message
        assert "files" not in message

    async def test_the_extensions_may_split_the_servers_between_them(self, world: Any):
        agent, client = make_agent(world, claiming("a"), claiming("b"))

        await agent.new_session(
            cwd=str(world.project), mcp_servers=[stdio("a"), stdio("b"), stdio("c")]
        )
        await settle(agent)

        (message,) = client.agent_messages()
        assert "1 MCP server (c)" in message

    async def test_the_log_names_only_the_unclaimed_servers_and_no_secret(
        self, world: Any, caplog: pytest.LogCaptureFixture
    ):
        agent, _ = make_agent(world, claiming("files"))

        with caplog.at_level(logging.WARNING, logger="pi_agent_cli.agent"):
            await agent.new_session(
                cwd=str(world.project), mcp_servers=[stdio("files"), http("search")]
            )
            await settle(agent)

        assert "Ignoring 1 MCP server(s)" in caplog.text and "search" in caplog.text
        assert SECRET not in caplog.text

    async def test_the_text_depends_on_whether_the_extension_is_installed(
        self, world: Any, monkeypatch: pytest.MonkeyPatch
    ):
        agent, client = make_agent(world)
        await agent.new_session(cwd=str(world.project), mcp_servers=[stdio("one")])
        await settle(agent)
        monkeypatch.setattr("pi_agent_cli.agent.mcp_extension_installed", lambda: True)
        await agent.new_session(cwd=str(world.project), mcp_servers=[stdio("two")])
        await settle(agent)

        without, installed = client.agent_messages()
        assert "install the pi-mcp-py package" in without
        assert "No extension took it over" in installed and "install" not in installed


# -- lines for the user ----------------------------------------------------


def noisy(captured: dict[str, Any], text: str = "early line") -> Callable[[ExtensionAPI], None]:
    """An extension that says something as it activates, and has a command (so the agent has
    something to advertise: that is the first update the client takes)."""

    def extension(pi: ExtensionAPI) -> None:
        captured["pi"] = pi
        pi.register_command("probe", description="probe", handler=lambda args: None)
        pi.notify(text)

    return extension


class TestNotices:
    async def test_a_line_sent_at_activation_waits_until_the_client_knows_the_session(
        self, world: Any
    ):
        agent, client = make_agent(world, noisy({}))

        await agent.new_session(cwd=str(world.project))
        # The response is returned and nothing has yielded since: the client has been sent
        # nothing, and a client that is sent a line now would drop it (zed#60199).
        assert client.updates == []

        await settle(agent)

        assert client.agent_messages() == ["early line"]
        # The commands went first: that is the update that shows the client takes the session.
        kinds = client.kinds()
        assert kinds.index("available_commands_update") < kinds.index("agent_message_chunk")

    async def test_the_held_lines_come_out_in_order_once(self, world: Any):
        def extension(pi: ExtensionAPI) -> None:
            for text in ("one", "two", "three"):
                pi.notify(text)

        agent, client = make_agent(world, extension)

        await agent.new_session(cwd=str(world.project))
        await settle(agent)
        await asyncio.sleep(0.05)

        assert client.agent_messages() == ["one", "two", "three"]

    async def test_a_line_sent_later_goes_out_at_once(self, world: Any):
        captured: dict[str, Any] = {}
        agent, client = make_agent(world, noisy(captured))
        await agent.new_session(cwd=str(world.project))
        await settle(agent)

        captured["pi"].notify("late line")
        await until(lambda: "late line" in client.agent_messages())

        assert client.agent_messages().count("late line") == 1

    async def test_a_line_is_one_agent_message_and_not_a_prompt_or_a_turn(self, world: Any):
        model = Model()
        agent, client = make_agent(world, noisy({}), model=model)

        created = await agent.new_session(cwd=str(world.project))
        await settle(agent)

        assert model.turns == 0
        assert client.agent_messages() == ["early line"]
        # And it is not part of the conversation: a session with only this has nothing in it.
        harness = agent._harnesses[created.session_id]
        assert (await harness.session.build_context()).messages == []

    async def test_a_session_closed_before_the_setup_got_nothing_and_leaves_nothing(
        self, world: Any
    ):
        agent, client = make_agent(world, noisy({}))

        created = await agent.new_session(cwd=str(world.project))
        await agent.close_session(session_id=created.session_id)
        await settle(agent)
        await asyncio.sleep(0.05)

        assert client.agent_messages() == []
        assert agent._held_notices == {}
        assert agent._notices_open == set()

    async def test_a_line_sent_after_the_session_was_closed_is_dropped(self, world: Any):
        captured: dict[str, Any] = {}
        agent, client = make_agent(world, noisy(captured))
        created = await agent.new_session(cwd=str(world.project))
        await settle(agent)
        client.updates.clear()

        await agent.close_session(session_id=created.session_id)
        captured["pi"].notify("too late")
        await asyncio.sleep(0.05)

        assert client.agent_messages() == []
        assert agent._held_notices == {}

    async def test_deleting_a_session_forgets_its_lines_too(self, world: Any):
        agent, _ = make_agent(world, noisy({}))
        created = await agent.new_session(cwd=str(world.project))
        await settle(agent)
        agent._held_notices[created.session_id] = ["stale"]  # what a race could leave behind

        await agent.close_session(session_id=created.session_id)

        assert created.session_id not in agent._held_notices
        assert created.session_id not in agent._notices_open

    async def test_opening_the_session_again_holds_the_lines_again(self, world: Any):
        # The client may not know the session yet (a retry after a failed load): the response
        # of this request is not sent, so the lines wait for the deferred setup again.
        count = {"n": 0}

        def extension(pi: ExtensionAPI) -> None:
            count["n"] += 1
            pi.register_command("probe", description="probe", handler=lambda args: None)
            pi.notify(f"hello {count['n']}")

        agent, client = make_agent(world, extension)
        created = await agent.new_session(cwd=str(world.project))
        await settle(agent)
        sid = created.session_id
        await agent.prompt(session_id=sid, prompt=[text_block("hi")])  # saves the session
        assert agent._notices_open == {sid}

        await agent.load_session(cwd=str(world.project), session_id=sid)
        # (Nothing has yielded since the response: the deferred setup has not run.)
        assert agent._notices_open == set()
        assert "hello 2" not in client.agent_messages()

        await settle(agent)
        assert client.agent_messages().count("hello 2") == 1
        assert agent._notices_open == {sid}


class TestNoticeProjection:
    def test_a_notice_is_one_agent_message(self):
        updates = list(project_event(NoticeEvent(text="MCP server files failed to start")))

        assert [u.session_update for u in updates] == ["agent_message_chunk"]
        assert updates[0].content.text == "MCP server files failed to start"

    @pytest.mark.parametrize("text", ["", "  \n\t"])
    def test_a_blank_notice_is_nothing(self, text: str):
        assert list(project_event(NoticeEvent(text=text))) == []


# -- the harness a new one replaces ----------------------------------------


class TestReopen:
    async def test_opening_an_open_session_again_closes_the_harness_it_replaces(self, world: Any):
        closed: list[int] = []
        made = {"n": 0}

        def extension(pi: ExtensionAPI) -> None:
            made["n"] += 1
            mine = made["n"]
            pi.register_cleanup(lambda: closed.append(mine))

        agent, _ = make_agent(world, extension)
        created = await agent.new_session(cwd=str(world.project))
        await settle(agent)
        sid = created.session_id
        await agent.prompt(session_id=sid, prompt=[text_block("hi")])  # saves the session
        first = agent._harnesses[sid]

        await agent.load_session(cwd=str(world.project), session_id=sid)
        await settle(agent)

        assert agent._harnesses[sid] is not first
        assert first.closed is True
        assert closed == [1]  # the cleanup of the first one ran; the second one is in use
        assert agent._harnesses[sid].closed is False

        await agent.close_session(session_id=sid)
        assert closed == [1, 2]

    async def test_the_session_still_answers_after_it_was_opened_again(self, world: Any):
        agent, client = make_agent(world)
        created = await agent.new_session(cwd=str(world.project))
        await settle(agent)
        sid = created.session_id
        await agent.prompt(session_id=sid, prompt=[text_block("one")])
        await agent.resume_session(cwd=str(world.project), session_id=sid)
        await settle(agent)

        result = await agent.prompt(session_id=sid, prompt=[text_block("two")])

        assert result.stop_reason == "end_turn"
        assert client.agent_messages().count("Hello from mock") == 2

    async def test_a_cleanup_that_fails_does_not_stop_the_new_session(
        self, world: Any, caplog: pytest.LogCaptureFixture
    ):
        def extension(pi: ExtensionAPI) -> None:
            def boom() -> None:
                raise RuntimeError("cleanup bug")

            pi.register_cleanup(boom)

        agent, _ = make_agent(world, extension)
        created = await agent.new_session(cwd=str(world.project))
        await settle(agent)
        sid = created.session_id
        await agent.prompt(session_id=sid, prompt=[text_block("hi")])

        with caplog.at_level(logging.WARNING):
            await agent.load_session(cwd=str(world.project), session_id=sid)
            await settle(agent)

        result = await agent.prompt(session_id=sid, prompt=[text_block("again")])
        assert result.stop_reason == "end_turn"


# -- turn barriers ---------------------------------------------------------


def with_barrier(release: asyncio.Event) -> Callable[[ExtensionAPI], None]:
    def extension(pi: ExtensionAPI) -> None:
        pi.add_turn_barrier(release.wait)

    return extension


class TestTurnBarrier:
    async def test_a_prompt_waits_for_the_barrier_and_then_runs(self, world: Any):
        model, release = Model(), asyncio.Event()
        agent, client = make_agent(world, with_barrier(release), model=model)
        created = await agent.new_session(cwd=str(world.project))
        await settle(agent)

        prompt = asyncio.create_task(
            agent.prompt(session_id=created.session_id, prompt=[text_block("hi")])
        )
        await asyncio.sleep(0.1)
        assert not prompt.done() and model.turns == 0

        release.set()
        result = await asyncio.wait_for(prompt, WAIT)

        assert result.stop_reason == "end_turn" and model.turns == 1
        assert "Hello from mock" in client.agent_messages()

    async def test_session_cancel_ends_the_wait(self, world: Any):
        model, release = Model(), asyncio.Event()
        agent, _ = make_agent(world, with_barrier(release), model=model)
        created = await agent.new_session(cwd=str(world.project))
        await settle(agent)
        sid = created.session_id
        harness = agent._harnesses[sid]

        prompt = asyncio.create_task(agent.prompt(session_id=sid, prompt=[text_block("hi")]))
        await asyncio.sleep(0.1)
        await agent.cancel(session_id=sid)
        result = await asyncio.wait_for(prompt, WAIT)

        assert result.stop_reason == "cancelled"
        assert model.turns == 0  # the question never reached the model
        assert harness.phase == "idle"  # and the harness was not left busy

    async def test_the_barrier_that_was_cancelled_is_still_usable_for_the_next_prompt(
        self, world: Any
    ):
        model, release = Model(), asyncio.Event()
        agent, _ = make_agent(world, with_barrier(release), model=model)
        created = await agent.new_session(cwd=str(world.project))
        await settle(agent)
        sid = created.session_id

        first = asyncio.create_task(agent.prompt(session_id=sid, prompt=[text_block("one")]))
        await asyncio.sleep(0.1)
        await agent.cancel(session_id=sid)
        assert (await asyncio.wait_for(first, WAIT)).stop_reason == "cancelled"

        release.set()
        second = await asyncio.wait_for(
            agent.prompt(session_id=sid, prompt=[text_block("two")]), WAIT
        )

        assert second.stop_reason == "end_turn" and model.turns == 1

    async def test_closing_the_session_ends_the_wait(self, world: Any):
        model, release = Model(), asyncio.Event()
        agent, _ = make_agent(world, with_barrier(release), model=model)
        created = await agent.new_session(cwd=str(world.project))
        await settle(agent)
        sid = created.session_id

        prompt = asyncio.create_task(agent.prompt(session_id=sid, prompt=[text_block("hi")]))
        await asyncio.sleep(0.1)
        await agent.close_session(session_id=sid)
        result = await asyncio.wait_for(prompt, WAIT)

        assert result.stop_reason == "cancelled" and model.turns == 0

    async def test_a_barrier_that_fails_does_not_fail_the_prompt(self, world: Any):
        def extension(pi: ExtensionAPI) -> None:
            async def broken() -> None:
                raise RuntimeError("barrier bug")

            pi.add_turn_barrier(broken)

        agent, client = make_agent(world, extension)
        created = await agent.new_session(cwd=str(world.project))
        await settle(agent)

        result = await agent.prompt(session_id=created.session_id, prompt=[text_block("hi")])

        assert result.stop_reason == "end_turn"
        assert "Hello from mock" in client.agent_messages()

    async def test_without_a_barrier_a_prompt_does_not_wait(self, world: Any):
        model = Model()
        agent, _ = make_agent(world, model=model)
        created = await agent.new_session(cwd=str(world.project))
        await settle(agent)

        result = await asyncio.wait_for(
            agent.prompt(session_id=created.session_id, prompt=[text_block("hi")]), WAIT
        )

        assert result.stop_reason == "end_turn" and model.turns == 1
