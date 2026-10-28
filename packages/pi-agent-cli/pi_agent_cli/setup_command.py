"""``pi-agent-cli --setup``: choose a model and save its API key, in a terminal.

An ACP client that can run a terminal login (Zed can) is offered this command when no model is
set (``agent.py``). It also works on its own, from any shell.

What it writes, and where:

- ``<home>/agent.toml``: a ``[model]`` table with the provider, the model id, an optional base URL
  and the *name* of the variable that holds the key. A missing file is created. A file that has no
  ``[model]`` table gets one at its end, and nothing else in it changes. A file that has one is
  never changed.
- ``<home>/local.env``: the line ``NAME=key``, put in by replacing the file as a whole. Other
  lines stay as they are. The file is private to the user on POSIX (mode 0600); on Windows it has
  the rights of the user folder.

The key is read without echo. It is never printed, logged or written to ``agent.toml``.
"""

from __future__ import annotations

import contextlib
import getpass
import importlib.util
import json
import os
import re
import shutil
import sys
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from pi_agent_cli.config import (
    MOCK_PROVIDER,
    agent_config_path,
    legacy_config_path,
    load_config,
    local_env_line_key,
    pi_home,
    read_local_env,
)

# The id of the terminal login that ``initialize`` offers, and the arguments that start it.
SETUP_METHOD_ID = "pi-setup"
SETUP_ARGS = ("--setup",)

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class Preset:
    """A provider that the agent can use once its LangChain package is installed."""

    label: str
    provider: str
    key_env: str  # the usual name of the variable that holds the key
    extra: str  # the extra of ``pi-agent-core-lc`` that installs the package
    module: str  # what the package imports as
    asks_base_url: bool


PRESETS = (
    Preset(
        "OpenAI, or an OpenAI-compatible endpoint",
        "openai",
        "OPENAI_API_KEY",
        "openai",
        "langchain_openai",
        True,
    ),
    Preset(
        "Anthropic", "anthropic", "ANTHROPIC_API_KEY", "anthropic", "langchain_anthropic", False
    ),
    Preset(
        "DeepSeek, or a DeepSeek-style gateway",
        "deepseek",
        "DEEPSEEK_API_KEY",
        "deepseek",
        "langchain_deepseek",
        True,
    ),
)


class SetupError(Exception):
    """A failure that the user can read about and fix (the text is shown as it is)."""


@dataclass
class SetupIO:
    """What the dialog talks to. The tests give it a script instead of a terminal."""

    ask: Callable[[str], str]
    ask_secret: Callable[[str], str]
    out: TextIO
    err: TextIO
    interactive: bool

    @classmethod
    def for_terminal(cls) -> SetupIO:
        return cls(
            ask=input,
            ask_secret=getpass.getpass,
            out=sys.stdout,
            err=sys.stderr,
            interactive=sys.stdin.isatty() and sys.stdout.isatty(),
        )

    def say(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)


# --- the files ------------------------------------------------------------------------------


def _toml_string(value: str) -> str:
    # A JSON string is a TOML basic string for the text that the dialog lets through.
    return json.dumps(value, ensure_ascii=False)


def model_table(provider: str, model_id: str, base_url: str | None, key_env: str) -> str:
    """The ``[model]`` table, as text. It is parsed again before it is returned."""
    lines = ["[model]", f"provider = {_toml_string(provider)}", f"id = {_toml_string(model_id)}"]
    if base_url:
        lines.append(f"base_url = {_toml_string(base_url)}")
    lines.append(f"api_key_env = {_toml_string(key_env)}")
    text = "\n".join(lines) + "\n"
    expected = {"provider": provider, "id": model_id, "api_key_env": key_env}
    if base_url:
        expected["base_url"] = base_url
    try:
        parsed = tomllib.loads(text).get("model")
    except tomllib.TOMLDecodeError:
        parsed = None
    if parsed != expected:
        raise SetupError("the model table did not survive a round trip; nothing was written")
    return text


_NEW_FILE_HEADER = (
    "# Written by `pi-agent-cli --setup`.\n"
    "# The API key is not in this file: the variable named by api_key_env holds it\n"
    "# (see local.env in this folder). All settings: agent.example.toml.\n\n"
)


