"""Build the frozen agent for the platform this runs on, and pack it.

    python packaging/agent/build.py [--out DIR]

Needs the repository's packages and PyInstaller in the Python that runs it (see
``docs/specs/2026-10-31-agent-binaries.md``). PyInstaller does not cross-compile, so one run
makes one target. Two programs are frozen:

1. ``pi-sandbox-child``: ``child.py`` of the workflow sandbox, alone, as a one-folder program.
   It has the standard library and nothing else, and it is one process on every platform
   (a one-file program is two on Windows, which the sandbox's Windows job refuses).
2. ``zycode-agent``: the agent as one file. The folder of 1 is inside it, where the frozen
   sandbox looks for it (``pi_dynamic_workflows/sandbox/frozen-child``).

The result is ``zycode-agent-<os>-<arch>-<version>.tar.gz`` (``.zip`` on Windows) with the
program at the top level of the archive: the form the ACP registry wants (``cmd: ./zycode-agent``).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CHILD_SCRIPT = ROOT / "packages/pi-dynamic-workflows/pi_dynamic_workflows/sandbox/child.py"
PROGRAM = "zycode-agent"


def target_name() -> str:
    """``<os>-<arch>`` in the words of the ACP registry (``darwin-aarch64``, ...)."""
    system = {"Linux": "linux", "Darwin": "darwin", "Windows": "windows"}[platform.system()]
    machine = platform.machine().lower()
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}[
        machine
    ]
    return f"{system}-{arch}"


def agent_version() -> str:
    text = (ROOT / "packages/pi-agent-cli/pyproject.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)["project"]["version"]


def _pyinstaller(*args: str, env: dict[str, str] | None = None) -> None:
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", *args]
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True, cwd=ROOT, env={**os.environ, **(env or {})})


def freeze(work: Path) -> Path:
    """Freeze the child, then the agent. Returns the agent program."""
    child_dist = work / "child-dist"
    _pyinstaller(
        "--onedir",
        "--name",
        "pi-sandbox-child",
        "--noupx",
        "--python-option",
        "X utf8",
        "--distpath",
        str(child_dist),
        "--workpath",
        str(work / "child-build"),
        "--specpath",
        str(work),
        str(CHILD_SCRIPT),
    )
    agent_dist = work / "agent-dist"
    _pyinstaller(
        "--distpath",
        str(agent_dist),
        "--workpath",
        str(work / "agent-build"),
        str(HERE / "zycode-agent.spec"),
        env={"ZYCODE_CHILD_DIR": str(child_dist / "pi-sandbox-child")},
    )
    suffix = ".exe" if os.name == "nt" else ""
    program = agent_dist / (PROGRAM + suffix)
    if not program.is_file():
        raise SystemExit(f"PyInstaller did not make {program}")
    return program


def pack(program: Path, out: Path, target: str, version: str) -> Path:
    stem = f"{PROGRAM}-{target}-{version}"
    if os.name == "nt":
        archive = out / f"{stem}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(program, program.name)
    else:
        archive = out / f"{stem}.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            info = tf.gettarinfo(str(program), arcname=program.name)
            info.mode = 0o755
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with program.open("rb") as handle:
                tf.addfile(info, handle)
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "agent")
    parser.add_argument("--work", type=Path, default=ROOT / "build" / "agent")
    args = parser.parse_args()

    target, version = target_name(), agent_version()
    shutil.rmtree(args.work, ignore_errors=True)
    args.out.mkdir(parents=True, exist_ok=True)
    program = freeze(args.work)
    archive = pack(program, args.out, target, version)
    # The program next to the archive: the smoke test runs it, and the archive is what is shipped.
    shutil.copy2(program, args.out / program.name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    print(f"target:  {target}")
    print(f"program: {args.out / program.name} ({program.stat().st_size / 1e6:.1f} MB)")
    print(f"archive: {archive} ({archive.stat().st_size / 1e6:.1f} MB)")
    print(f"sha256:  {digest}")


if __name__ == "__main__":
    main()
