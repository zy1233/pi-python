from __future__ import annotations

import importlib.util
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

BUILD_SCRIPT = Path(__file__).resolve().parents[3] / "packaging" / "agent" / "build.py"
spec = importlib.util.spec_from_file_location("zycode_agent_build", BUILD_SCRIPT)
assert spec and spec.loader
build_mod = importlib.util.module_from_spec(spec)
sys.modules["zycode_agent_build"] = build_mod
spec.loader.exec_module(build_mod)

bundle_from_agent_archive = build_mod.bundle_from_agent_archive
pack_bundle = build_mod.pack_bundle


def _read_member(archive: Path, name: str) -> bytes:
    with tarfile.open(archive, "r:gz") as tf:
        member = tf.extractfile(name)
        assert member is not None
        return member.read()


def test_pack_bundle(tmp_path: Path) -> None:
    tui_bin = tmp_path / "zypi"
    tui_bin.write_bytes(b"mock-zypi-binary")
    agent_bin = tmp_path / "zycode-agent"
    agent_bin.write_bytes(b"mock-agent-binary")
    out_dir = tmp_path / "out"

    archive = pack_bundle(tui_bin, agent_bin, out_dir, "linux-x86_64", "1.0.0")

    assert archive.name == "zycode-linux-x86_64-1.0.0.tar.gz"
    assert archive.is_file()

    with tarfile.open(archive, "r:gz") as tf:
        names = tf.getnames()
        assert set(names) == {"zypi", "zycode-agent"}
        for member in tf.getmembers():
            assert member.mode == 0o755
            assert member.uid == 0
            assert member.gid == 0
            assert member.uname == ""
            assert member.gname == ""
            content = tf.extractfile(member).read()
            if member.name == "zypi":
                assert content == b"mock-zypi-binary"
            else:
                assert content == b"mock-agent-binary"


def test_bundle_from_agent_archive(tmp_path: Path) -> None:
    # 1. Create a mock zycode-agent tar.gz archive
    agent_bin = tmp_path / "zycode-agent"
    agent_bin.write_bytes(b"agent-payload")
    agent_tar = tmp_path / "zycode-agent-linux-x86_64-1.0.0.tar.gz"
    with tarfile.open(agent_tar, "w:gz") as tf:
        info = tf.gettarinfo(str(agent_bin), arcname="zycode-agent")
        with agent_bin.open("rb") as f:
            tf.addfile(info, f)

    tui_bin = tmp_path / "zypi"
    tui_bin.write_bytes(b"tui-payload")
    out_dir = tmp_path / "out"

    bundle = bundle_from_agent_archive(tui_bin, agent_tar, out_dir, "linux-x86_64", "1.0.0")

    assert bundle.name == "zycode-linux-x86_64-1.0.0.tar.gz"
    assert bundle.is_file()

    with tarfile.open(bundle, "r:gz") as tf:
        assert set(tf.getnames()) == {"zypi", "zycode-agent"}
        assert tf.extractfile("zypi").read() == b"tui-payload"
        assert tf.extractfile("zycode-agent").read() == b"agent-payload"


def test_bundle_from_the_archive_that_pack_writes(tmp_path: Path) -> None:
    # The workflow gives the bundle step the archive that ``pack`` wrote (a .zip on Windows, a
    # .tar.gz elsewhere). The test goes through ``pack``, so a change in the layout of that
    # archive (a prefix, another name) breaks this test and not the release.
    program = tmp_path / "zycode-agent"
    program.write_bytes(b"agent-payload")
    archives = tmp_path / "archives"
    archives.mkdir()
    agent_archive = build_mod.pack(program, archives, "linux-x86_64", "1.0.0")
    assert agent_archive.name.startswith("zycode-agent-linux-x86_64-1.0.0.")

    tui_bin = tmp_path / "zypi"
    tui_bin.write_bytes(b"tui-payload")

    bundle = bundle_from_agent_archive(tui_bin, agent_archive, archives, "linux-x86_64", "1.0.0")

    assert bundle.name == "zycode-linux-x86_64-1.0.0.tar.gz"
    assert _read_member(bundle, "zypi") == b"tui-payload"
    assert _read_member(bundle, "zycode-agent") == b"agent-payload"


