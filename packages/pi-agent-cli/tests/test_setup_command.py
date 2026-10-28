"""``pi-agent-cli --setup``: the dialog, the files it writes, and the flag."""

from __future__ import annotations

import io
import os
import stat
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pi_agent_cli import setup_command
from pi_agent_cli.config import load_config, read_local_env
from pi_agent_cli.setup_command import (
    SetupError,
    SetupIO,
    add_model_table,
    model_table,
    run_setup,
    write_local_env,
)

KEY = "sk-test-0123456789"
MODEL = b'[model]\nprovider = "deepseek"\nid = "ds-x"\napi_key_env = "MY_KEY"\n'
OLD_FILE = 'permission = "auto"\n\n[agent]\ncommand = "python -m pi_agent_cli"\n'
posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes and links")


@dataclass
class Terminal:
    """A script in place of a person: answers, hidden answers, and a record of what was shown."""

    answers: list[str] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)
    interactive: bool = True
    on_empty: type[BaseException] = EOFError
    prompts: list[str] = field(default_factory=list)
    out: io.StringIO = field(default_factory=io.StringIO)
    err: io.StringIO = field(default_factory=io.StringIO)

    def setup_io(self) -> SetupIO:
        def take(queue: list[str], prompt: str) -> str:
            self.prompts.append(prompt)
            if not queue:
                raise self.on_empty
            return queue.pop(0)

        return SetupIO(
            ask=lambda prompt: take(self.answers, prompt),
            ask_secret=lambda prompt: take(self.secrets, prompt),
            out=self.out,
            err=self.err,
            interactive=self.interactive,
        )

    def shown(self) -> str:
        """Everything a person could see or scroll back to."""
        return self.out.getvalue() + self.err.getvalue() + "".join(self.prompts)


def run(
    home: Path,
    *,
    answers: list[str] | None = None,
    secrets: list[str] | None = None,
    environ: dict[str, str] | None = None,
    **terminal: object,
) -> tuple[int, Terminal]:
    term = Terminal(answers=list(answers or []), secrets=list(secrets or []), **terminal)  # type: ignore[arg-type]
    code = run_setup(home, io=term.setup_io(), environ={} if environ is None else environ)
    return code, term


# --- a fresh home ---------------------------------------------------------------------------


def test_without_a_terminal_it_exits_2_and_writes_nothing(tmp_path):
    code, term = run(tmp_path, interactive=False)
    assert code == 2
    assert "needs a terminal" in term.err.getvalue()
    assert term.prompts == []
    assert list(tmp_path.iterdir()) == []


def test_a_fresh_home_gets_a_model_table_and_a_private_key_file(tmp_path):
    code, term = run(tmp_path, answers=["1", "gpt-x", "https://gw.example/v1", ""], secrets=[KEY])
    assert code == 0, term.err.getvalue()

    config = load_config(tmp_path)
    assert config.provider == "openai"
    assert config.model_id == "gpt-x"
    assert config.base_url == "https://gw.example/v1"
    assert config.api_key_env == "OPENAI_API_KEY"  # the Enter key took the default name
    env_file = tmp_path / "local.env"
    assert env_file.read_bytes() == f"OPENAI_API_KEY={KEY}\n".encode()
    assert read_local_env(tmp_path) == {"OPENAI_API_KEY": KEY}
    # The key is in the one file for keys, and nowhere that a person or a log could see it.
    assert KEY not in term.shown()
    assert KEY not in (tmp_path / "agent.toml").read_text(encoding="utf-8")
    if os.name == "posix":
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["agent.toml", "local.env"]


def test_anthropic_gets_no_base_url_question(tmp_path):
    code, term = run(tmp_path, answers=["2", "claude-x", ""], secrets=[KEY])
    assert code == 0, term.err.getvalue()
    assert not any("Base URL" in prompt for prompt in term.prompts)
    table = tomllib.loads((tmp_path / "agent.toml").read_text(encoding="utf-8"))["model"]
    assert table == {"provider": "anthropic", "id": "claude-x", "api_key_env": "ANTHROPIC_API_KEY"}


def test_a_provider_can_be_named_and_the_key_variable_changed(tmp_path):
    code, term = run(tmp_path, answers=["DeepSeek", "ds-x", "", "MY_DS_KEY"], secrets=[KEY])
    assert code == 0, term.err.getvalue()
    config = load_config(tmp_path)
    assert (config.provider, config.api_key_env) == ("deepseek", "MY_DS_KEY")
    assert read_local_env(tmp_path) == {"MY_DS_KEY": KEY}


def test_a_bad_answer_is_asked_again(tmp_path):
    answers = ["9", "x", "1", "", "has space", "gpt-x", "ftp://host", "", "1BAD", ""]
    secrets = ["", "two words", "'quoted'", KEY]
    code, term = run(tmp_path, answers=answers, secrets=secrets)
    assert code == 0, term.err.getvalue()
    assert load_config(tmp_path).model_id == "gpt-x"
    assert read_local_env(tmp_path) == {"OPENAI_API_KEY": KEY}
    out = term.out.getvalue()
    for complaint in (
        "Type a number from 1 to 3.",
        "A model id has text",
        "A base URL starts with",
        "A variable name has",
        "The key is empty",
        "A key has no spaces",
    ):
        assert complaint in out
    # The answers that were turned down are not in the files.
    assert "ftp://host" not in (tmp_path / "agent.toml").read_text(encoding="utf-8")


