"""``zypi -p "..."`` while only zypi is signalled: do the agent and its tool call outlive it?

``-p`` runs the agent as a plain child that shares the terminal, so zypi has no stdin pipe to close
as a stop request (the TUI's way). Instead zypi sets ``PI_AGENT_PARENT_PID`` and the agent stops
itself when that process is gone. The agent here is ``tests/_print_agent.py``: the real one-shot
``main()`` of pi_agent_cli with the LLM replaced by one scripted turn that runs a long ``bash``
call. Signals go to zypi only (what ``kill $pid``, ``timeout`` or a supervisor does).

    print_exit.py [--zypi PATH] [--python PATH] [--signals TERM,KILL,HUP,INT]

No terminal is needed. Exit status 0 only when nothing was left behind in every case.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import signal
import subprocess
import time
from pathlib import Path

import pty_term as pt
import zypi_env as env

AGENT = env.AGENT_TESTS / "_print_agent.py"
START_SECONDS = 30.0
WATCH_SECONDS = 10.0


def read_pid(path: Path) -> int | None:
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def told_parent(agent_pid: int) -> str:
    """``PI_AGENT_PARENT_PID`` as the agent was started with it (``/proc`` keeps the original)."""
    try:
        raw = Path(f"/proc/{agent_pid}/environ").read_bytes()
    except OSError:
        return "?"
    for item in raw.split(b"\0"):
        if item.startswith(b"PI_AGENT_PARENT_PID="):
            return item.split(b"=", 1)[1].decode()
    return "unset"


def run(zypi: str, python: str, sig: signal.Signals) -> tuple[bool, str]:
    root = env.scratch("pi-print-")
    tool_pidfile = root / "tool.pid"
    env_file = root / "tool.env"
    agent_pidfile = root / "agent.pid"
    run_env = env.base_env(
        root,
        PI_AGENT_COMMAND=f"{python} {AGENT}",
        PI_TEST_TOOL_PIDFILE=str(tool_pidfile),
        PI_TEST_TOOL_ENVFILE=str(env_file),
        PI_TEST_AGENT_PIDFILE=str(agent_pidfile),
    )
    marker = env.marker(root)
    proc = subprocess.Popen(
        [zypi, "-p", "hello"],
        env=run_env,
        cwd=str(root / "work"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + START_SECONDS
        tool = agent = None
        while time.monotonic() < deadline and not (tool and agent):
            tool, agent = read_pid(tool_pidfile), read_pid(agent_pidfile)
            if tool is not None and not pt.alive(tool):
                tool = None
            time.sleep(0.1)
        if not (tool and agent):
            return False, "the agent never got to its tool call"
        told = told_parent(agent)
        tool_env = env_file.read_text().strip() if env_file.exists() else "?"
        os.kill(proc.pid, sig)
        t0 = time.monotonic()
        gone: dict[str, float] = {}
        pids = {"zypi": proc.pid, "agent": agent, "tool": tool}
        while time.monotonic() - t0 < WATCH_SECONDS and len(gone) < len(pids):
            if proc.poll() is not None and "zypi" not in gone:
                gone["zypi"] = time.monotonic() - t0
            for name in ("agent", "tool"):
                if name not in gone and not pt.alive(pids[name]):
                    gone[name] = time.monotonic() - t0
            time.sleep(0.05)
        left = [f"{name} pid {pids[name]}" for name in pids if name not in gone]
        problems = list(left)
        if told != str(proc.pid):
            problems.append(f"agent was told parent {told}, zypi is {proc.pid}")
        if tool_env != "unset":
            problems.append(f"the tool inherited PI_AGENT_PARENT_PID={tool_env}")
        times = ", ".join(f"{n} gone {t:.1f}s" for n, t in gone.items())
        return not problems, times + (f"  <-- {'; '.join(problems)}" if problems else "")
    finally:
        pt.kill_marked(marker)
        with contextlib.suppress(OSError):
            proc.kill()
        proc.wait()
        env.remove(root)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--zypi", help="the zypi binary (default: $ZYPI, then tui/target/*/zypi)")
    ap.add_argument(
        "--python", help="interpreter with pi_agent_cli (default: $PI_PYTHON, then this)"
    )
    ap.add_argument("--signals", default="TERM,KILL,HUP,INT")
    args = ap.parse_args()
    zypi = env.find_zypi(args.zypi)
    python = env.agent_python(args.python)
    bad = 0
    print("zypi -p hello (agent: tests/_print_agent.py), the signal goes to zypi only")
    for name in args.signals.split(","):
        sig = signal.Signals[f"SIG{name.strip().upper()}"]
        ok, detail = run(zypi, python, sig)
        print(f"[{'ok  ' if ok else 'LEAK'}] SIG{sig.name[3:]:4} {detail}", flush=True)
        bad += 0 if ok else 1
    print(f"\n{'all' if not bad else 'not all'} cases left nothing behind")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
