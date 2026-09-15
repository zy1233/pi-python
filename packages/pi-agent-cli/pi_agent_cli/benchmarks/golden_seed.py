"""Golden Seed Snapshot provisioning and management for benchmark tasks.

Provides "provision once, fresh restore per task" functionality aligning with
FrontierHarness Eval golden checkpoint principles.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pi_agent_cli.benchmarks.docker_runner import (
    get_docker_cmd,
    run_docker_sync,
    to_docker_mount_path,
)
from pi_agent_cli.benchmarks.models import BenchmarkTask


def get_git_commit(cwd: Path | None = None) -> str:
    """Return the current Git commit hash or 'unknown'."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(cwd) if cwd else None,
        )
        return res.stdout.strip()
    except Exception:
        return "unknown"


def get_seed_dir_name(task_id: str) -> str:
    """Convert a task ID (e.g. 'terminal-bench/regex-log') to a filesystem-safe seed dir name."""
    return task_id.replace("/", "_")


def get_seed_dir(seeds_dir: Path | str, task: BenchmarkTask) -> Path | None:
    """Return the seed directory for a task if it exists and contains files."""
    base = Path(seeds_dir).resolve()
    # Check normalized name (e.g. terminal-bench_regex-log)
    candidate = base / get_seed_dir_name(task.id)
    if candidate.is_dir() and any(candidate.iterdir()):
        return candidate

    # Also check double underscore alternative
    alt = base / task.id.replace("/", "__")
    if alt.is_dir() and any(alt.iterdir()):
        return alt

    # Also check task.name
    cand_name = base / task.name
    if cand_name.is_dir() and any(cand_name.iterdir()):
        return cand_name

    return None


def is_seed_ready(seeds_dir: Path | str, task: BenchmarkTask) -> bool:
    """Check if a valid seed exists for the given task."""
    return get_seed_dir(seeds_dir, task) is not None


def load_golden_manifest(seeds_dir: Path | str) -> dict[str, Any] | None:
    """Load the golden-manifest.json from the seeds directory if present."""
    manifest_file = Path(seeds_dir).resolve() / "golden-manifest.json"
    if not manifest_file.is_file():
        return None
    try:
        with open(manifest_file, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def provision_golden_seeds(
    tasks: list[BenchmarkTask],
    seeds_dir: Path | str,
    *,
    verbose: bool = True,
    pull_timeout: float = 600.0,
    force: bool = False,
) -> Path:
    """Provision golden seed directories for the given benchmark tasks.

    Extracts initial workspace files (/app) from each task's Docker image into
    a read-only seed directory, and writes a golden-manifest.json recording
    image digests, file counts, and pi-python git commit.
    """
    if not get_docker_cmd():
        raise RuntimeError("Docker is not available to provision golden seeds.")

    seeds_path = Path(seeds_dir).resolve()
    seeds_path.mkdir(parents=True, exist_ok=True)

    git_commit = get_git_commit()
    created_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest: dict[str, Any] = {
        "version": "1.0",
        "pi_python_commit": git_commit,
        "created_at": created_at,
        "seeds_dir": str(seeds_path),
        "seeds": {},
    }

    # Load existing manifest if present to preserve unchanged entries
    existing = load_golden_manifest(seeds_path)
    if existing and isinstance(existing.get("seeds"), dict):
        manifest["seeds"].update(existing["seeds"])

    if verbose:
        print(f"\n{'=' * 70}")
        print(" Provisioning Golden Seeds for Benchmark Tasks")
        print(f" Target Directory: {seeds_path}")
        print(f" Commit: {git_commit}")
        print(f" Total Tasks: {len(tasks)}")
        print(f"{'=' * 70}\n")

    for idx, task in enumerate(tasks, 1):
        image = task.metadata.get("environment", {}).get("docker_image") or task.metadata.get(
            "environment", {}
        ).get("image")
        if not image:
            if verbose:
                print(f"[{idx}/{len(tasks)}] Skipping {task.id} (no docker_image specified)")
            continue

        seed_dir_name = get_seed_dir_name(task.id)
        task_seed_dir = seeds_path / seed_dir_name

        if task_seed_dir.is_dir() and any(task_seed_dir.iterdir()) and not force:
            if verbose:
                print(f"[{idx}/{len(tasks)}] {task.id}: Seed already exists, skipping extraction.")
            continue

        if verbose:
            print(f"[{idx}/{len(tasks)}] Provisioning seed for {task.id} (image: {image})...")

        # 1. Pull image if needed
        inspect = run_docker_sync(["image", "inspect", image])
        if inspect.returncode != 0:
            if verbose:
                print(f"  Pulling image {image}...")
            pull = run_docker_sync(["pull", image], timeout=pull_timeout)
            if pull.returncode != 0:
                print(f"  Warning: Failed to pull {image}: {pull.stderr.strip()}")
                continue

        # Get image digest / ID
        image_digest = "unknown"
        digest_res = run_docker_sync(["image", "inspect", "--format={{.Id}}", image])
        if digest_res.returncode == 0 and digest_res.stdout.strip():
            image_digest = digest_res.stdout.strip()

        # 2. Extract /app into seed dir
        task_seed_dir.mkdir(parents=True, exist_ok=True)
        tmp_container = f"seed-{seed_dir_name}-{int(time.time())}"
        with contextlib.suppress(Exception):
            run_docker_sync(["rm", "-f", tmp_container])

        create_res = run_docker_sync(["create", "--name", tmp_container, image])
        if create_res.returncode != 0:
            print(f"  Warning: Failed to create container for {image}: {create_res.stderr.strip()}")
            continue

        mount_path = to_docker_mount_path(task_seed_dir)
        cp_res = run_docker_sync(["cp", f"{tmp_container}:/app/.", mount_path], timeout=180.0)
        run_docker_sync(["rm", "-f", tmp_container])

        if cp_res.returncode != 0:
            print(f"  Warning: Failed to copy /app from {image}: {cp_res.stderr.strip()}")
            continue

        # Count extracted files
        file_count = sum(1 for _ in task_seed_dir.rglob("*") if _.is_file())

        manifest["seeds"][task.id] = {
            "image": image,
            "image_digest": image_digest,
            "seed_dir": seed_dir_name,
            "seed_file_count": file_count,
            "provisioned_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        if verbose:
            print(f"  Extracted {file_count} files into {task_seed_dir.name}")

    # Write manifest
    manifest_file = seeds_path / "golden-manifest.json"
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    if verbose:
        print(f"\nGolden seeds provisioning complete. Manifest saved to: {manifest_file}\n")

    return manifest_file
