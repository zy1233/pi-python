"""The terminal login in ACP: what ``initialize`` offers, who is sent to it, and how it ends.

A client that can run a terminal login is offered ``pi-agent-cli --setup`` as an authentication
method, and a new session that could not work (no model, no key) is answered with
``auth_required``. A client that cannot run one sees no change. The SDK drops an item of
``authMethods`` that does not validate, without a word, so the wire tests read the raw JSON.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from acp import RequestError
from acp.schema import AuthenticateResponse, ClientCapabilities

from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.auth_methods import (
    SETUP_METHOD_ID,
    SETUP_METHOD_NAME,
    terminal_auth_form,
)
from pi_agent_cli.config import CliConfig, config_problem, no_model_message
from pi_agent_cli.setup_command import SetupIO, run_setup
from pi_agent_core.tests.mock_stream import mock_text_stream

KEY = "sk-test-0123456789"

# What clients announce in ``initialize`` (wire form).
FS = {"readTextFile": True, "writeTextFile": True}
ZED = {"fs": FS, "terminal": True, "auth": {"terminal": True}, "_meta": {"terminal-auth": True}}
FIRST_CLASS_ONLY = {"fs": FS, "terminal": True, "auth": {"terminal": True}}
REGISTRY_VALIDATOR = {
    "fs": FS,
    "terminal": True,
    "_meta": {"terminal_output": True, "terminal-auth": True},
}
ZYPI = {"fs": FS, "terminal": True, "_meta": {}}
NOTHING: dict[str, Any] = {}


def caps(wire: dict[str, Any]) -> ClientCapabilities:
    return ClientCapabilities.model_validate(wire)


class _Recorder:
    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        return None


def _env(monkeypatch: pytest.MonkeyPatch, home: Path, *, mock: bool = False) -> None:
    if mock:
        monkeypatch.setenv("PI_USE_MOCK", "1")
    else:
        monkeypatch.delenv("PI_USE_MOCK", raising=False)
    monkeypatch.setenv("PI_HOME", str(home))


async def _agent(home: Path, wire: dict[str, Any] | None) -> PiAcpAgent:
    """An agent that reads its settings from ``home``, after an ``initialize`` like a client's."""
    agent = PiAcpAgent(home=home)
    agent.on_connect(_Recorder())  # type: ignore[arg-type]
    await agent.initialize(
        protocol_version=1, client_capabilities=None if wire is None else caps(wire)
    )
    return agent


def _scripted_setup(answers: list[str], secrets: list[str]) -> SetupIO:
    answers, secrets = list(answers), list(secrets)

    def take(queue: list[str], _prompt: str) -> str:
        if not queue:
            raise EOFError
        return queue.pop(0)

    return SetupIO(
        ask=lambda prompt: take(answers, prompt),
        ask_secret=lambda prompt: take(secrets, prompt),
        out=io.StringIO(),
        err=io.StringIO(),
        interactive=True,
    )


def _run_setup(home: Path) -> None:
    code = run_setup(
        home, io=_scripted_setup(["1", "gpt-x", "", ""], [KEY]), environ={}
    )  # OpenAI, model gpt-x, the provider's own URL, the usual variable name
    assert code == 0


def _forget_openai_key_afterwards(monkeypatch: pytest.MonkeyPatch) -> None:
    """``load_local_env`` writes to ``os.environ`` itself; this makes the test undo it."""
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")
    monkeypatch.delenv("OPENAI_API_KEY")


# --- which form a client gets ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("wire", "form"),
    [
        (ZED, "terminal"),
        (FIRST_CLASS_ONLY, "terminal"),
        (REGISTRY_VALIDATOR, "legacy"),
        (ZYPI, None),
        (NOTHING, None),
        ({"_meta": {"terminal-auth": "yes"}}, None),
        ({"_meta": {"terminal-auth": False}}, None),
        ({"auth": {"terminal": False}, "_meta": {"terminal-auth": False}}, None),
    ],
    ids=["zed", "first-class-only", "registry-validator", "zypi", "nothing", "yes", "false", "off"],
)
def test_the_form_follows_what_the_client_announces(wire, form):
    assert terminal_auth_form(caps(wire)) == form


def test_a_client_that_sends_no_capabilities_gets_no_form():
    assert terminal_auth_form(None) is None


# --- initialize -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_client_with_terminal_auth_gets_a_terminal_method(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    agent = PiAcpAgent(home=tmp_path)
    response = await agent.initialize(protocol_version=1, client_capabilities=caps(ZED))
    (method,) = response.model_dump(by_alias=True, exclude_none=True)["authMethods"]
    assert method["type"] == "terminal"
    assert method["id"] == SETUP_METHOD_ID
    assert method["name"] == SETUP_METHOD_NAME
    assert method["args"] == ["--setup"]
    assert "_meta" not in method  # the first-class form needs no command of its own