@pytest.mark.parametrize("stop", [EOFError, KeyboardInterrupt])
def test_a_stopped_dialog_saves_nothing(tmp_path, stop):
    code, term = run(tmp_path, answers=["1", "gpt-x"], on_empty=stop)  # ends at the base URL
    assert code == 1
    assert "Nothing was saved" in term.out.getvalue()
    assert list(tmp_path.iterdir()) == []


# --- the install hint -----------------------------------------------------------------------


def test_the_hint_says_how_to_install_a_missing_package(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_command, "_installed", lambda module: False)
    code, term = run(tmp_path, answers=["1", "gpt-x", "", ""], secrets=[KEY])
    assert code == 0
    assert 'pip install "pi-agent-core-lc[openai]"' in term.out.getvalue()
    assert '"--with", "pi-agent-core-lc[openai]"' in term.out.getvalue()


def test_there_is_no_hint_when_the_package_is_there(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_command, "_installed", lambda module: True)
    code, term = run(tmp_path, answers=["1", "gpt-x", "", ""], secrets=[KEY])
    assert code == 0
    assert "pi-agent-core-lc" not in term.out.getvalue()


# --- a home that has files ------------------------------------------------------------------


@pytest.mark.parametrize(
    "old",
    [
        OLD_FILE,
        OLD_FILE.rstrip("\n"),
        OLD_FILE + "\n",
        OLD_FILE.replace("\n", "\r\n"),
        "",
    ],
    ids=["lf", "no-final-newline", "blank-line-end", "crlf", "empty"],
)
def test_the_model_table_goes_to_the_end_and_the_rest_of_the_file_is_kept(tmp_path, old):
    path = tmp_path / "agent.toml"
    path.write_bytes(old.encode())
    code, term = run(tmp_path, answers=["1", "gpt-x", "", ""], secrets=[KEY])
    assert code == 0, term.err.getvalue()
    new = path.read_bytes().decode()
    assert new.startswith(old)  # to the byte, line ends included
    added = new[len(old) :]
    assert "[model]" in added
    if "\r\n" in old:
        assert "\n" not in added.replace("\r\n", "")  # the new lines end as the old ones do
    parsed = tomllib.loads(new)
    assert parsed["model"]["id"] == "gpt-x"
    if old:
        assert parsed["permission"] == "auto"
    assert load_config(tmp_path).provider == "openai"


def test_a_model_table_without_a_provider_is_not_touched(tmp_path):
    path = tmp_path / "agent.toml"
    before = b'[model]\nid = "x"\n'
    path.write_bytes(before)
    code, term = run(tmp_path, answers=["1"], secrets=[KEY])
    assert code == 1
    assert "[model] table" in term.err.getvalue()
    assert term.prompts == []  # it says so before it asks for anything
    assert path.read_bytes() == before
    assert not (tmp_path / "local.env").exists()


def test_a_file_that_is_not_toml_is_reported_and_left_alone(tmp_path):
    path = tmp_path / "agent.toml"
    before = b"this is = = not toml\n"
    path.write_bytes(before)
    code, term = run(tmp_path, answers=["1"], secrets=[KEY])
    assert code == 1
    assert "cannot read" in term.err.getvalue()
    assert path.read_bytes() == before
    assert not (tmp_path / "local.env").exists()


def test_a_legacy_config_file_is_not_shadowed(tmp_path):
    # A new agent.toml would hide config.toml, and every setting in it, from the agent.
    (tmp_path / "config.toml").write_bytes(b'permission = "auto"\n')
    code, term = run(tmp_path, answers=["1"], secrets=[KEY])
    assert code == 1
    assert "old file" in term.err.getvalue()
    assert term.prompts == []
    assert not (tmp_path / "agent.toml").exists()


def test_a_model_without_its_key_asks_for_the_key_only(tmp_path):
    (tmp_path / "agent.toml").write_bytes(MODEL)
    code, term = run(tmp_path, secrets=[KEY])
    assert code == 0, term.err.getvalue()
    assert len(term.prompts) == 1
    assert "MY_KEY" in term.prompts[0]
    assert read_local_env(tmp_path) == {"MY_KEY": KEY}
    assert (tmp_path / "agent.toml").read_bytes() == MODEL
    assert "keeps the old key" not in term.out.getvalue()


@pytest.mark.parametrize(
    ("answer", "replaced"), [("n", False), ("", False), ("y", True), ("YES", True)]
)
def test_a_model_that_has_its_key_asks_before_it_replaces_it(tmp_path, answer, replaced):
    (tmp_path / "agent.toml").write_bytes(MODEL)
    (tmp_path / "local.env").write_bytes(b"MY_KEY=old\n")
    code, term = run(tmp_path, answers=[answer], secrets=[KEY] if replaced else [])
    assert code == 0, term.err.getvalue()
    assert read_local_env(tmp_path) == {"MY_KEY": KEY if replaced else "old"}
    assert ("keeps the old key" in term.out.getvalue()) is replaced
    assert KEY not in term.shown()


