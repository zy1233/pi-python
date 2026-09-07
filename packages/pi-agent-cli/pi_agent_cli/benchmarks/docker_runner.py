"""Docker container sandbox runner for FrontierHarness and Terminal-Bench tasks."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from pi_agent_cli.benchmarks.models import BenchmarkTask
from pi_agent_core.coding_tools._base import CodingTool
from pi_agent_core.coding_tools.bash import BashParams, _format_timeout_seconds, _resolve_timeout
from pi_agent_core.coding_tools.output_accumulator import OutputAccumulator
from pi_agent_core.coding_tools.truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES
from pi_agent_core.types import AgentTool, AgentToolResult, AgentToolUpdateCallback

_DESCRIPTION = (
    "Execute a bash command inside the task container's /app directory. Returns stdout and stderr. "
    f"Output is truncated to last {DEFAULT_MAX_LINES} lines or {DEFAULT_MAX_BYTES // 1024}KB "
    "(whichever is hit first). Optionally provide a timeout in seconds."
)


def get_docker_cmd() -> list[str] | None:
    """Find the docker command prefix (native docker or via WSL)."""
    if shutil.which("docker"):
        # Test if docker daemon responds
        try:
            res = subprocess.run(
                ["docker", "version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0:
                return ["docker"]
        except Exception:
            pass

    if sys.platform == "win32" and shutil.which("wsl"):
        try:
            res = subprocess.run(
                ["wsl", "docker", "version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0:
                return ["wsl", "docker"]
        except Exception:
            pass

    return None


def to_docker_mount_path(path: Path) -> str:
    """Convert host filesystem path to a path usable by Docker daemon."""
    resolved = path.resolve()
    if sys.platform == "win32":
        # Check if running via wsl docker
        drive = resolved.drive.replace(":", "").lower()
        posix = resolved.as_posix()
        if len(posix) >= 2 and posix[1] == ":":
            posix = posix[2:]
        return f"/mnt/{drive}{posix}"
    return str(resolved)


def run_docker_sync(
    args: list[str],
    *,
    timeout: float = 120.0,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Execute a synchronous docker command."""
    prefix = get_docker_cmd()
    if not prefix:
        raise RuntimeError("Docker is not available.")
    cmd = [*prefix, *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


class DockerContainerSession:
    """Manages the lifecycle of a task container."""

    def __init__(self, task: BenchmarkTask, workspace: Path) -> None:
        self.task = task
        self.workspace = workspace
        clean_task_name = "".join(
            c if c.isalnum() or c in "-_" else "-" for c in task.id
        ).strip("-")
        self.container_name = f"pi-eval-{clean_task_name}-{int(time.time())}"
        self.image = (
            task.metadata.get("environment", {}).get("docker_image")
            or task.metadata.get("environment", {}).get("image")
        )
        self.is_running = False

    def setup(self) -> None:
        """Pull image, extract starter files, and start container with bind-mount."""
        if not self.image:
            raise ValueError(f"No docker_image found for task: {self.task.id}")

        # 1. Check if image is pulled; if not, pull it
        inspect = run_docker_sync(["image", "inspect", self.image])
        if inspect.returncode != 0:
            print(f"[DOCKER] Pulling image: {self.image}...")
            pull = run_docker_sync(["pull", self.image], timeout=600.0)
            if pull.returncode != 0:
                raise RuntimeError(
                    f"Failed to pull image {self.image}: {pull.stderr.strip()}"
                )

        # 2. Extract initial repository/environment from image /app if present
        tmp_init = f"init-{self.container_name}"
        with contextlib.suppress(Exception):
            run_docker_sync(["create", "--name", tmp_init, self.image])
            mount_ws = to_docker_mount_path(self.workspace)
            cp_res = run_docker_sync(["cp", f"{tmp_init}:/app/.", mount_ws])
            if cp_res.returncode == 0:
                print(f"[DOCKER] Extracted starter repository into {self.workspace}")
            run_docker_sync(["rm", "-f", tmp_init])

        # 3. Start container with workspace bind-mounted to /app and proxy env
        proxy_url = (
            os.environ.get("https_proxy")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("http_proxy")
            or "http://172.20.35.30:10809"
        )
        mount_arg = f"{to_docker_mount_path(self.workspace)}:/app"
        run_res = run_docker_sync([
            "run",
            "-d",
            "--name",
            self.container_name,
            "-e",
            f"http_proxy={proxy_url}",
            "-e",
            f"https_proxy={proxy_url}",
            "-e",
            f"HTTP_PROXY={proxy_url}",
            "-e",
            f"HTTPS_PROXY={proxy_url}",
            "-v",
            mount_arg,
            "-w",
            "/app",
            self.image,
            "tail",
            "-f",
            "/dev/null",
        ])
        if run_res.returncode != 0:
            raise RuntimeError(
                f"Failed to start container {self.container_name}: {run_res.stderr.strip()}"
            )
        self.is_running = True
        print(f"[DOCKER] Container {self.container_name} started.")

        # Configure safe git directory inside container
        run_docker_sync([
            "exec",
            self.container_name,
            "git",
            "config",
            "--global",
            "--add",
            "safe.directory",
            "*",
        ])

        # 4. Copy tests if present
        tests_dir = self.task.task_dir / "tests"
        if tests_dir.is_dir():
            cp_test = run_docker_sync([
                "cp",
                f"{to_docker_mount_path(tests_dir)}/.",
                f"{self.container_name}:/tests/",
            ])
            if cp_test.returncode == 0:
                # Strip CRLF line endings on Linux container files
                run_docker_sync([
                    "exec",
                    self.container_name,
                    "sh",
                    "-c",
                    "sed -i 's/\\r$//' /tests/* 2>/dev/null || true",
                ])
                # chmod +x on tests
                run_docker_sync([
                    "exec",
                    self.container_name,
                    "chmod",
                    "-R",
                    "+x",
                    "/tests",
                ])

    def teardown(self) -> None:
        """Stop and remove container, copying back any modified workspace files."""
        if self.is_running:
            # Sync files from container /app back to local workspace before tearing down
            mount_ws = to_docker_mount_path(self.workspace)
            with contextlib.suppress(Exception):
                run_docker_sync(["cp", f"{self.container_name}:/app/.", mount_ws])
            run_docker_sync(["rm", "-f", self.container_name])
            self.is_running = False
            print(f"[DOCKER] Container {self.container_name} removed.")

    def run_verifier(self, timeout_sec: float = 180.0) -> tuple[bool, str]:
        """Execute the verifier inside the container."""
        if not self.is_running:
            return (False, "Container is not running.")

        # Check for test.sh
        test_sh = self.task.task_dir / "tests" / "test.sh"
        test_py = self.task.task_dir / "tests" / "test_outputs.py"

        if test_sh.is_file():
            res = run_docker_sync(
                ["exec", self.container_name, "bash", "/tests/test.sh"],
                timeout=timeout_sec,
            )
            out = (
                f"=== Docker test.sh Output (exit {res.returncode}) ===\n"
                f"{res.stdout}\n{res.stderr}"
            )

            # Check reward.json or reward.txt
            reward_json = run_docker_sync(
                ["exec", self.container_name, "cat", "/logs/verifier/reward.json"]
            )
            if reward_json.returncode == 0 and reward_json.stdout.strip():
                try:
                    data = json.loads(reward_json.stdout.strip())
                    r = data.get("reward")
                    passed = r == 1 or r == 1.0
                    return (passed, f"{out}\n\n[Reward JSON]: {reward_json.stdout.strip()}")
                except Exception:
                    pass

            reward_txt = run_docker_sync(
                ["exec", self.container_name, "cat", "/logs/verifier/reward.txt"]
            )
            if reward_txt.returncode == 0 and reward_txt.stdout.strip():
                txt = reward_txt.stdout.strip()
                passed = txt == "1"
                return (passed, f"{out}\n\n[Reward TXT]: {txt}")

            return (res.returncode == 0, out)

        if test_py.is_file():
            # Run pytest inside container.
            res = run_docker_sync(
                [
                    "exec",
                    self.container_name,
                    "python3",
                    "-m",
                    "pytest",
                    "/tests/test_outputs.py",
                    "-v",
                    "--tb=short",
                ],
                timeout=timeout_sec,
            )
            out = (
                f"=== Docker pytest Output (exit {res.returncode}) ===\n"
                f"{res.stdout}\n{res.stderr}"
            )
            return (res.returncode == 0, out)

        return (False, "No verifier found for container execution.")


def create_docker_bash_tool(
    container_name: str,
    *,
    cwd: str = "/app",
) -> AgentTool:
    """Create a bash tool that executes commands inside a running Docker container."""
    docker_prefix = get_docker_cmd()
    if not docker_prefix:
        raise RuntimeError("Docker is not available for docker bash tool.")

    async def execute(
        _tool_call_id: str,
        params: BashParams,
        signal: Any | None = None,
        on_update: AgentToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        output = OutputAccumulator(temp_file_prefix="pi-docker-bash")
        timeout = _resolve_timeout(params.timeout)

        if signal is not None and getattr(signal, "aborted", False):
            raise RuntimeError("Command aborted")

        # docker exec -i -w {cwd} {container_name} bash -lc {command}
        argv = [
            *docker_prefix,
            "exec",
            "-i",
            "-w",
            cwd,
            container_name,
            "bash",
            "-lc",
            params.command,
        ]

        spawn_kwargs: dict[str, Any] = {
            "stdin": asyncio.subprocess.DEVNULL,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.STDOUT,
        }
        if sys.platform != "win32":
            spawn_kwargs["start_new_session"] = True

        proc = await asyncio.create_subprocess_exec(*argv, **spawn_kwargs)

        async def pump() -> None:
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(8192)
                if not chunk:
                    return
                output.append(chunk)

        async def wait_exit() -> int:
            while proc.returncode is None:
                await asyncio.sleep(0.02)
            return proc.returncode

        exit_task = asyncio.create_task(wait_exit())
        pump_task = asyncio.create_task(pump())

        timed_out = False
        aborted = False

        try:
            await asyncio.wait_for(asyncio.shield(exit_task), timeout=timeout)
        except TimeoutError:
            timed_out = True
            with contextlib.suppress(Exception):
                proc.kill()
        except asyncio.CancelledError:
            aborted = True
            with contextlib.suppress(Exception):
                proc.kill()
            raise

        with contextlib.suppress(Exception):
            await asyncio.wait_for(pump_task, timeout=1.0)

        snapshot = output.snapshot(persist_if_truncated=True)
        text = snapshot.content or ""

        if timed_out:
            raise RuntimeError(
                f"{text}\n\nCommand timed out after {_format_timeout_seconds(timeout)} seconds"
            )
        if aborted:
            raise RuntimeError(f"{text}\n\nCommand aborted")
        if proc.returncode != 0 and proc.returncode >= 0:
            raise RuntimeError(f"{text}\n\nCommand exited with code {proc.returncode}")

        details: dict[str, Any] | None = None
        if snapshot.truncation.truncated:
            details = {
                "truncation": snapshot.truncation.to_dict(),
                "fullOutputPath": snapshot.full_output_path,
            }

        return AgentToolResult(
            content=[{"type": "text", "text": text}],
            details=details,
        )

    return CodingTool(
        name="bash",
        description=_DESCRIPTION,
        label="bash",
        parameters=BashParams,
        execute_fn=execute,
        prompt_snippet="Execute bash commands (ls, grep, find, etc.) in the container",
        prompt_guidelines=["Commands run inside the container's /app directory."],
    )
