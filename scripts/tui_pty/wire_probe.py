"""What one zypi run did, seen from the Python agent's side of the wire.

Shared by ``flags_probe.py`` and ``settings_probe.py``. ``execute`` starts zypi under a PTY with
``wire_log_agent.py`` as its agent (``PI_AGENT_COMMAND``), which starts ``context_spy_agent.py``.
It types a prompt, answers the permission question if one comes, exits, and gives back a ``Run``:
how zypi ended, the label on the prompt box, the screens, the ACP lines the two sides exchanged and
what the LLM call would have been sent. ``summarize`` turns a ``Run`` into a flat dict;
``changes`` lists what is different between two of them.

Why the wire: zypi gives the Python agent no other input. It starts it with no argument and with
no variable but ``PI_HOME``, so a flag or a setting that does not show up in the ACP lines (or in
what the agent does with them) cannot change what the agent does. The plan's 1.P6 and decision 14
are about that. Linux only (it needs a pty).
"""

from __future__ import annotations

import difflib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import zypi_env as env

HERE = Path(__file__).resolve().parent
WIRE_AGENT = HERE / "wire_log_agent.py"
SPY_AGENT = HERE / "context_spy_agent.py"

AGENT_TOML = "\n".join(
    [
        "[model]",
        'provider = "mock"',
        'id = "mock-a"',
        "",
        "[[models]]",
        'id = "mock-b"',
        'name = "Second model"',
        "",
    ]
)
# The welcome page, or the prompt box of a session that starts without it (--resume, a prompt).
READY = re.compile(r"Thanks for trying zypi|╰[─ ].*╯")
REPLY = "Hello from mock"
# What the Python agent's permission question looks like ("Allow once", "Reject").
QUESTION = re.compile(r"Allow once")
# What zypi sends that the Python agent drops at its entry (and that holds pids and times).
IGNORED_METHODS = ("_x.ai/",)
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Variables of this harness: the same in every run, so they say nothing about a flag.
HARNESS_ENV = re.compile(
    r"^(PI_HOME|PI_AGENT_COMMAND|PI_USE_MOCK|PI_PYTHON|PI_TEST_.*|WIRE_.*|SPY_.*)$"
)


@dataclass
class Run:
    name: str
    ready: bool = False
    exit_code: int | None = None  # zypi's, when it ended on its own (a refusal) or at /exit
    early: bool = False  # it ended before the TUI was up
    label: str = ""  # the text in the prompt box's bottom border, e.g. "mock-a · always-approve"
    label_end: str = ""  # the same after the turn (the model name is there only from then on)
    screen_ready: str = ""
    screen_end: str = ""
    answered: bool = False  # the scripted LLM's reply was on the screen
    questions: list[str] = field(default_factory=list)  # the screen at each permission question
    wire: list[dict] = field(default_factory=list)
    spy: list[dict] = field(default_factory=list)
    facts: dict[str, str] = field(default_factory=dict)  # what a scenario's collect() found
    # The scratch folder, and the same with its links followed: zypi reports the second one
    # (/run/shm/... for /dev/shm/...).
    root: str = ""
    root_real: str = ""

    @property
    def asked(self) -> bool:
        return bool(self.questions)

    @property
    def question(self) -> str:
        return self.questions[0] if self.questions else ""


