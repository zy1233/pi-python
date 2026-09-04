"""Task loader for FrontierHarness Eval and Terminal-Bench style tasks."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pi_agent_cli.benchmarks.models import BenchmarkTask


def load_task_from_dir(task_dir: Path | str) -> BenchmarkTask:
    """Parse a task directory containing task.toml and instruction.md."""
    task_path = Path(task_dir).resolve()
    toml_path = task_path / "task.toml"
    instruction_path = task_path / "instruction.md"

    if not task_path.is_dir():
        raise FileNotFoundError(f"Task directory does not exist: {task_path}")
    if not instruction_path.is_file():
        raise FileNotFoundError(f"Missing instruction.md in: {task_path}")

    config: dict[str, Any] = {}
    if toml_path.is_file():
        with open(toml_path, "rb") as f:
            config = tomllib.load(f)

    # Resolve ID and Suite
    task_section = config.get("task", {})
    metadata_section = config.get("metadata", {})
    raw_name = task_section.get("name") or metadata_section.get("task_id") or task_path.name

    if "/" in raw_name:
        suite, name = raw_name.split("/", 1)
    else:
        suite = "terminal-bench"
        name = raw_name

    task_id = f"{suite}/{name}" if "/" not in raw_name else raw_name

    instruction = instruction_path.read_text(encoding="utf-8").strip()

    agent_section = config.get("agent", {})
    verifier_section = config.get("verifier", {})
    env_section = config.get("environment", {})

    agent_timeout_sec = float(agent_section.get("timeout_sec") or 300.0)
    verifier_timeout_sec = float(verifier_section.get("timeout_sec") or 180.0)
    max_turns = int(agent_section.get("max_turns") or 30)
    difficulty = str(metadata_section.get("difficulty") or "medium")
    tags = list(task_section.get("keywords") or metadata_section.get("tags") or [])
    workdir_relative = str(env_section.get("workdir") or ".")

    # Find verifier script or test file
    test_sh = task_path / "tests" / "test.sh"
    test_outputs = task_path / "tests" / "test_outputs.py"

    verifier_script = test_sh if test_sh.is_file() else None
    verifier_test = test_outputs if test_outputs.is_file() else None

    return BenchmarkTask(
        id=task_id,
        name=name,
        suite=suite,
        instruction=instruction,
        task_dir=task_path,
        agent_timeout_sec=agent_timeout_sec,
        verifier_timeout_sec=verifier_timeout_sec,
        max_turns=max_turns,
        difficulty=difficulty,
        tags=tags,
        workdir_relative=workdir_relative,
        verifier_script=verifier_script,
        verifier_test=verifier_test,
        metadata=config,
    )


def discover_tasks(
    tasks_root: Path | str,
    *,
    filter_names: list[str] | set[str] | None = None,
) -> list[BenchmarkTask]:
    """Scan a root directory for all subdirectories containing task.toml / instruction.md."""
    root = Path(tasks_root).resolve()
    if not root.is_dir():
        return []

    tasks: list[BenchmarkTask] = []
    # If the root itself is a single task
    if (root / "instruction.md").is_file():
        tasks.append(load_task_from_dir(root))
        return tasks

    # Check immediate children and one level deeper
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "instruction.md").is_file():
            try:
                task = load_task_from_dir(child)
                tasks.append(task)
            except Exception:
                continue
        elif child.is_dir():
            # Check nested subdirectories (e.g. tasks/terminal-bench/...)
            for subchild in sorted(child.iterdir()):
                if subchild.is_dir() and (subchild / "instruction.md").is_file():
                    try:
                        task = load_task_from_dir(subchild)
                        tasks.append(task)
                    except Exception:
                        continue

    if filter_names:
        filter_set = {n.lower() for n in filter_names}
        tasks = [
            t
            for t in tasks
            if t.name.lower() in filter_set
            or t.id.lower() in filter_set
            or any(f in t.name.lower() for f in filter_set)
        ]

    return tasks
