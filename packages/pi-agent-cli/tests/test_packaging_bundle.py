from __future__ import annotations

import importlib.util
import sys
import tarfile
from pathlib import Path

BUILD_SCRIPT = Path(__file__).resolve().parents[3] / "packaging" / "agent" / "build.py"
spec = importlib.util.spec_from_file_location("zycode_agent_build", BUILD_SCRIPT)
assert spec and spec.loader
build_mod = importlib.util.module_from_spec(spec)
sys.modules["zycode_agent_build"] = build_mod
spec.loader.exec_module(build_mod)

bundle_from_agent_archive = build_mod.bundle_from_agent_archive
pack_bundle = build_mod.pack_bundle


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