def test_bundle_from_a_zip_archive(tmp_path: Path) -> None:
    # The Windows agent archive is a .zip, and its program is ``zycode-agent.exe``.
    agent_zip = tmp_path / "zycode-agent-windows-x86_64-1.0.0.zip"
    with zipfile.ZipFile(agent_zip, "w") as zf:
        zf.writestr("zycode-agent.exe", b"agent-payload")
    tui_bin = tmp_path / "zypi"
    tui_bin.write_bytes(b"tui-payload")

    bundle = bundle_from_agent_archive(
        tui_bin, agent_zip, tmp_path / "out", "linux-x86_64", "1.0.0"
    )

    with tarfile.open(bundle, "r:gz") as tf:
        assert set(tf.getnames()) == {"zypi", "zycode-agent"}
    assert _read_member(bundle, "zycode-agent") == b"agent-payload"


def test_bundle_from_an_archive_without_the_program(tmp_path: Path) -> None:
    other = tmp_path / "README"
    other.write_bytes(b"not the program")
    agent_tar = tmp_path / "zycode-agent-linux-x86_64-1.0.0.tar.gz"
    with tarfile.open(agent_tar, "w:gz") as tf:
        tf.add(other, arcname="README")
    tui_bin = tmp_path / "zypi"
    tui_bin.write_bytes(b"tui-payload")

    with pytest.raises(FileNotFoundError, match="zycode-agent not found"):
        bundle_from_agent_archive(tui_bin, agent_tar, tmp_path / "out", "linux-x86_64", "1.0.0")


def test_bundle_skips_a_directory_with_the_name_of_the_program(tmp_path: Path) -> None:
    # ``extractfile`` gives None for a directory. The step must say that the program is missing,
    # and not stop with an AttributeError.
    agent_tar = tmp_path / "zycode-agent-linux-x86_64-1.0.0.tar.gz"
    with tarfile.open(agent_tar, "w:gz") as tf:
        info = tarfile.TarInfo("zycode-agent")
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        tf.addfile(info)
    tui_bin = tmp_path / "zypi"
    tui_bin.write_bytes(b"tui-payload")

    with pytest.raises(FileNotFoundError, match="zycode-agent not found"):
        bundle_from_agent_archive(tui_bin, agent_tar, tmp_path / "out", "linux-x86_64", "1.0.0")


def test_cli_makes_a_bundle_from_an_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The command that ``.github/workflows/agent-binaries.yml`` runs for each target.
    program = tmp_path / "zycode-agent"
    program.write_bytes(b"agent-payload")
    archives = tmp_path / "archives"
    archives.mkdir()
    agent_archive = build_mod.pack(program, archives, "linux-aarch64", "1.0.0")
    tui_bin = tmp_path / "zypi"
    tui_bin.write_bytes(b"tui-payload")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build.py",
            "--bundle-from-archive",
            str(agent_archive),
            "--tui-binary",
            str(tui_bin),
            "--target",
            "linux-aarch64",
            "--version",
            "1.0.0",
            "--out",
            str(archives),
        ],
    )

    build_mod.main()

    bundle = archives / "zycode-linux-aarch64-1.0.0.tar.gz"
    assert bundle.is_file()
    assert "sha256:" in capsys.readouterr().out
    assert _read_member(bundle, "zypi") == b"tui-payload"


def test_cli_needs_the_zypi_file_for_a_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["build.py", "--bundle-from-archive", str(tmp_path / "agent.tar.gz")]
    )

    with pytest.raises(SystemExit, match="--tui-binary"):
        build_mod.main()
