#!/usr/bin/env python
"""What a pure-ACP session list costs (plan 0.7, ADR1).

ADR1 moves the TUI from reading session files itself to asking the agent over ACP. The price is
that a list is no longer a local read: the agent has to be running first, and it opens one file
per session. This script measures that price on a synthetic home, with the real agent
(``python -m pi_agent_cli``, mock LLM) over stdio, as the TUI talks to it:

* ``start``    - spawn the agent until ``initialize`` is answered (process start and imports);
* ``list``     - ``session/list`` with the project's ``cwd``, the first call after start: the
  first page, which is what the TUI can show; ``list 2nd`` is the same call again (the agent
  keeps nothing between calls, so this shows the cost of the work itself, without the
  first-call imports);
* ``pages``    - every page of that listing, following ``nextCursor`` (how many pages in brackets);
* ``all``      - the first page of ``session/list`` without ``cwd`` (a picker across projects);
* ``repo``, ``repo page`` and ``preview`` - the parts of a list, measured in-process:
  ``JsonlSessionRepo.list`` (one header read per session file, whatever the ``cwd``),
  ``JsonlSessionRepo.list_page`` (the first page only) and ``read_session_previews`` (title and
  mtime of the sessions that match);
* ``load``     - ``session/load`` of the newest session of the project (``--entries`` entries).

Times are medians of ``--runs`` runs, in milliseconds. The OS file cache is warm (the files were
just written); a cold disk is slower and is not simulated. Run it where the home will live: the
file system decides most of the numbers (NTFS and 9p are far slower than ext4).

    python scripts/session_list_bench.py --sessions 10 100 1000 --runs 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, spawn_agent_process

WIRE_TIMEOUT = 600.0
PROJECT_EVERY = 3  # one session in three belongs to the project being listed
OTHER_PROJECTS = 20  # the rest are spread over this many other directories
FILLER = "x" * 900  # an assistant reply of about a kilobyte


class _Client:
    """The ACP client side: the bench only counts what the agent sends."""

    def __init__(self) -> None:
        self.updates = 0

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates += 1

    async def request_permission(self, **kwargs: Any) -> Any:  # no tool runs, so never asked
        raise RuntimeError("unexpected permission request")


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _file_name(created_at: str, session_id: str) -> str:
    # The repo's own naming (JsonlSessionRepo._session_path).
    safe = created_at.replace(":", "").replace("-", "").replace(".", "").replace("+", "")
    return f"{safe}-{session_id}.jsonl"


def _entry(entry_id: str, parent: str | None, role: str, text: str, at: datetime) -> str:
    """A message entry in the shape the agent itself writes (so ``session/load`` accepts it)."""
    message: dict[str, Any] = {
        "role": role,
        "content": [{"type": "text", "text": text}],
    }
    if role == "assistant":
        message.update(
            api="langchain",
            provider="mock",
            model="mock",
            usage={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 0},
            stopReason="stop",
        )
    message["timestamp"] = int(at.timestamp() * 1000)
    entry = {
        "type": "message",
        "id": entry_id,
        "parentId": parent,
        "timestamp": _stamp(at),
        "message": message,
    }
    return json.dumps(entry)


def write_sessions(sessions_dir: Path, count: int, project: str, entries: int) -> None:
    """``count`` session files, a third of them the project's, the rest in other directories.

    A list reads only each file's header and its first user message; the remaining entries
    give the files a realistic size (about ``entries`` KB) and make ``session/load`` work.
    """
    sessions_dir.mkdir(parents=True, exist_ok=True)
    first = datetime.now(UTC) - timedelta(minutes=7 * count)
    for index in range(count):
        cwd = project if index % PROJECT_EVERY == 0 else f"{project}-other{index % OTHER_PROJECTS}"
        created = first + timedelta(minutes=7 * index)
        session_id = uuid.uuid4().hex
        header = {
            "type": "session",
            "version": 3,
            "id": session_id,
            "timestamp": _stamp(created),
            "cwd": cwd,
        }
        lines = [json.dumps(header)]
        parent: str | None = None
        for position in range(entries):
            entry_id = f"{index % 0x100000:05x}{position % 0x1000:03x}"
            role = "user" if position % 2 == 0 else "assistant"
            text = f"session {index}: question {position}" if role == "user" else FILLER
            moment = created + timedelta(seconds=position + 1)
            lines.append(_entry(entry_id, parent, role, text, moment))
            parent = entry_id
        path = sessions_dir / _file_name(_stamp(created), session_id)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _timed(awaitable: Any) -> tuple[float, Any]:
    started = time.perf_counter()
    result = await asyncio.wait_for(awaitable, WIRE_TIMEOUT)
    return (time.perf_counter() - started) * 1000, result


async def one_run(home: Path, project: str) -> dict[str, float]:
    client = _Client()
    env = {"PI_HOME": str(home), "PI_USE_MOCK": "1"}
    started = time.perf_counter()
    async with spawn_agent_process(
        client,
        sys.executable,
        "-m",
        "pi_agent_cli",
        env=env,
        cwd=home,
        transport_kwargs={"stderr": subprocess.DEVNULL, "shutdown_timeout": 30.0},
    ) as (conn, _process):
        await asyncio.wait_for(conn.initialize(protocol_version=PROTOCOL_VERSION), WIRE_TIMEOUT)
        row = {"start": (time.perf_counter() - started) * 1000}
        row["list"], first = await _timed(conn.list_sessions(cwd=project))
        row["list2"], _ = await _timed(conn.list_sessions(cwd=project))
        started = time.perf_counter()
        pages, listed, cursor = 0, 0, None
        while True:
            response = await asyncio.wait_for(
                conn.list_sessions(cwd=project, cursor=cursor), WIRE_TIMEOUT
            )
            pages, listed, cursor = pages + 1, listed + len(response.sessions), response.next_cursor
            if cursor is None or pages > 10_000:
                break
        row["pages"] = (time.perf_counter() - started) * 1000
        row["page_count"] = float(pages)
        row["listed"] = float(listed)
        row["all"], everything = await _timed(conn.list_sessions())
        row["all_listed"] = float(len(everything.sessions))
        row["bytes"] = float(len(first.model_dump_json(by_alias=True, exclude_none=True)))
        if first.sessions:
            target = first.sessions[0].session_id
            row["load"], _ = await _timed(
                conn.load_session(cwd=project, session_id=target, mcp_servers=[])
            )
            row["replayed"] = float(client.updates)
    return row


async def in_process(home: Path, project: str) -> dict[str, float]:
    """The parts of a list, without the process and the wire."""
    from pi_agent_cli import session_list
    from pi_agent_cli.session_list import read_session_previews
    from pi_agent_harness import JsonlSessionRepo

    page_size = getattr(session_list, "SESSION_LIST_PAGE_SIZE", 50)  # absent before paging

    repo = JsonlSessionRepo(home / "sessions")
    started = time.perf_counter()
    listed = await repo.list({"cwd": project})
    repo_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    await asyncio.to_thread(read_session_previews, [(m.path, m.createdAt) for m in listed])
    preview_ms = (time.perf_counter() - started) * 1000
    result = {"repo": repo_ms, "preview": preview_ms}
    if hasattr(repo, "list_page"):  # the first page only
        started = time.perf_counter()
        await repo.list_page({"cwd": project, "limit": page_size})
        result["repo_page"] = (time.perf_counter() - started) * 1000
    return result


def median(rows: list[dict[str, float]], key: str) -> float:
    return statistics.median(row[key] for row in rows)


async def measure(root: Path, count: int, args: argparse.Namespace) -> dict[str, float]:
    home = root / f"home-{count}"
    project = str(root / "project")
    write_sessions(home / "sessions", count, project, args.entries)
    rows = [await one_run(home, project) for _ in range(args.runs)]
    halves = [await in_process(home, project) for _ in range(args.runs)]
    result = {key: median(rows, key) for key in rows[0]}
    result.update({key: median(halves, key) for key in halves[0]})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--sessions", type=int, nargs="+", default=[10, 100, 1000])
    parser.add_argument("--entries", type=int, default=12, help="entries per session file")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--json", action="store_true", help="print the numbers as JSON")
    args = parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix="session-list-bench-"))
    results: dict[int, dict[str, float]] = {}
    try:
        for count in args.sessions:
            results[count] = asyncio.run(measure(root, count, args))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if args.json:
        print(json.dumps(results, indent=2))
        return 0
    print(f"{platform.system()} {platform.release()}, Python {platform.python_version()}")
    print(f"{args.runs} runs, medians in ms; one session in {PROJECT_EVERY} is the project's\n")
    header = [
        "sessions",
        "listed",
        "start",
        "list",
        "list 2nd",
        "pages (n)",
        "all",
        "repo",
        "repo page",
        "preview",
        "KiB",
    ]
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * len(header))
    for count, r in results.items():
        cells = [
            str(count),
            f"{r['listed']:.0f}",
            f"{r['start']:.0f}",
            f"{r['list']:.0f}",
            f"{r['list2']:.0f}",
            f"{r['pages']:.0f} ({r['page_count']:.0f})",
            f"{r['all']:.0f}",
            f"{r['repo']:.0f}",
            f"{r['repo_page']:.0f}" if "repo_page" in r else "-",
            f"{r['preview']:.0f}",
            f"{r['bytes'] / 1024:.0f}",
        ]
        print("| " + " | ".join(cells) + " |")
    print("\nsession/load of the newest session of the project:")
    for count, r in results.items():
        load_ms, replayed = r.get("load", float("nan")), r.get("replayed", 0)
        print(f"  {count} sessions: {load_ms:.0f} ms, {replayed:.0f} updates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
