"""Local ExecutionEnv implementation for AgentHarness."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pi_agent_harness.types import ExecResult, ExecutionError, FileError, FileInfo

_IS_WINDOWS = sys.platform == "win32"


def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill process and its children.

    On Unix (when started with ``start_new_session=True``), kills the entire
    process group so child processes are not orphaned.  On Windows,
    ``proc.kill()`` already calls ``TerminateProcess`` which terminates the
    process tree.
    """
    with contextlib.suppress(ProcessLookupError, OSError):
        if not _IS_WINDOWS and proc.pid is not None:
            os.killpg(proc.pid, 9)  # SIGKILL
        else:
            proc.kill()


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
        kwargs: dict[str, Any] = {}
        if not _IS_WINDOWS:
            kwargs["start_new_session"] = True
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=workdir,
            env={**os.environ, **(env or {})},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )
        abort_task: asyncio.Task[None] | None = None
        aborted = False

        async def _watch_signal() -> None:
            nonlocal aborted
            await signal.wait_aborted()
            aborted = True
            _kill_process_tree(proc)

        if signal is not None and hasattr(signal, "wait_aborted"):
            abort_task = asyncio.create_task(_watch_signal())

        async def _read_stream(
            stream: asyncio.StreamReader, callback: Callable[[str], Any] | None
        ) -> str:
            parts: list[str] = []
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode(errors="replace")
                parts.append(text)
                if callback:
                    callback(text)
            return "".join(parts)

        try:
            assert proc.stdout is not None and proc.stderr is not None
            stdout, stderr = await asyncio.wait_for(
                asyncio.gather(
                    _read_stream(proc.stdout, on_stdout),
                    _read_stream(proc.stderr, on_stderr),
                ),
                timeout=timeout,
            )
            await proc.wait()
        except TimeoutError as exc:
            _kill_process_tree(proc)
            await proc.wait()
            raise ExecutionError("timeout", f"Command timed out after {timeout}s", exc) from exc
        except asyncio.CancelledError:
            _kill_process_tree(proc)
            await proc.wait()
            raise
        finally:
            if abort_task is not None and not abort_task.done():
                abort_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await abort_task
        if aborted:
            await proc.wait()
            raise ExecutionError("aborted", "Execution aborted by signal")
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
