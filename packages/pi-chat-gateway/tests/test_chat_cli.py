"""The ``zycode-chat`` command line, without a network."""

from __future__ import annotations

import stat
import sys
import tomllib
from pathlib import Path

import pytest

from pi_chat_gateway import cli
from pi_chat_gateway.config import load_config, load_credentials
from pi_chat_gateway.store import Owner, Store


def _setup_files(home: Path, project: Path) -> None:
    (home / "chat.toml").write_text(cli._template("demo", project, "feishu", "feishu"), "utf-8")
    cli._write_credentials(home, "feishu", {"app_id": "cli_x", "app_secret": "secret-1"})


def test_the_template_is_a_valid_configuration(home: Path, project: Path):
    _setup_files(home, project)
    config = load_config(home)
    assert config.start_mode == "ask" and config.project("demo").path == project.resolve()
    assert load_credentials(config)["feishu"]["app_secret"] == "secret-1"


@pytest.mark.skipif(sys.platform == "win32", reason="mode bits")
def test_credentials_are_merged_and_private(home: Path, project: Path):
    _setup_files(home, project)
    cli._write_credentials(
        home, "other", {"app_id": 'quote " and \\ backslash', "app_secret": "s2"}
    )
    path = home / "chat" / "credentials.toml"
    data = tomllib.loads(path.read_text("utf-8"))
    assert data["feishu"]["app_secret"] == "secret-1"
    assert data["other"]["app_id"] == 'quote " and \\ backslash'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_pair_unpair_and_status(home: Path, project: Path, capsys: pytest.CaptureFixture[str]):
    _setup_files(home, project)
    with pytest.raises(SystemExit) as done:
        cli.main(["pair"])
    assert done.value.code == 0
    printed = capsys.readouterr().out
    assert "/pair " in printed
    Store(home).set_owner(Owner("feishu", "ou_owner", "tk", "oc_dm", 0.0))
    with pytest.raises(SystemExit) as done:
        cli.main(["pair"])
    assert done.value.code == 1, "a platform with an owner gets no new code"
    with pytest.raises(SystemExit):
        cli.main(["status"])
    assert "owner ou_owner" in capsys.readouterr().out
    with pytest.raises(SystemExit) as done:
        cli.main(["unpair"])
    assert done.value.code == 0 and Store(home).owner("feishu") is None


def test_a_missing_configuration_is_a_clear_error(home: Path, capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as done:
        cli.main(["status"])
    assert done.value.code == 1
    assert "chat.toml is missing" in capsys.readouterr().err