@pytest.mark.asyncio
async def test_an_older_client_gets_the_command_in_meta(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    agent = PiAcpAgent(home=tmp_path)
    response = await agent.initialize(
        protocol_version=1, client_capabilities=caps(REGISTRY_VALIDATOR)
    )
    (method,) = response.model_dump(by_alias=True, exclude_none=True)["authMethods"]
    assert method["id"] == SETUP_METHOD_ID
    assert "type" not in method  # the default type, "agent": what an older client knows
    login = method["_meta"]["terminal-auth"]
    assert login["command"] == sys.executable
    assert login["args"] == ["-m", "pi_agent_cli", "--setup"]
    assert login["env"] == {"PI_HOME": str(tmp_path)}  # the terminal has no env of the agent
    assert login["label"] == SETUP_METHOD_NAME


@pytest.mark.asyncio
async def test_a_frozen_build_gives_an_older_client_its_own_program_without_dash_m(
    monkeypatch, tmp_path
):
    """Frozen, ``sys.executable`` is the agent itself: it has no ``-m``, only its own flags."""
    _env(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    agent = PiAcpAgent(home=tmp_path)
    response = await agent.initialize(
        protocol_version=1, client_capabilities=caps(REGISTRY_VALIDATOR)
    )
    (method,) = response.model_dump(by_alias=True, exclude_none=True)["authMethods"]
    login = method["_meta"]["terminal-auth"]
    assert login["command"] == sys.executable
    assert login["args"] == ["--setup"]
    assert login["env"] == {"PI_HOME": str(tmp_path)}


@pytest.mark.asyncio
@pytest.mark.parametrize("wire", [ZYPI, NOTHING, None], ids=["zypi", "nothing", "no-capabilities"])
async def test_a_client_that_cannot_run_a_terminal_login_gets_no_method(
    monkeypatch, tmp_path, wire
):
    _env(monkeypatch, tmp_path)
    agent = PiAcpAgent(home=tmp_path)
    response = await agent.initialize(
        protocol_version=1, client_capabilities=None if wire is None else caps(wire)
    )
    assert response.auth_methods == []


@pytest.mark.asyncio
async def test_an_agent_with_its_own_settings_offers_nothing(tmp_path):
    agent = PiAcpAgent(stream_fn=mock_text_stream, home=tmp_path, config=CliConfig())
    response = await agent.initialize(protocol_version=1, client_capabilities=caps(ZED))
    assert response.auth_methods == []
    with pytest.raises(RequestError) as caught:
        await agent.authenticate(SETUP_METHOD_ID)
    assert caught.value.code == -32602


# --- the raw answer on the wire -------------------------------------------------------------


def _initialize_over_stdio(home: Path, wire: dict[str, Any]) -> dict[str, Any]:
    """Start the agent, send ``initialize`` as raw JSON, and return the raw ``result``."""
    env = {k: v for k, v in os.environ.items() if k not in {"PI_USE_MOCK", "PI_HOME"}}
    env["PI_HOME"] = str(home)
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": 1, "clientCapabilities": wire},
    }
    done = subprocess.run(
        [sys.executable, "-m", "pi_agent_cli"],
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    first_line = done.stdout.splitlines()[0]
    reply = json.loads(first_line)
    assert reply["id"] == 1, done.stderr
    return reply["result"]


@pytest.mark.parametrize(
    ("wire", "kind"),
    [(ZED, "terminal"), (REGISTRY_VALIDATOR, "legacy"), (ZYPI, None)],
    ids=["zed", "registry-validator", "zypi"],
)
def test_the_method_is_on_the_wire(tmp_path, wire, kind):
    result = _initialize_over_stdio(tmp_path, wire)
    methods = result["authMethods"]
    if kind is None:
        assert methods == []
        return
    # A method that the SDK found invalid would be missing here, and the list would be empty.
    assert len(methods) == 1, result
    (method,) = methods
    assert method["id"] == SETUP_METHOD_ID
    if kind == "terminal":
        assert method["type"] == "terminal"
        assert method["args"] == ["--setup"]
    else:
        assert method["_meta"]["terminal-auth"]["args"][-1] == "--setup"


# --- session/new ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_new_session_without_a_model_sends_a_capable_client_to_the_login(
    monkeypatch, tmp_path
):
    _env(monkeypatch, tmp_path)
    agent = await _agent(tmp_path, ZED)
    with pytest.raises(RequestError) as caught:
        await agent.new_session(cwd=str(tmp_path))
    assert caught.value.code == -32000
    message = str(caught.value)
    assert message.startswith("No model is set.")
    assert SETUP_METHOD_NAME in message
    assert caught.value.data == {"authMethods": [SETUP_METHOD_ID]}
    assert agent._harnesses == {}  # no session was made
    assert list((tmp_path / "sessions").iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("wire", [ZYPI, NOTHING, None], ids=["zypi", "nothing", "no-capabilities"])
async def test_a_client_that_cannot_run_the_login_still_gets_its_session(
    monkeypatch, tmp_path, wire
):
    _env(monkeypatch, tmp_path)
    agent = await _agent(tmp_path, wire)
    session = await agent.new_session(cwd=str(tmp_path))
    assert session.session_id  # the first turn says what is missing (test_first_run_errors.py)


@pytest.mark.asyncio
async def test_the_scripted_llm_needs_no_login(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, mock=True)
    agent = await _agent(tmp_path, ZED)
    session = await agent.new_session(cwd=str(tmp_path))
    assert session.session_id


@pytest.mark.asyncio
async def test_a_missing_key_counts_unless_the_providers_own_variable_has_one(
    monkeypatch, tmp_path
):
    _env(monkeypatch, tmp_path)
    (tmp_path / "agent.toml").write_text(
        '[model]\nprovider = "deepseek"\nid = "ds-x"\napi_key_env = "MY_KEY"\n', encoding="utf-8"
    )
    monkeypatch.delenv("MY_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    agent = await _agent(tmp_path, ZED)

    with pytest.raises(RequestError) as caught:
        await agent.new_session(cwd=str(tmp_path))
    assert caught.value.code == -32000
    assert str(caught.value).startswith("The variable MY_KEY has no value.")

    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-the-sdk-variable")
    assert (await agent.new_session(cwd=str(tmp_path))).session_id


@pytest.mark.asyncio
async def test_a_model_with_its_key_opens_a_session(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    (tmp_path / "agent.toml").write_text(
        '[model]\nprovider = "deepseek"\nid = "ds-x"\napi_key_env = "MY_KEY"\n', encoding="utf-8"
    )
    monkeypatch.setenv("MY_KEY", KEY)
    agent = await _agent(tmp_path, ZED)
    assert (await agent.new_session(cwd=str(tmp_path))).session_id


@pytest.mark.asyncio
async def test_the_next_session_uses_what_setup_wrote_while_the_agent_was_up(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    _forget_openai_key_afterwards(monkeypatch)
    agent = await _agent(tmp_path, ZED)
    with pytest.raises(RequestError):
        await agent.new_session(cwd=str(tmp_path))

    _run_setup(tmp_path)  # what the client's terminal does

    session = await agent.new_session(cwd=str(tmp_path))
    assert session.session_id
    assert agent._config.provider == "openai"
    assert agent._config.model_id == "gpt-x"
    assert os.environ["OPENAI_API_KEY"] == KEY
    assert config_problem(agent._config) is None


@pytest.mark.asyncio
async def test_an_agent_that_works_is_not_read_again(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    (tmp_path / "agent.toml").write_text(
        '[model]\nprovider = "deepseek"\nid = "first"\napi_key_env = "MY_KEY"\n', encoding="utf-8"
    )
    monkeypatch.setenv("MY_KEY", KEY)
    agent = await _agent(tmp_path, ZED)
    (tmp_path / "agent.toml").write_text(
        '[model]\nprovider = "deepseek"\nid = "second"\napi_key_env = "MY_KEY"\n', encoding="utf-8"
    )
    await agent.new_session(cwd=str(tmp_path))
    assert agent._config.model_id == "first"


@pytest.mark.asyncio
async def test_resume_opens_a_session_even_when_the_login_is_due(monkeypatch, tmp_path):
    # Old history can be read without a model; only a new session is sent to the login.
    _env(monkeypatch, tmp_path)
    agent = await _agent(tmp_path, ZED)
    response = await agent.resume_session(session_id=str(uuid.uuid4()), cwd=str(tmp_path))
    assert response is not None


# --- authenticate ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_refuses_a_method_that_was_not_offered(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    agent = await _agent(tmp_path, ZED)
    with pytest.raises(RequestError) as caught:
        await agent.authenticate("something-else")
    assert caught.value.code == -32602


@pytest.mark.asyncio
async def test_authenticate_says_the_login_is_not_done_while_nothing_is_set(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    agent = await _agent(tmp_path, ZED)
    with pytest.raises(RequestError) as caught:
        await agent.authenticate(SETUP_METHOD_ID)
    assert caught.value.code == -32000


@pytest.mark.asyncio
async def test_authenticate_succeeds_after_setup(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    _forget_openai_key_afterwards(monkeypatch)
    agent = await _agent(tmp_path, ZED)
    _run_setup(tmp_path)
    response = await agent.authenticate(SETUP_METHOD_ID)
    assert isinstance(response, AuthenticateResponse)
    assert agent._config.provider == "openai"


# --- the helper -----------------------------------------------------------------------------


def test_config_problem_reads_the_files_and_not_the_network():
    assert config_problem(CliConfig(), {}) == "No model is set."
    assert no_model_message().startswith("No model is set.")
    assert config_problem(CliConfig(), {"PI_USE_MOCK": "1"}) is None
    openai = CliConfig(provider="openai", api_key_env="K")
    assert config_problem(openai, {}) == "The variable K has no value."
    assert config_problem(openai, {"K": "x"}) is None
    assert config_problem(openai, {"OPENAI_API_KEY": "x"}) is None  # the SDK's own variable
    assert config_problem(CliConfig(provider="openai"), {}) is None  # no variable is named
    other = CliConfig(provider="somewhere-else", api_key_env="K")
    assert config_problem(other, {"OPENAI_API_KEY": "x"}) == "The variable K has no value."
