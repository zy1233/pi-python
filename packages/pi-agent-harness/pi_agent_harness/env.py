"""Local ExecutionEnv implementation for AgentHarness."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pi_agent_harness.types import ExecResult, ExecutionError, FileError, FileInfo


class LocalExecutionEnv:
    def __init__(self, cwd: str | Path) -> None:
        self.cwd = str(Path(cwd).resolve())
        self._temp_paths: list[Path] = []

    async def absolute_path(self, path: str | Path) -> str:
        return str(self._resolve(path))

    async def canonical_path(self, path: str | Path) -> str:
        return str(self._resolve(path).resolve())

    async def exists(self, path: str | Path) -> bool:
        return self._resolve(path).exists()

    async def read_text_file(self, path: str | Path) -> str:
        target = self._resolve(path)
        return await asyncio.to_thread(target.read_text, encoding="utf-8")

    async def read_text_lines(self, path: str | Path, max_lines: int | None = None) -> list[str]:
        text = await self.read_text_file(path)
        lines = text.splitlines()
        return lines[:max_lines] if max_lines is not None else lines

    async def read_binary_file(self, path: str | Path) -> bytes:
        target = self._resolve(path)
        return await asyncio.to_thread(target.read_bytes)

    async def write_file(self, path: str | Path, content: str | bytes) -> None:
        target = self._resolve(path)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        if isinstance(content, bytes):
            await asyncio.to_thread(target.write_bytes, content)
        else:
            await asyncio.to_thread(target.write_text, content, encoding="utf-8")

    async def append_file(self, path: str | Path, content: str | bytes) -> None:
        target = self._resolve(path)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        mode = "ab" if isinstance(content, bytes) else "a"
        encoding = None if isinstance(content, bytes) else "utf-8"

        def append() -> None:
            with target.open(mode, encoding=encoding) as fh:
                fh.write(content)

        await asyncio.to_thread(append)

    async def file_info(self, path: str | Path) -> FileInfo:
        target = self._resolve(path)
        if not target.exists() and not target.is_symlink():
            raise FileError("not_found", f"File not found: {path}", str(target))
        stat = await asyncio.to_thread(target.lstat)
        if target.is_symlink():
            kind = "symlink"
        elif target.is_dir():
            kind = "directory"
        elif target.is_file():
            kind = "file"
        else:
            raise FileError("invalid", f"Unsupported file type: {path}", str(target))
        return FileInfo(
            name=target.name,
            path=self._display_path(target),
            kind=kind,
            size=stat.st_size,
            mtimeMs=stat.st_mtime * 1000,
        )

    async def list_dir(self, path: str | Path) -> list[FileInfo]:
        target = self._resolve(path)
        if not target.exists():
            raise FileError("not_found", f"Directory not found: {path}", str(target))
        if not target.is_dir():
            raise FileError("not_directory", f"Not a directory: {path}", str(target))
        children = sorted(target.iterdir(), key=lambda p: p.name)
        return [await self.file_info(child) for child in children]

    async def create_dir(self, path: str | Path) -> None:
        await asyncio.to_thread(self._resolve(path).mkdir, parents=True, exist_ok=True)

    async def remove(self, path: str | Path) -> None:
        target = self._resolve(path)
        if target.is_dir() and not target.is_symlink():
            await asyncio.to_thread(shutil.rmtree, target)
        elif target.exists() or target.is_symlink():
            await asyncio.to_thread(target.unlink)

    async def create_temp_dir(self, prefix: str = "pi-harness-") -> str:
        path = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix=prefix))
        self._temp_paths.append(path)
        return str(path)

    async def create_temp_file(self, prefix: str = "pi-harness-", suffix: str = "") -> str:
        fd, name = await asyncio.to_thread(tempfile.mkstemp, prefix=prefix, suffix=suffix)
        os.close(fd)
        path = Path(name)
        self._temp_paths.append(path)
        return str(path)

    async def exec(
        self,
        command: str,
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        signal: Any | None = None,
        on_stdout: Callable[[str], Any] | None = None,
        on_stderr: Callable[[str], Any] | None = None,
    ) -> ExecResult:
        if signal is not None and getattr(signal, "aborted", False):
            raise ExecutionError("aborted", "Execution aborted before start")
        workdir = str(self._resolve(cwd or self.cwd))
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=workdir,
            env={**os.environ, **(env or {})},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise ExecutionError("timeout", f"Command timed out after {timeout}s", exc) from exc
        stdout = stdout_b.decode(errors="replace")
        stderr = stderr_b.decode(errors="replace")
        if on_stdout and stdout:
            on_stdout(stdout)
        if on_stderr and stderr:
            on_stderr(stderr)
        return ExecResult(stdout=stdout, stderr=stderr, exitCode=proc.returncode or 0)

    async def cleanup(self) -> None:
        for path in self._temp_paths:
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
            except OSError:
                pass
        self._temp_paths.clear()

    def _resolve(self, path: str | Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = Path(self.cwd) / candidate
        return candidate

    def _display_path(self, path: Path) -> str:
        try:
            relative = path.resolve().relative_to(Path(self.cwd).resolve())
            return relative.as_posix()
        except ValueError:
            return path.resolve().as_posix()