def status_label(screen: str) -> str:
    """The text in the frame of the prompt box's bottom border, where the model and mode show."""
    for line in reversed(screen.splitlines()):
        text = line.strip()
        if text.startswith("╰") and text.endswith("╯"):
            return text.strip("╰╯─ ")
    return ""


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def execute(
    name: str,
    flags: tuple[str, ...] = (),
    *,
    zypi: str,
    python: str,
    config: str = "",
    agent_toml: str = AGENT_TOML,
    keys: tuple[str, ...] = (),
    prompts: tuple[str, ...] = ("go",),
    tool_call: dict | None = None,
    permission: str = "ask",
    answer: str | tuple[str, ...] | None = "\r",
    setup: Callable[[Path], None] | None = None,
    collect: Callable[[Path], dict[str, str]] | None = None,
    env_extra: dict[str, str] | None = None,
    turn_seconds: float = 15.0,
) -> Run:
    """One zypi run in a throw-away home.

    ``answer`` is the keys sent when a permission question shows: one string for every question,
    or one string per question (the last one is used again). ``None`` leaves the first question
    open: the run ends there and zypi is killed.
    """
    answers = (answer,) if isinstance(answer, str) else answer
    import pty_term as pt  # needs pyte and a Linux pty

    root = env.scratch("pi-probe-")
    home, work = root / "home", root / "work"
    (home / "agent.toml").write_text(agent_toml, encoding="utf-8")
    if config:
        (home / "config.toml").write_text(config, encoding="utf-8")
    run_env = env.base_env(
        root,
        PI_AGENT_COMMAND=f"{python} {WIRE_AGENT}",
        PI_USE_MOCK="1",
        PI_TEST_PERMISSION=permission,
        WIRE_LOG=str(root / "wire.jsonl"),
        WIRE_AGENT_SCRIPT=str(SPY_AGENT),
        SPY_LOG=str(root / "spy.jsonl"),
    )
    if tool_call is not None:
        run_env["PI_TEST_TOOL_CALL"] = json.dumps(tool_call)
    run_env.update(env_extra or {})
    run = Run(name, root=str(root), root_real=str(root.resolve()))
    marker = env.marker(root)
    term = None
    try:
        if setup is not None:
            setup(root)
        term = pt.Term([zypi, *flags], run_env, cwd=str(work))
        run.ready = term.wait_for(READY, 25)
        if term.proc.poll() is not None or not run.ready:
            # zypi ended before it drew anything: a refusal (its message is on the screen).
            run.early = True
            run.exit_code = term.wait_exit(3)
            run.screen_end = term.text()
            return run
        term.pump(1.0)  # the label can be drawn a few frames after the first text
        run.screen_ready = term.text()
        for key in keys:
            term.send(key)
            term.pump(1.0)
        run.label = status_label(term.text())
        left_open = False
        for turn, prompt in enumerate(prompts, start=1):
            if prompt:
                term.type(prompt + "\r")
            asked_here = False
            end = time.monotonic() + turn_seconds
            while time.monotonic() < end and term.proc.poll() is None:
                term.pump(0.25)
                screen = term.text()
                if QUESTION.search(screen) and not asked_here:
                    asked_here = True
                    run.questions.append(screen)
                    if answers is None:
                        left_open = True
                        break
                    term.send(answers[min(len(run.questions), len(answers)) - 1])
                    term.pump(0.6)
                    continue
                if screen.count(REPLY) >= turn:
                    run.answered = True
                    break
            if left_open:
                break
        run.screen_end = term.text()
        run.label_end = status_label(run.screen_end)
        if collect is not None:
            run.facts.update(collect(root))
        if left_open:
            pass  # a question is open: /exit would only be typed into it
        elif term.proc.poll() is None:
            term.type("/exit")
            term.pump(0.5)
            term.send("\r")
            run.exit_code = term.wait_exit(15)
        else:
            run.exit_code = term.proc.poll()
    finally:
        if term is not None:
            term.kill_group()
        pt.kill_marked(marker)
        run.wire = read_jsonl(root / "wire.jsonl")
        run.spy = read_jsonl(root / "spy.jsonl")
        env.remove(root)
    return run


def keep(run: Run, folder: Path) -> None:
    """Write what ``run`` recorded into ``folder``: the wire, the spy log and the two screens."""
    folder.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", run.name).strip("_") or "run"
    for suffix, rows in (("wire", run.wire), ("spy", run.spy)):
        text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
        (folder / f"{stem}.{suffix}.jsonl").write_text(text + "\n", encoding="utf-8")
    (folder / f"{stem}.screen-ready.txt").write_text(run.screen_ready + "\n", encoding="utf-8")
    (folder / f"{stem}.screen-end.txt").write_text(run.screen_end + "\n", encoding="utf-8")


# ---- reading a run ---------------------------------------------------------------------------
def normalize(value: object, run: Run) -> object:
    """Without the scratch folder's name and the random ids, so that two runs can be compared."""
    if isinstance(value, str):
        text = value
        for root in (run.root_real, run.root):
            if root:
                text = text.replace(root, "<root>")
        text = UUID.sub("<uuid>", text)
        return text if len(text) <= 4000 else text[:4000] + "..."
    if isinstance(value, list):
        return [normalize(item, run) for item in value]
    if isinstance(value, dict):
        return {key: normalize(item, run) for key, item in value.items()}
    return value