def _read_raw(path: Path) -> str:
    """The text of ``path`` with its line ends as they are (``Path.read_text`` changes them)."""
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _line_end(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def check_model_table_can_be_added(path: Path) -> None:
    """Raise ``SetupError`` unless ``path`` is missing or a file that has no ``[model]`` table."""
    if not path.is_file():
        return
    try:
        data = tomllib.loads(_read_raw(path))
    except ValueError as exc:  # a TOML syntax error, or text that is not UTF-8
        raise SetupError(f"cannot read {path}: {exc}. Fix the file first.") from exc
    if "model" in data:
        raise SetupError(
            f"{path} has a [model] table that sets no provider. "
            "Edit it by hand, or remove it and run --setup again."
        )


def add_model_table(path: Path, table: str) -> None:
    """Create ``path`` with ``table``, or add ``table`` to the end of a file with no ``[model]``.

    The text that is already in the file is kept to the byte, line ends included.
    """
    check_model_table_can_be_added(path)
    if not path.is_file():
        _write_atomic(path, _NEW_FILE_HEADER + table, private=False)
        return
    old = _read_raw(path)
    eol = _line_end(old)
    if not old or old.endswith(eol * 2):
        separator = ""
    elif old.endswith(eol):
        separator = eol
    else:
        separator = eol * 2
    _write_atomic(path, old + separator + table.replace("\n", eol), private=False)


def write_local_env(home: Path, name: str, value: str) -> Path:
    """Set ``name=value`` in ``<home>/local.env``; the other lines stay as they are."""
    path = home / "local.env"
    old = _read_raw(path) if path.is_file() else ""
    eol = _line_end(old)
    lines: list[str] = []
    placed = False
    for line in old.splitlines():
        if local_env_line_key(line) == name:
            if not placed:  # the first line of a key is the one that counts; later ones go
                lines.append(f"{name}={value}")
                placed = True
            continue
        lines.append(line)
    if not placed:
        lines.append(f"{name}={value}")
    _write_atomic(path, eol.join(lines) + eol, private=True)
    return path


def _write_atomic(path: Path, text: str, *, private: bool) -> None:
    """Put ``text`` in ``path`` by writing a file beside it and renaming that over it.

    The line ends of ``text`` are written as they are. A symbolic link (a dotfile manager makes
    them) is followed, so the link stays a link. A file that is replaced keeps its mode, unless it
    is to be private.
    """
    if path.is_symlink():
        path = Path(os.path.realpath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(temp, flags, 0o600 if private else 0o666)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            if private and os.name == "posix":
                os.fchmod(handle.fileno(), 0o600)  # a leftover temp file keeps its old mode
            handle.write(text)
        if not private and path.exists():
            shutil.copymode(path, temp)
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise


# --- the questions --------------------------------------------------------------------------


def _bad_text(value: str) -> bool:
    """Is there a space, a control character, or a character that UTF-8 cannot write?"""
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return True
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:  # a byte of the terminal that did not decode (a lone surrogate)
        return True
    return False


def _ask_until(ask: Callable[[], str], check: Callable[[str], str | None], io: SetupIO) -> str:
    """Ask again until ``check`` has no complaint about the (stripped) answer."""
    while True:
        answer = ask().strip()
        problem = check(answer)
        if problem is None:
            return answer
        io.say(problem)


def _find_preset(answer: str) -> Preset | None:
    if answer.isdigit() and 1 <= int(answer) <= len(PRESETS):
        return PRESETS[int(answer) - 1]
    return next((p for p in PRESETS if p.provider == answer.casefold()), None)


def _ask_preset(io: SetupIO) -> Preset:
    io.say("Which provider do you use?")
    for number, preset in enumerate(PRESETS, start=1):
        io.say(f"  {number}. {preset.label}")

    def check(answer: str) -> str | None:
        return None if _find_preset(answer) else f"Type a number from 1 to {len(PRESETS)}."

    answer = _ask_until(lambda: io.ask("Number: "), check, io)
    found = _find_preset(answer)
    assert found is not None  # check() has let only such an answer through
    return found


def _ask_model_id(io: SetupIO) -> str:
    def check(answer: str) -> str | None:
        if not answer or _bad_text(answer):
            return "A model id has text and no spaces. Try again."
        return None

    return _ask_until(lambda: io.ask("Model id, as the provider names it: "), check, io)


def _ask_base_url(io: SetupIO) -> str | None:
    def check(answer: str) -> str | None:
        if not answer:
            return None
        if _bad_text(answer) or not answer.startswith(("http://", "https://")):
            return "A base URL starts with http:// or https:// and has no spaces. Try again."
        return None

    answer = _ask_until(lambda: io.ask("Base URL (Enter: the provider's own): "), check, io)
    return answer or None


def _ask_key_env(io: SetupIO, default: str) -> str:
    def check(answer: str) -> str | None:
        if _ENV_NAME.fullmatch(answer):
            return None
        return "A variable name has letters, digits and _ and does not start with a digit."

    prompt = f"Variable that holds the key (Enter: {default}): "
    return _ask_until(lambda: io.ask(prompt).strip() or default, check, io)


def _ask_key(io: SetupIO, name: str) -> str:
    def check(answer: str) -> str | None:
        if not answer:
            return "The key is empty. Try again."
        quoted = len(answer) >= 2 and answer[0] == answer[-1] and answer[0] in "\"'"
        if _bad_text(answer) or quoted:
            return "A key has no spaces and no quote marks at its ends. Try again."
        return None

    prompt = f"API key for {name} (what you type is hidden): "
    return _ask_until(lambda: io.ask_secret(prompt), check, io)


def _confirm(io: SetupIO, question: str) -> bool:
    return io.ask(f"{question} [y/N]: ").strip().casefold() in {"y", "yes"}


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


# --- the dialog -----------------------------------------------------------------------------


def run_setup(
    home: Path | str | None = None,
    *,
    io: SetupIO | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Run the dialog. Exit status: 0 done or nothing to do, 1 failed or stopped, 2 no terminal."""
    io = io or SetupIO.for_terminal()
    if not io.interactive:
        print(
            "error: --setup asks questions, so it needs a terminal. Run it in a terminal window, "
            "or write agent.toml and local.env by hand (see agent.example.toml).",
            file=io.err,
            flush=True,
        )
        return 2
    try:
        return _dialog(pi_home(home), io, os.environ if environ is None else environ)
    except SetupError as exc:
        print(f"error: {exc}", file=io.err, flush=True)
        return 1
    except (EOFError, KeyboardInterrupt):
        io.say("\nStopped. Nothing was saved.")
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=io.err, flush=True)
        return 1


def _dialog(home: Path, io: SetupIO, environ: Mapping[str, str]) -> int:
    config_path = agent_config_path(home)
    legacy_path = legacy_config_path(home)
    in_use = config_path if config_path.is_file() else legacy_path
    try:
        config = load_config(home)
    except ValueError as exc:  # a TOML syntax error, or text that is not UTF-8
        raise SetupError(f"cannot read {in_use}: {exc}. Fix the file first.") from exc
    try:
        local_env = read_local_env(home)
    except ValueError as exc:
        raise SetupError(f"cannot read {home / 'local.env'}: {exc}. Fix the file first.") from exc
    # The real environment wins over local.env, as it does when the agent starts.
    values = {**local_env, **environ}

    io.say("pi-python setup")
    io.say(f"Home folder: {home}")
    io.say()

    key_env = config.api_key_env
    replaced_key = False
    preset: Preset | None

    if config.provider.casefold() == MOCK_PROVIDER:
        if not config_path.is_file() and legacy_path.is_file():
            raise SetupError(
                f"your settings are in the old file {legacy_path}, which the agent reads only "
                f"while {config_path.name} does not exist. Add a [model] table to it, or move it "
                f"to {config_path}, then run --setup again."
            )
        check_model_table_can_be_added(config_path)
        io.say("No model is set yet.")
        preset = _ask_preset(io)
        provider = preset.provider
        model_id = _ask_model_id(io)
        base_url = _ask_base_url(io) if preset.asks_base_url else None
        key_env = _ask_key_env(io, preset.key_env)
        table = model_table(provider, model_id, base_url, key_env)
        key = _ask_key(io, key_env)
        add_model_table(config_path, table)
    else:
        provider, model_id = config.provider, config.model_id
        preset = next((p for p in PRESETS if p.provider == provider.casefold()), None)
        io.say(f"The model is {provider} / {model_id} (from {in_use}).")
        if not key_env:
            io.say(
                "It has no api_key_env, so the provider reads the key from its own variable. "
                "Nothing to set up."
            )
            return 0
        if values.get(key_env):
            io.say(f"The variable {key_env} has a value.")
            if not _confirm(io, f"Replace the key in {key_env}?"):
                io.say("Nothing changed.")
                return 0
            replaced_key = True
        else:
            io.say(f"The variable {key_env} has no value.")
        key = _ask_key(io, key_env)

    assert key_env is not None
    env_path = write_local_env(home, key_env, key)

    io.say()
    io.say("Saved.")
    io.say(f"  Model: {provider} / {model_id}  ({config_path})")
    rights = (
        "only your user can read it"
        if os.name == "posix"
        else "it has the rights of your user folder"
    )
    io.say(f"  Key:   {key_env} in {env_path}  ({rights})")
    io.say("Start a new session in your agent client.")
    if replaced_key:
        io.say("An agent that is running keeps the old key until it is started again.")
    if preset is not None and not _installed(preset.module):
        io.say()
        io.say(f"Note: the package for {preset.provider} is not installed in this environment.")
        io.say(
            f'  uvx (for example in Zed): add "--with", "pi-agent-core-lc[{preset.extra}]" '
            'to the arguments, before "pi-agent-cli".'
        )
        io.say(f'  pip: pip install "pi-agent-core-lc[{preset.extra}]"')
    return 0
