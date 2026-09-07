"""Evaluation runner for executing benchmark tasks against pi-agent-cli / AgentHarness."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from pi_agent_cli.benchmarks.docker_runner import (
    DockerContainerSession,
    create_docker_bash_tool,
    get_docker_cmd,
)
from pi_agent_cli.benchmarks.models import BenchmarkTask, TrialResult
from pi_agent_cli.config import CliConfig, load_config, pi_home
from pi_agent_cli.factory import create_session_harness, default_stream_fn, load_session_resources
from pi_agent_core.coding_tools import create_all_tools
from pi_agent_core.messages import AssistantMessage
from pi_agent_harness import JsonlSessionRepo

# Default token prices (USD per 1M tokens) for cost calculation if provider doesn't report cost
# DeepSeek / Moonshot / SiliconFlow typical reference pricing
DEFAULT_PRICING = {
    "input_per_million": 1.0,
    "cached_input_per_million": 0.1,
    "output_per_million": 2.0,
}


def _calculate_cost(
    *,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    provider: str,
    pricing: dict[str, float] | None = None,
) -> float:
    p = pricing or DEFAULT_PRICING
    uncached_input = max(0, input_tokens - cached_tokens)
    cost = (
        (uncached_input / 1_000_000.0) * p["input_per_million"]
        + (cached_tokens / 1_000_000.0) * p["cached_input_per_million"]
        + (output_tokens / 1_000_000.0) * p["output_per_million"]
    )
    return cost


def _copy_task_environment(task: BenchmarkTask, workspace: Path) -> None:
    """Copy non-verifier fixtures from task directory into workspace."""
    env_dir = task.task_dir / "environment"
    if env_dir.is_dir():
        for item in env_dir.iterdir():
            if item.name in ("Dockerfile", ".dockerignore"):
                continue
            dest = workspace / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)


def _prepare_verifier_test(task: BenchmarkTask, workspace: Path) -> Path | None:
    """Adapt verifier test paths for non-container (local Windows/macOS) execution."""
    if not task.verifier_test or not task.verifier_test.is_file():
        return None

    content = task.verifier_test.read_text(encoding="utf-8", errors="ignore")
    # If the test mentions /app or /data and /app is not a native directory
    if not Path("/app").is_dir() and ("/app" in content or "/data" in content):
        workspace_s = str(workspace.resolve()).replace("\\", "/")
        content = content.replace('"/app/', f'"{workspace_s}/')
        content = content.replace("'/app/", f"'{workspace_s}/")
        content = content.replace('"/app"', f'"{workspace_s}"')
        content = content.replace("'/app'", f"'{workspace_s}'")
        content = content.replace('"/data/', f'"{workspace_s}/data/')
        content = content.replace("'/data/", f"'{workspace_s}/data/")
        content = content.replace('Path("/app/', f'Path("{workspace_s}/')
        content = content.replace("Path('/app/", f"Path('{workspace_s}/")

        adapted_path = workspace / f"_adapted_{task.verifier_test.name}"
        adapted_path.write_text(content, encoding="utf-8")
        return adapted_path

    return task.verifier_test


def _run_verifier(
    task: BenchmarkTask,
    workspace: Path,
    timeout_sec: float = 180.0,
) -> tuple[bool, str]:
    """Execute the task verifier and determine if the solution passed."""
    env = os.environ.copy()
    env["WORKSPACE_DIR"] = str(workspace.resolve())
    env["PYTHONPATH"] = f"{workspace.resolve()!s}{os.pathsep}{env.get('PYTHONPATH', '')}"

    test_file = _prepare_verifier_test(task, workspace)

    # Preference 1: Pytest test file
    if test_file and test_file.is_file():
        python_exe = sys.executable
        cmd = [
            python_exe,
            "-m",
            "pytest",
            str(test_file.resolve()),
            "-v",
            "--tb=short",
        ]
        try:
            res = subprocess.run(
                cmd,
                cwd=str(workspace),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            output = f"=== Pytest Output (exit {res.returncode}) ===\n{res.stdout}\n{res.stderr}"
            return (res.returncode == 0, output)
        except subprocess.TimeoutExpired:
            return (False, f"Verifier timed out after {timeout_sec}s")
        except Exception as e:
            return (False, f"Verifier error: {e}")

    # Preference 2: Shell script verifier
    if task.verifier_script and task.verifier_script.is_file():
        cmd = ["bash", str(task.verifier_script.resolve())]
        try:
            res = subprocess.run(
                cmd,
                cwd=str(workspace),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                shell=(sys.platform == "win32"),
            )
            output = f"=== Script Output (exit {res.returncode}) ===\n{res.stdout}\n{res.stderr}"
            return (res.returncode == 0, output)
        except subprocess.TimeoutExpired:
            return (False, f"Verifier timed out after {timeout_sec}s")
        except Exception as e:
            return (False, f"Verifier error: {e}")

    return (
        False,
        (
            "infra_invalid: No container/verifier infrastructure available "
            "for DeepSWE task in local environment"
        ),
    )


async def run_trial(
    task: BenchmarkTask,
    *,
    trial_dir: Path,
    config: CliConfig | None = None,
    home: Path | None = None,
    max_turns: int | None = None,
    verbose: bool = True,
    use_docker: bool = True,
) -> TrialResult:
    """Execute a single task trial with AgentHarness and score it."""
    trial_dir.mkdir(parents=True, exist_ok=True)
    workspace = trial_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    task_image = (
        task.metadata.get("environment", {}).get("docker_image")
        or task.metadata.get("environment", {}).get("image")
    )
    has_docker = get_docker_cmd() is not None
    should_use_docker = use_docker and has_docker and bool(task_image)

    # When running Docker in WSL or Linux where workspace is on a Windows 9p/drvfs mount (/mnt/...),
    # drvfs does not support POSIX file permission modes (e.g. chmod 600 fails or is ignored).
    # We use a temporary ext4 workspace under /tmp and sync back upon completion.
    temp_ext4_ws: Path | None = None
    if should_use_docker and sys.platform != "win32" and str(workspace.resolve()).startswith("/mnt/"):
        import tempfile

        clean_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in task.id).strip("-")
        temp_ext4_ws = Path(tempfile.mkdtemp(prefix=f"pi-ws-{clean_name}-"))
        active_ws = temp_ext4_ws
    else:
        active_ws = workspace

    docker_session: DockerContainerSession | None = None
    custom_tools: list[Any] | None = None
    junctions: list[str] = []

    if should_use_docker:
        try:
            docker_session = DockerContainerSession(task, active_ws)
            docker_session.setup()
            _copy_task_environment(task, active_ws)
            tools_dict = create_all_tools(str(active_ws.resolve()))
            tools_dict["bash"] = create_docker_bash_tool(
                docker_session.container_name, cwd="/app"
            )
            custom_tools = list(tools_dict.values())
        except Exception as e:
            if verbose:
                print(f"[DOCKER] Failed to setup container: {e}. Falling back to local.")
            docker_session = None
            custom_tools = None

    if docker_session is None:
        _copy_task_environment(task, active_ws)
        # On Windows, bridge root /app and /data to workspace via NTFS junctions
        if sys.platform == "win32":
            drive = active_ws.drive or "D:"
            app_link = f"{drive}\\app"
            data_link = f"{drive}\\data"
            if not os.path.exists(app_link):
                with contextlib.suppress(Exception):
                    import _winapi

                    _winapi.CreateJunction(str(active_ws.resolve()), app_link)
                    junctions.append(app_link)
            ws_data = active_ws / "data"
            if ws_data.is_dir() and not os.path.exists(data_link):
                with contextlib.suppress(Exception):
                    import _winapi

                    _winapi.CreateJunction(str(ws_data.resolve()), data_link)
                    junctions.append(data_link)

    try:
        home_path = pi_home(home)
        cfg = config or load_config(home_path)
        effective_max_turns = max_turns or task.max_turns or cfg.max_turns or 25
        cfg = replace(cfg, permission="auto", max_turns=effective_max_turns)

        # Initialize session
        sessions_dir = trial_dir / "session"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        repo = JsonlSessionRepo(sessions_dir)
        session = await repo.create({"cwd": str(active_ws.resolve())})

        resources = await load_session_resources(cwd=active_ws, config=cfg)
        harness = await create_session_harness(
            session=session,
            cwd=active_ws,
            config=cfg,
            stream_fn=default_stream_fn(),
            resources=resources,
            home=home_path,
            tools=custom_tools,
        )

        if verbose:
            mode_str = "Docker" if docker_session else "Local"
            print(f"\n[EVAL] Starting task: {task.id} ({mode_str})")
            print(f"[EVAL] Model: {cfg.provider}:{cfg.model_id} | Max Turns: {effective_max_turns}")
            print(f"[EVAL] Workspace: {active_ws}")

        start_time = time.perf_counter()
        trial_error: str | None = None

        try:
            # Prompt the agent to begin solving the task
            await asyncio.wait_for(
                harness.prompt(task.instruction),
                timeout=task.agent_timeout_sec,
            )
        except TimeoutError:
            trial_error = f"Agent execution timed out after {task.agent_timeout_sec}s"
            if verbose:
                print(f"[EVAL] Timeout: {trial_error}")
        except Exception as e:
            trial_error = f"Agent loop error: {e}"
            if verbose:
                print(f"[EVAL] Error: {trial_error}")

        duration = time.perf_counter() - start_time

        # Inspect messages from session to compute turn and token statistics
        entries = await session.get_entries()
        assistant_messages: list[AssistantMessage] = []
        trajectory: list[dict[str, Any]] = []

        for entry in entries:
            raw_msg = getattr(entry, "message", None)
            if raw_msg is not None:
                if isinstance(raw_msg, dict):
                    trajectory.append(raw_msg)
                    if raw_msg.get("role") == "assistant":
                        with contextlib.suppress(Exception):
                            assistant_messages.append(AssistantMessage.model_validate(raw_msg))
                elif hasattr(raw_msg, "model_dump"):
                    trajectory.append(raw_msg.model_dump())
                    if getattr(raw_msg, "role", None) == "assistant":
                        if isinstance(raw_msg, AssistantMessage):
                            assistant_messages.append(raw_msg)
                        else:
                            assistant_messages.append(
                                AssistantMessage.model_validate(raw_msg.model_dump())
                            )

        # Calculate token counts
        total_input = 0
        total_output = 0
        total_cached = 0
        total_reasoning = 0
        total_tokens = 0
        reported_cost = 0.0

        no_action_turns = 0
        total_turns = len(assistant_messages)

        for msg in assistant_messages:
            u = msg.usage
            total_input += u.input
            total_output += u.output
            total_cached += u.cacheRead
            total_reasoning += u.reasoningTokens
            total_tokens += u.totalTokens or (u.input + u.output)
            if u.cost and u.cost.total:
                reported_cost += u.cost.total

            # Check for action tool calls
            action_calls = [
                b
                for b in msg.content
                if isinstance(b, dict)
                and b.get("type") == "toolCall"
                and b.get("name") in ("write", "edit", "bash")
            ]
            if not action_calls:
                no_action_turns += 1

        cache_hit_rate = (
            (total_cached / (total_input + total_cached))
            if (total_input + total_cached) > 0
            else 0.0
        )

        cost_usd = (
            reported_cost
            if reported_cost > 0
            else _calculate_cost(
                input_tokens=total_input,
                cached_tokens=total_cached,
                output_tokens=total_output,
                provider=cfg.provider,
            )
        )

        # Run verifier
        if verbose:
            print("[EVAL] Running verifier...")

        if docker_session is not None and docker_session.is_running:
            passed, verifier_output = docker_session.run_verifier(
                timeout_sec=task.verifier_timeout_sec
            )
        else:
            passed, verifier_output = _run_verifier(
                task,
                active_ws,
                timeout_sec=task.verifier_timeout_sec,
            )

        if "infra_invalid" in verifier_output:
            status: Any = "infra_invalid"
        elif passed:
            status = "success"
        elif trial_error:
            status = "error"
        else:
            status = "failure"

        if verbose:
            print(
                f"[EVAL] Result: {status.upper()} "
                f"(Duration: {duration:.1f}s, Turns: {total_turns})"
            )
            cache_str = f"{cache_hit_rate * 100:.1f}%"
            print(f"[EVAL] Tokens: {total_tokens} (Cache: {cache_str}, Cost: ${cost_usd:.4f})")

        # Save trajectory and verifier output
        trajectory_file = trial_dir / "trajectory.json"
        with open(trajectory_file, "w", encoding="utf-8") as f:
            import json

            json.dump(trajectory, f, indent=2, ensure_ascii=False)

        verifier_file = trial_dir / "verifier_output.txt"
        verifier_file.write_text(verifier_output, encoding="utf-8")

        result = TrialResult(
            id=task.id,
            title=task.name,
            suite=task.suite,
            status=status,
            success=passed,
            duration_seconds=round(duration, 2),
            cost_first_cold_usd=round(cost_usd, 4),
            turns=total_turns,
            no_action_turns=no_action_turns,
            cache_hit_rate_normalized=round(cache_hit_rate, 4),
            input_tokens=total_input,
            output_tokens=total_output,
            cached_tokens=total_cached,
            reasoning_tokens=total_reasoning,
            total_tokens=total_tokens,
            error=trial_error,
            verifier_output=verifier_output,
            trajectory_file=str(trajectory_file.resolve()),
            metadata={
                "provider": cfg.provider,
                "model_id": cfg.model_id,
                "difficulty": task.difficulty,
                "runtime": "docker" if docker_session else "local",
            },
        )

        # Write trial.json
        trial_json_path = trial_dir / "trial.json"
        trial_json_path.write_text(result.to_json(indent=2), encoding="utf-8")

        return result
    finally:
        if docker_session is not None:
            docker_session.teardown()
        if temp_ext4_ws is not None and temp_ext4_ws.is_dir():
            with contextlib.suppress(Exception):
                # Sync back generated files to workspace before cleanup
                shutil.copytree(temp_ext4_ws, workspace, dirs_exist_ok=True)
                shutil.rmtree(temp_ext4_ws)
        for j in junctions:
            with contextlib.suppress(Exception):
                os.rmdir(j)