def test_a_key_in_the_real_environment_counts_as_set(tmp_path):
    (tmp_path / "agent.toml").write_bytes(MODEL)
    code, term = run(tmp_path, answers=["n"], environ={"MY_KEY": "from-env"})
    assert code == 0
    assert "has a value" in term.out.getvalue()
    assert not (tmp_path / "local.env").exists()


def test_a_model_with_no_key_variable_has_nothing_to_set_up(tmp_path):
    (tmp_path / "agent.toml").write_bytes(b'[model]\nprovider = "anthropic"\nid = "c"\n')
    code, term = run(tmp_path)
    assert code == 0
    assert "Nothing to set up" in term.out.getvalue()
    assert term.prompts == []
    assert not (tmp_path / "local.env").exists()


# --- the writers ----------------------------------------------------------------------------


def test_write_local_env_replaces_the_first_line_of_a_key_and_keeps_the_rest(tmp_path):
    path = tmp_path / "local.env"
    path.write_bytes(b"# my keys\nOTHER=1\nexport MY_KEY=old\nOTHER2='x'\nMY_KEY=again\n")
    write_local_env(tmp_path, "MY_KEY", KEY)
    assert path.read_bytes() == f"# my keys\nOTHER=1\nMY_KEY={KEY}\nOTHER2='x'\n".encode()


def test_write_local_env_adds_a_key_and_keeps_the_line_ends(tmp_path):
    path = tmp_path / "local.env"
    path.write_bytes(b"OTHER=1\r\n")
    write_local_env(tmp_path, "MY_KEY", KEY)
    assert path.read_bytes() == f"OTHER=1\r\nMY_KEY={KEY}\r\n".encode()


@posix_only
def test_local_env_becomes_private_even_when_it_was_not(tmp_path):
    path = tmp_path / "local.env"
    path.write_text("A=1\n", encoding="utf-8")
    path.chmod(0o644)
    write_local_env(tmp_path, "MY_KEY", KEY)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@posix_only
def test_a_leftover_temporary_file_does_not_make_the_key_file_public(tmp_path):
    leftover = tmp_path / "local.env.tmp"  # what a run that was killed in mid-write leaves
    leftover.write_text("junk\n", encoding="utf-8")
    leftover.chmod(0o644)
    write_local_env(tmp_path, "MY_KEY", KEY)
    assert stat.S_IMODE((tmp_path / "local.env").stat().st_mode) == 0o600
    assert not leftover.exists()


@posix_only
def test_a_replaced_agent_toml_keeps_its_mode(tmp_path):
    path = tmp_path / "agent.toml"
    path.write_bytes(OLD_FILE.encode())
    path.chmod(0o640)
    add_model_table(path, model_table("openai", "gpt-x", None, "K"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


@posix_only
def test_a_symbolic_link_stays_a_link(tmp_path):
    real = tmp_path / "dotfiles" / "agent.toml"
    real.parent.mkdir()
    real.write_bytes(OLD_FILE.encode())
    home = tmp_path / "home"
    home.mkdir()
    (home / "agent.toml").symlink_to(real)
    add_model_table(home / "agent.toml", model_table("openai", "gpt-x", None, "K"))
    assert (home / "agent.toml").is_symlink()
    assert "[model]" in real.read_text(encoding="utf-8")


def test_a_failed_write_leaves_no_temporary_file(tmp_path, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(setup_command.os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        write_local_env(tmp_path, "MY_KEY", KEY)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("model_id", ['we"ird', "back\\slash", "ünï-cødé", "a#b", "x'y", "[z]"])
def test_the_model_table_holds_odd_text(model_id):
    parsed = tomllib.loads(model_table("openai", model_id, None, "K"))
    assert parsed["model"]["id"] == model_id


def test_the_model_table_refuses_text_that_toml_cannot_hold():
    with pytest.raises(SetupError):
        model_table("openai", "a\x7fb", None, "K")


# --- the flag -------------------------------------------------------------------------------


def _cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in {"PI_USE_MOCK", "PI_HOME"}}
    env["PI_HOME"] = str(tmp_path)
    return subprocess.run(
        [sys.executable, "-m", "pi_agent_cli", *args],
        check=False,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        env=env,
        timeout=120,
    )


def test_the_flag_without_a_terminal_exits_2(tmp_path):
    result = _cli(tmp_path, "--setup")
    assert result.returncode == 2, result.stderr
    assert "needs a terminal" in result.stderr
    assert result.stdout == ""
    assert list(tmp_path.iterdir()) == []


def test_the_flag_does_not_mix_with_print_mode(tmp_path):
    result = _cli(tmp_path, "--setup", "-p", "hi")
    assert result.returncode == 2
    assert "not allowed with" in result.stderr
