"""The terminal login that ``initialize`` offers, and the error that sends a client to it.

``pi-agent-cli --setup`` (``setup_command.py``) asks for a model and an API key in a terminal. An
ACP client that can run a terminal login is offered it as an authentication method, and a new
session that cannot work (no model, no key) is answered with ``auth_required``, so the client
shows the method. A client that cannot run it gets no method and no error: the turn then says
what is missing (``factory.py``).

What a client announces decides the form of the method:

- ``clientCapabilities.auth.terminal = true``: a method of type ``terminal``. The client runs the
  command that started the agent, with ``args`` added to its arguments.
- ``clientCapabilities._meta["terminal-auth"] = true`` and nothing more (older clients): a method
  of the default type ``agent`` that has ``_meta["terminal-auth"]``, the command to run.
- Neither (the TUI ``zypi`` is one of these): no method.

The SDK drops an item of ``authMethods`` that does not validate, and says nothing. A plain
``AuthMethodTerminal`` has no ``type``, so it is not on the wire at all; ``TerminalAuthMethod``
has it. The tests read the raw JSON of the answer for that reason.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

from acp import RequestError
from acp.schema import AuthMethodAgent, ClientCapabilities, TerminalAuthMethod

from pi_agent_core.frozen import is_frozen

# The id of the terminal login, and the arguments that make the agent command run it.
SETUP_METHOD_ID = "pi-setup"
SETUP_ARGS = ("--setup",)
SETUP_METHOD_NAME = "Set up model and API key"
_SETUP_METHOD_DESCRIPTION = "Choose a model and save its API key. It asks in a terminal."

TerminalAuthForm = Literal["terminal", "legacy"]


def terminal_auth_form(capabilities: ClientCapabilities | None) -> TerminalAuthForm | None:
    """The form of terminal login that the client says it can run (``None``: none)."""
    if capabilities is None:
        return None
    if capabilities.auth is not None and capabilities.auth.terminal is True:
        return "terminal"
    if (capabilities.field_meta or {}).get("terminal-auth") is True:
        return "legacy"
    return None


def _legacy_command() -> tuple[str, list[str]]:
    """The command that runs the login without the client's help: a program and its arguments.

    A frozen build is one program: ``sys.executable`` is the agent itself and there is no
    ``-m``. Anywhere else the interpreter runs the module.
    """
    if is_frozen():
        return sys.executable, [*SETUP_ARGS]
    return sys.executable, ["-m", "pi_agent_cli", *SETUP_ARGS]


def setup_auth_method(
    capabilities: ClientCapabilities | None, home: Path
) -> TerminalAuthMethod | AuthMethodAgent | None:
    """The method to put in ``authMethods`` for this client (``None``: it cannot run one)."""
    form = terminal_auth_form(capabilities)
    if form == "terminal":
        return TerminalAuthMethod(
            type="terminal",
            id=SETUP_METHOD_ID,
            name=SETUP_METHOD_NAME,
            description=_SETUP_METHOD_DESCRIPTION,
            args=list(SETUP_ARGS),
            env={},
        )
    if form == "legacy":
        # An older client cannot add arguments to its own command for the agent, so the whole
        # command is given, and the home folder with it: the terminal does not have the
        # environment that the agent was started with.
        command, command_args = _legacy_command()
        return AuthMethodAgent(
            id=SETUP_METHOD_ID,
            name=SETUP_METHOD_NAME,
            description=_SETUP_METHOD_DESCRIPTION,
            field_meta={
                "terminal-auth": {
                    "label": SETUP_METHOD_NAME,
                    "command": command,
                    "args": command_args,
                    "env": {"PI_HOME": str(home)},
                }
            },
        )
    return None


def auth_required_error(problem: str) -> RequestError:
    """The answer to a new session that cannot work. ``problem`` is a sentence from the config."""
    return RequestError(
        -32000,
        f'{problem} Choose "{SETUP_METHOD_NAME}" to set it up.',
        {"authMethods": [SETUP_METHOD_ID]},
    )