def flatten(value: object, prefix: str, out: dict[str, object]) -> None:
    if isinstance(value, dict):
        if not value:
            out[prefix] = "{}"
        for key, item in value.items():
            flatten(item, f"{prefix}.{key}", out)
    elif isinstance(value, list) and value and all(isinstance(i, (dict, list)) for i in value):
        for index, item in enumerate(value):
            flatten(item, f"{prefix}[{index}]", out)
    else:
        out[prefix] = value


def messages(run: Run, direction: str) -> list[dict]:
    rows = []
    for record in run.wire:
        if record.get("d") != direction:
            continue
        try:
            rows.append(json.loads(record["line"]))
        except (KeyError, json.JSONDecodeError):
            continue
    return rows


def summarize(run: Run) -> dict[str, object]:
    """Everything one run showed, as ``path -> value``. Paths and values hold no scratch path."""
    out: dict[str, object] = {
        "ready": run.ready,
        "label": run.label,
        "label.after_turn": run.label_end,
    }
    if run.early:
        what = "never ready" if run.exit_code is None else f"exit {run.exit_code}"
        out["refused"] = f"{what}: {first_line(run.screen_end)}"
        return out
    out["answered"] = run.answered
    out["asked"] = run.asked
    out["exit"] = run.exit_code
    starts = [r for r in run.wire if r.get("d") == "start"]
    if starts:
        start = starts[0]
        out["agent.argv"] = start.get("argv")
        for key, value in (start.get("env") or {}).items():
            if not HARNESS_ENV.match(key):
                out[f"agent.env.{key}"] = normalize(value, run)
        out["agent.env_names"] = sorted(
            n for n in start.get("env_names", []) if not HARNESS_ENV.match(n)
        )
    seen: dict[str, int] = {}
    for msg in messages(run, "c2a"):
        method = msg.get("method")
        if not method or method.startswith(IGNORED_METHODS):
            continue
        index = seen.get(method, 0)
        seen[method] = index + 1
        if index >= 3:
            continue
        flatten(normalize(msg.get("params", {}), run), f"c2a {method}[{index}]", out)
    for index, msg in enumerate(m for m in messages(run, "a2c") if "error" in m):
        out[f"a2c error[{index}]"] = normalize(msg["error"], run)
    out["spy.calls"] = len(run.spy)
    if run.spy:
        first = run.spy[0]
        out["spy.model"] = f"{first['model']['provider']}/{first['model']['id']}"
        out["spy.model.reasoning"] = first["model"]["reasoning"]
        out["spy.reasoning"] = first.get("reasoning")
        out["spy.tools"] = first.get("tools")
        out["spy.system_prompt"] = normalize(first.get("system_prompt"), run)
        out["spy.last_text"] = first.get("last_text")
    for key, value in run.facts.items():
        out[f"fact.{key}"] = normalize(value, run)
    return out


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:160]
    return ""


def show(value: object) -> str:
    if isinstance(value, str):
        text = value.replace("\n", "\\n")
        return text if len(text) <= 100 else f"<{len(value)} chars> {text[:60]}..."
    return json.dumps(value, ensure_ascii=False)


def text_diff(old: str, new: str) -> str:
    """For two long texts (a system prompt): the lines that were added and removed."""
    lines = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if line.startswith(("+++", "---", "@@")):
            continue
        lines.append(line[:110])
    shown = lines[:6]
    more = f" (+{len(lines) - 6} more lines)" if len(lines) > 6 else ""
    return f"{len(old)} -> {len(new)} chars: " + " | ".join(shown) + more


def changes(base: dict[str, object], other: dict[str, object]) -> list[str]:
    """What differs between two summaries: ``key: old -> new``; an empty list means no effect."""
    if "refused" in other:
        return [f"refused: {other['refused']}"]
    if "refused" in base:
        return ["the base run was refused"]
    out = []
    for key in sorted(set(base) | set(other)):
        old, new = base.get(key, "(absent)"), other.get(key, "(absent)")
        if old == new:
            continue
        if isinstance(old, list) and isinstance(new, list) and key == "agent.env_names":
            added, gone = sorted(set(new) - set(old)), sorted(set(old) - set(new))
            out.append(f"{key}: +{added} -{gone}")
        elif isinstance(old, str) and isinstance(new, str) and (len(old) > 100 or len(new) > 100):
            out.append(f"{key}: {text_diff(old, new)}")
        else:
            out.append(f"{key}: {show(old)} -> {show(new)}")
    return out
