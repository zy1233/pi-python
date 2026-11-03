"""The Auto permission mode in one session.

``auto_rules.decide`` judges one call from a context. This module is what a session needs
around it:

* It builds the context for each decision (in a worker thread: it reads files), and checks the
  parts of it that the rules cannot check themselves when Auto starts and when a call comes
  (a project folder that is not there, a ``PATH`` with a relative entry, a ``bash`` tool that
  does not run bash). While a check fails, every call that is not harmless asks.
* It says which tool object is a built-in one by identity, not by name (``builtin``): an
  extension may register a tool under the name ``bash``.
* It keeps the calls of one assistant message in order: decide, run, decide, run. The rules
  judge a call on the files as they are at that moment, so a call must not be judged before the
  earlier call of its message has run. The harness runs calls one by one while Auto is on, and
  the guard in ``decide`` asks about a call that would be judged too early.
* It keeps the record of the decisions (``pi.auto_decision`` entries of the session file, and
  the command ``/auto-log``), and the limits of the ``bash`` calls that Auto let through.
* It gives a ``bash`` call that the rules leave open to the model step (``auto_classifier``)
  when ``classifier = "model"``: with the user's own messages of the session, which it keeps
  (``note_user_message``, ``load_history``). With ``classifier = "rules"`` such a call asks.

Fail closed: an error, a timeout or an unreadable state is an ASK, never an ALLOW (invariant
9b). A floor rule is never overridden by the model: the rules answer first, and only a call that
they leave open reaches it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import time
from collections import Counter, OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pi_agent_cli.auto_classifier import (
    POLICY_HASH,
    ClassifierRequest,
    ModelClassifier,
    ModelTarget,
    UserMessageLog,
)
from pi_agent_cli.auto_reasons import Reason
from pi_agent_cli.auto_rules import (
    AutoContext,
    Outcome,
    RuleDecision,
    ToolCall,
    build_context,
    decide,
    decide_rules_only,
    default_protected_globs,
    is_absolute_folder,
)
from pi_agent_cli.config import MAX_AUTO_MODEL_TIMEOUT_SECONDS, AutoConfig, CliConfig
from pi_agent_core.coding_tools.bash import BashLimits, ShellConfig, get_shell_config

logger = logging.getLogger(__name__)

AUDIT_ENTRY_TYPE = "pi.auto_decision"
LOG_COMMAND = "auto-log"
LOG_LINES = 20
AUTO_EXTENSION_NAME = "pi-auto-mode"

# A decision reads files and runs in a worker thread. A file system that hangs must not hold
# the turn for ever: the call asks instead.
DECISION_TIMEOUT_SECONDS = 20.0

# The tools of ours that the rules know by name. Anything else with such a name is not ours.
BUILTIN_TOOL_NAMES = ("bash", "write", "edit")

_MAX_LIMITS_HELD = 256
_ERROR_REASONS = frozenset(
    {Reason.INTERNAL_ERROR, Reason.CHECKER_UNAVAILABLE, Reason.CHECKER_UNREADABLE}
)


@dataclass(frozen=True)
class AutoVerdict:
    """What Auto decided for one call: ``ALLOW`` or ``ASK`` (Auto never blocks)."""

    outcome: Outcome
    by: str
    reason: Reason
    rule_id: str
    ms: int = 0
    # Set when the call went to the model step (``classifier`` is then ``"model"``).
    classifier: str = "rules"
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    model: str = ""
    # ``session/cancel`` ended the decision: the call is blocked, not put to the user.
    cancelled: bool = False

    @property
    def allows(self) -> bool:
        return self.outcome is Outcome.ALLOW


def _ask(reason: Reason, rule_id: str, *, by: str = "error") -> AutoVerdict:
    return AutoVerdict(Outcome.ASK, by, reason, rule_id)


def _verdict(decision: RuleDecision) -> AutoVerdict:
    by = "error" if decision.reason in _ERROR_REASONS else decision.by.value
    return AutoVerdict(decision.outcome, by, decision.reason, decision.rule_id)


class BashLimitRegistry:
    """The limits for the ``bash`` calls that Auto let through, by tool call id.

    The Bash tool asks for the limits of a call just before it starts it (``take``). A call
    that Auto did not let through (the user said yes to a question) has none: the user chose it.
    An entry that is never taken (the call was blocked later, or cancelled) is dropped when
    newer ones push it out.
    """

    def __init__(self) -> None:
        self._held: OrderedDict[str, BashLimits] = OrderedDict()

    def put(self, tool_call_id: str, limits: BashLimits) -> None:
        self._held[tool_call_id] = limits
        self._held.move_to_end(tool_call_id)
        while len(self._held) > _MAX_LIMITS_HELD:
            self._held.popitem(last=False)

    def take(self, tool_call_id: str) -> BashLimits | None:
        return self._held.pop(tool_call_id, None)

    def clear(self) -> None:
        self._held.clear()


# ----------------------------------------------------------------------- the checks


def path_problems(path_value: str | None) -> list[str]:
    """Why a ``PATH`` is not safe for Auto. An entry that is empty or relative means the
    project folder to the shell: a file there can answer to a command name such as ``git``,
    and the rules judge the name. An unset ``PATH`` is the shell's own default and is fine."""
    if path_value is None:
        return []
    if all(is_absolute_folder(entry) for entry in path_value.split(os.pathsep)):
        return []
    return [
        "PATH has an entry that is empty or not absolute, so a file of the project can run "
        "when a command name is typed. Remove that entry from PATH."
    ]


def shell_problems(resolve: Callable[[str | None], ShellConfig] | None = None) -> list[str]:
    """Why the Bash tool is not running bash. The rules read bash commands only."""
    try:
        shell = (resolve or get_shell_config)(None).shell
    except Exception:
        return ["The Bash tool cannot find a shell to run."]
    name = os.path.basename(shell.replace("\\", "/")).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if name != "bash":
        return [f"The Bash tool runs {name or 'an unnamed shell'}, not bash."]
    return []


def context_problems(ctx: AutoContext) -> list[str]:
    """Why the rules cannot trust *ctx*. Every folder in it has to be absolute (a relative one
    is read from the folder of the process, not the project), and the project folder has to be
    there. The rules ask about every call on their own when a folder is not absolute; this says
    which one, so the user can fix it."""
    problems: list[str] = []
    if not is_absolute_folder(ctx.cwd):
        problems.append("The project folder is not an absolute path.")
    elif not os.path.isdir(ctx.cwd):
        problems.append("The project folder does not exist.")
    if not is_absolute_folder(ctx.home_dir):
        problems.append("The home folder is not known, or it is not an absolute path.")
    if not is_absolute_folder(ctx.pi_home):
        problems.append("The folder of the agent's own files (PI_HOME) is not an absolute path.")
    problems.extend(
        f"extra_roots in [auto] has a folder that is not absolute: {root!r}."
        for root in ctx.extra_roots
        if not is_absolute_folder(root)
    )
    if not all(is_absolute_folder(path) for path in ctx.protected_paths):
        problems.append("A protected file of the project is not an absolute path.")
    return problems


# ---------------------------------------------------------------------- the messages

ENTRY_MESSAGE = (
    "Auto mode is on. These run with no question: tools that only read, edits inside the "
    "project folder, and shell commands that only read. These ask you first: anything outside "
    "the project folder or on the protected list, deletes, network use, secrets, and workflows "
    "(a model never overrides these). Auto never refuses a call by itself: "
    "you can always say yes. Auto does not guard what tools that only read can read (read, "
    "grep, find, ls, fetch_url, web_search). A sandbox is the boundary, not Auto. "
    f"/{LOG_COMMAND} shows what Auto decided."
)

RULES_ENTRY_SENTENCE = (
    " A shell command that Auto cannot show to be safe asks you too (the model step is off: "
    'classifier = "rules" in [auto]).'
)

MODEL_ENTRY_SENTENCE = (
    " A shell command that Auto cannot show to be safe is judged by a model, {model}. It gets "
    "your messages of this session and the command, and answers allow or ask. If it does not "
    "allow the command, does not answer in time, or answers in a way that Auto cannot read, "
    "you are asked."
)

UNKNOWN_MODEL_ENTRY_SENTENCE = (
    ' The model that [auto] names, "{model}", is not the id of a model in [model] or '
    "[[models]]: no model judges, so a shell command that Auto cannot show to be safe asks "
    'you (fix the id, or set classifier = "rules" in [auto]).'
)

_FROM_CONFIG_HINT = (
    ' permission = "auto" in agent.toml means this mode. To run with no question at all, '
    'set permission = "always-approve".'
)

_MAX_SHOWN_ID_CHARS = 64


def shown_model_id(text: str) -> str:
    """A model id from the settings as one line of printable text, cut at 64 characters: it goes
    into a notice and into a log line. Nothing else of a model entry is ever shown with it (a
    ``base_url`` or the name of a key variable can hold a secret)."""
    line = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
    if len(line) > _MAX_SHOWN_ID_CHARS:
        line = line[: _MAX_SHOWN_ID_CHARS - 3].rstrip() + "..."
    return line


def unknown_judge(config: CliConfig) -> str | None:
    """The ``[auto] model`` id that no ``[model]`` or ``[[models]]`` entry has, while the model
    step is on: no model judges then, and every call that needs a judge asks. ``None`` when the
    step is off (``classifier = "rules"``), when no id is set, or when the id is known."""
    if config.auto.classifier == "model" and config.auto_model_is_unknown():
        return config.auto.model
    return None


def entry_message(
    *,
    from_config: bool = False,
    classifier: str = "model",
    model: str = "the model of this session",
    unknown_model: str | None = None,
) -> str:
    """The text for the user when a session is in Auto. *from_config*: the mode comes from
    ``permission = "auto"`` in ``agent.toml``, which meant "no question" in older versions.
    *classifier* is the ``[auto]`` setting of that name, and *model* says which model judges
    when it is ``"model"`` (the user's messages and the command go to it). *unknown_model* is
    the ``[auto] model`` id that no model entry has (``unknown_judge``): no model judges then,
    and the sentence says so in place of the one that names a model."""
    if classifier != "model":
        step = RULES_ENTRY_SENTENCE
    elif unknown_model:
        step = UNKNOWN_MODEL_ENTRY_SENTENCE.format(model=shown_model_id(unknown_model))
    else:
        step = MODEL_ENTRY_SENTENCE.format(model=model)
    return ENTRY_MESSAGE + step + (_FROM_CONFIG_HINT if from_config else "")


def problems_message(problems: Sequence[str]) -> str:
    """The text for the user when Auto cannot check the session: what is wrong, and what it
    means. One line each, so a client shows them as a list."""
    lines = [
        "Auto cannot check this session safely. It asks about every call that is not "
        "read-only until this is fixed:"
    ]
    lines.extend(f"- {problem}" for problem in problems)
    return "\n".join(lines)


def config_problems_message(problems: Sequence[str]) -> str:
    """The text for the user about the values of ``[auto]`` that Auto cannot work with, in a
    session that is not in Auto (the entry message of Auto lists them for a session that is)."""
    return "\n".join(
        [
            "Settings in agent.toml that Auto cannot work with. When Auto is on, it asks about "
            "every call that is not read-only until they are fixed:",
            *(f"- {problem}" for problem in problems),
        ]
    )


def config_notes_message(notes: Sequence[str]) -> str:
    """The text for the user about the values of ``agent.toml`` that were not used."""
    return "\n".join(["Settings in agent.toml that are not used:", *(f"- {n}" for n in notes)])


def format_log(records: Sequence[Any], *, classifier: str = "rules") -> str:
    """The answer of ``/auto-log``: counts, what the model step did, and the last decisions
    (oldest first). *classifier* is the setting of ``[auto]`` now."""
    valid = [record for record in records if isinstance(record, Mapping)]
    if not valid:
        return "Auto has made no decisions in this session."
    allowed = sum(1 for record in valid if record.get("outcome") == "allow")
    asked = sum(1 for record in valid if record.get("outcome") == "ask")
    by_counts: dict[str, int] = {}
    for record in valid:
        by = str(record.get("by", "?"))
        by_counts[by] = by_counts.get(by, 0) + 1
    lines = [
        f"Auto decisions in this session: {len(valid)} (allowed {allowed}, asked {asked}).",
        "Decided by: "
        + ", ".join(
            f"{name} {by_counts.get(name, 0)}" for name in ("rule", "floor", "model", "error")
        )
        + ".",
        _model_line(valid, classifier),
        f"Last {min(LOG_LINES, len(valid))}, oldest first:",
    ]
    for record in valid[-LOG_LINES:]:
        lines.append(
            "  {outcome:<5} {tool:<8} {reason}  [{rule}]  {ms} ms".format(
                outcome=str(record.get("outcome", "?")),
                tool=str(record.get("tool", "?")),
                reason=str(record.get("reason", "?")),
                rule=str(record.get("rule_id", "?")),
                ms=record.get("ms", "?"),
            )
        )
    return "\n".join(lines)


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _model_line(records: Sequence[Mapping[str, Any]], classifier: str) -> str:
    """What the model step did in the records. A record is of the model step when its
    ``classifier`` is ``model``: the model answered (``by`` is ``model``), or the step could not
    (``error``: no answer in time, a failed request, an answer that could not be read) or would
    not (``floor``: the call was too long to send)."""
    judged = [record for record in records if record.get("classifier") == "model"]
    if not judged:
        if classifier == "model":
            return "Model: not called yet (the rules decided every call)."
        return 'Model: off (classifier = "rules"). A command that the rules cannot prove asks you.'
    answered = [record for record in judged if record.get("by") == "model"]
    allowed = sum(1 for record in answered if record.get("outcome") == "allow")
    failed = sum(1 for record in judged if record.get("by") == "error")
    not_sent = sum(1 for record in judged if record.get("by") == "floor")
    tokens_in = int(sum(_number(record.get("tokens_in")) for record in judged))
    tokens_out = int(sum(_number(record.get("tokens_out")) for record in judged))
    cost = sum(_number(record.get("cost_usd")) for record in judged)
    times = sorted(int(_number(record.get("ms"))) for record in judged)
    parts = [
        f"Model: {len(judged)} calls judged, {len(answered)} answered "
        f"(allowed {allowed}, asked {len(answered) - allowed}), {failed} failed, "
        f"{not_sent} not sent",
        f"tokens in {tokens_in}, out {tokens_out}",
        f"cost ${cost:.5f}" if cost > 0 else "cost not reported",
        f"time median {times[len(times) // 2]} ms, slowest {times[-1]} ms",
    ]
    return "; ".join(parts) + "."


# ------------------------------------------------------------------------ the session


def _user_text_of(entry: Any) -> str | None:
    """The text of a session entry that is a message of the role ``user``; ``None`` for any other
    entry. Only text blocks count: no image, no tool result."""
    if getattr(entry, "type", None) != "message":
        return None
    message = getattr(entry, "message", None)
    if not isinstance(message, Mapping) or message.get("role") != "user":
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, Mapping)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    )


class AutoSession:
    """Auto for one session. It is built for every session; ``enter`` turns it on.

    *harness* is the session's ``AgentHarness``. The tool objects that are built-in ones are
    taken from it when this is built, which has to happen before any extension loads.
    *get_config* gives the agent's current configuration (the protected set and the ``[auto]``
    table are read from it), *trusted* says whether the project is trusted now. *classifier* is
    the model step; without one, it is built to ask the session's model (or the model that
    ``[auto] model`` names) through the harness's own stream function. *environ* and
    *resolve_shell* are for tests.
    """

    def __init__(
        self,
        *,
        harness: Any,
        get_config: Callable[[], CliConfig],
        home: Path,
        trusted: Callable[[], bool],
        limits: BashLimitRegistry | None = None,
        classifier: ModelClassifier | None = None,
        environ: Mapping[str, str] | None = None,
        resolve_shell: Callable[[str | None], ShellConfig] | None = None,
    ) -> None:
        self._harness = harness
        self._get_config = get_config
        self._home = home
        self._trusted = trusted
        self._environ = environ
        self._resolve_shell = resolve_shell
        self.limits = limits if limits is not None else BashLimitRegistry()
        self.user_messages = UserMessageLog()
        self.classifier = (
            classifier
            if classifier is not None
            else ModelClassifier(
                self._model_target,
                timeout=lambda: self._get_config().auto.timeout_seconds,
                notify=self._tell,
            )
        )
        self._builtin: dict[str, Any] = {}
        for name in BUILTIN_TOOL_NAMES:
            tool = harness.get_tool(name)
            if tool is not None:
                self._builtin[name] = tool
        self._pi: Any = None
        self._active = False
        self._start_problems: tuple[str, ...] | None = None
        self._check_lock = asyncio.Lock()
        self._open_calls: Counter[str] = Counter()
        self._saved_execution: Any = None
        self._toggled: dict[int, tuple[Any, Any]] = {}
        harness.subscribe(self._on_event)

    # ---- the extension (audit and command)

    def activate(self, pi: Any) -> None:
        """Extension entry point: keeps the API for the audit entries and adds ``/auto-log``."""
        self._pi = pi
        pi.register_command(
            LOG_COMMAND,
            description="Show what Auto decided in this session",
            handler=self._log_command,
        )

    def _log_command(self, args: str) -> None:
        pi = self._pi
        if pi is None:
            return
        entries = pi.get_custom_entries(AUDIT_ENTRY_TYPE)
        records = [getattr(entry, "data", None) for entry in entries]
        pi.send_message(format_log(records, classifier=self.classifier_name))

    def _record(self, tool_call_id: str, tool: str, origin: Any, verdict: AutoVerdict) -> None:
        pi = self._pi
        if pi is None:
            return
        try:
            pi.append_entry(
                AUDIT_ENTRY_TYPE,
                {
                    "tool_call_id": tool_call_id,
                    "tool": tool,
                    "outcome": verdict.outcome.value,
                    "by": verdict.by,
                    "reason": verdict.reason.value,
                    "rule_id": verdict.rule_id,
                    "classifier": verdict.classifier,
                    "ms": verdict.ms,
                    "tokens_in": verdict.tokens_in,
                    "tokens_out": verdict.tokens_out,
                    "cost_usd": verdict.cost_usd,
                    "model": verdict.model,
                    "policy": POLICY_HASH if verdict.classifier == "model" else "",
                    "subagent": bool(origin),
                },
            )
        except Exception:
            logger.warning("Could not record an Auto decision", exc_info=True)

    # ---- turning it on and off

    @property
    def active(self) -> bool:
        return self._active

    @property
    def problems(self) -> tuple[str, ...]:
        """What is wrong now (empty: nothing). Includes the checks that read the environment."""
        return (*(self._start_problems or ()), *self._live_problems())

    async def enter(self) -> tuple[str, ...]:
        """Turn Auto on for the session: run its calls one by one, and check the session.

        Returns the problems (empty: all fine). While there is one, every call that is not
        harmless asks.
        """
        self._active = True
        self._apply_sequential(True)
        self._start_problems = None
        await self._ensure_checked()
        return self.problems

    def leave(self) -> None:
        """Turn Auto off: the harness runs the calls of a message together again."""
        self._active = False
        self._apply_sequential(False)
        self.limits.clear()

    def _apply_sequential(self, on: bool) -> None:
        """Make the harness run the calls of one message one after the other (W-1).

        The loop reads ``tool_execution`` when a run starts and the tools' ``execution_mode``
        for each batch, so both are set: the first covers the next run, the second the next
        message of a run that is going on.
        """
        harness = self._harness
        if on:
            if not self._toggled and self._saved_execution is None:
                self._saved_execution = harness.tool_execution
            harness.tool_execution = "sequential"
            for tool in harness.get_tools():
                if (
                    id(tool) in self._toggled
                    or getattr(tool, "execution_mode", None) == "sequential"
                ):
                    continue
                previous = getattr(tool, "execution_mode", None)
                try:
                    tool.execution_mode = "sequential"
                except Exception:
                    logger.debug("Could not set execution_mode of a tool", exc_info=True)
                    continue
                self._toggled[id(tool)] = (tool, previous)
            return
        if self._saved_execution is not None:
            harness.tool_execution = self._saved_execution
            self._saved_execution = None
        for tool, previous in self._toggled.values():
            try:
                tool.execution_mode = previous
            except Exception:
                logger.debug("Could not restore execution_mode of a tool", exc_info=True)
        self._toggled.clear()

    def _other_calls_open(self, call_id: str) -> bool:
        """Whether a call other than this one has started and not ended. The id comes from the
        model, so two calls of one message can have the same id: the count of an id above one
        is another call too."""
        return any(count > (1 if key == call_id else 0) for key, count in self._open_calls.items())

    def _on_event(self, event: Any, signal: Any | None = None) -> None:
        kind = getattr(event, "type", None)
        if kind == "tool_execution_start":
            self._open_calls[event.tool_call_id] += 1
        elif kind == "tool_execution_end":
            self._open_calls[event.tool_call_id] -= 1
            if self._open_calls[event.tool_call_id] <= 0:
                del self._open_calls[event.tool_call_id]
        elif kind in {"agent_start", "agent_end", "turn_start", "turn_end"}:
            self._open_calls.clear()

    # ---- the checks of the session

    def _live_problems(self) -> list[str]:
        """What is read again before every call. The values of ``[auto]`` are in it: the
        agent reads ``agent.toml`` again when it could not run a turn (``--setup`` while it was
        up), and the new file can hold a value that Auto cannot work with."""
        path_value = (self._environ if self._environ is not None else os.environ).get("PATH")
        return [
            *self._get_config().auto.problems,
            *path_problems(path_value),
            *shell_problems(self._resolve_shell),
        ]

    def _make_context(self, origin_cwd: str | None = None) -> AutoContext:
        auto = self._get_config().auto
        return build_context(
            cwd=self._harness.env.cwd,
            project_trusted=self._trusted(),
            pi_home_dir=str(self._home),
            extra_roots=auto.extra_roots,
            protected_globs=(*default_protected_globs(), *auto.protected),
            config=self._get_config(),
            origin_cwd=origin_cwd,
        )

    def _check_blocking(self) -> tuple[str, ...]:
        return tuple(context_problems(self._make_context()))

    async def _ensure_checked(self) -> None:
        async with self._check_lock:
            if self._start_problems is not None:
                return
            try:
                self._start_problems = await asyncio.wait_for(
                    asyncio.to_thread(self._check_blocking), DECISION_TIMEOUT_SECONDS
                )
            except Exception:
                logger.warning("The check of the Auto context failed", exc_info=True)
                self._start_problems = ("Auto could not read the state of the session.",)

    # ---- the decision

    async def decide(self, event: Any) -> AutoVerdict:
        """The verdict for one ``tool_call`` event. It never raises: any failure is an ASK."""
        started = time.monotonic()
        name = str(getattr(event, "toolName", ""))
        call_id = str(getattr(event, "toolCallId", ""))
        origin = getattr(event, "origin", None)
        try:
            verdict = await asyncio.wait_for(self._decide(event), self._decision_budget())
        except TimeoutError:
            verdict = _ask(Reason.CHECKER_UNAVAILABLE, "session.timeout")
        except Exception:
            logger.warning("Auto failed to decide a call of tool %s", name, exc_info=True)
            verdict = _ask(Reason.INTERNAL_ERROR, "session.error")
        verdict = dataclasses.replace(verdict, ms=int((time.monotonic() - started) * 1000))
        if verdict.allows and name == "bash" and not origin:
            self.limits.put(call_id, self._bash_limits())
        self._record(call_id, name, origin, verdict)
        return verdict

    def _decision_budget(self) -> float:
        """The seconds that one decision may take. The model step has its own timeout, and it
        comes on top of the time that the rules and the context may use."""
        auto = self._get_config().auto
        if auto.classifier != "model":
            return DECISION_TIMEOUT_SECONDS
        return DECISION_TIMEOUT_SECONDS + min(auto.timeout_seconds, MAX_AUTO_MODEL_TIMEOUT_SECONDS)

    def _bash_limits(self) -> BashLimits:
        auto: AutoConfig = self._get_config().auto
        return BashLimits(
            timeout_seconds=auto.bash_timeout_seconds,
            max_output_bytes=auto.bash_max_output_mb * 1024 * 1024,
        )

    async def _decide(self, event: Any) -> AutoVerdict:
        name = str(event.toolName)
        origin = getattr(event, "origin", None)
        origin_cwd: str | None = None
        if origin:
            if not isinstance(origin, Mapping) or origin.get("kind") != "subagent":
                return _ask(Reason.UNCLEAR, "session.unknown_origin", by="floor")
            cwd = origin.get("cwd")
            if not isinstance(cwd, str) or not cwd:
                return _ask(Reason.UNCLEAR, "session.subagent_without_cwd", by="floor")
            origin_cwd = cwd
        await self._ensure_checked()
        # Keep the harness's batches in order, also for a tool that came later (an extension).
        self._apply_sequential(True)
        if self._start_problems or await asyncio.to_thread(self._live_problems):
            return _ask(Reason.UNCLEAR, "session.check_failed", by="floor")
        if not origin and self._other_calls_open(str(event.toolCallId)):
            # An earlier call of this message has not finished: the harness runs this batch
            # in parallel (Auto was switched on while it was being prepared). This call would
            # be judged on files that the earlier one may still change.
            return _ask(Reason.UNCLEAR, "session.parallel_batch", by="floor")
        tool = self._harness.get_tool(name)
        call = ToolCall(
            name=name,
            input=dict(getattr(event, "input", None) or {}),
            annotations=getattr(event, "annotations", None),
            builtin=tool is not None and tool is self._builtin.get(name),
        )
        decision = await asyncio.to_thread(self._decide_blocking, call, origin_cwd)
        if decision.outcome is Outcome.JUDGE:
            return await self._judge(call, origin_cwd)
        return _verdict(decision)

    def _decide_blocking(self, call: ToolCall, origin_cwd: str | None) -> RuleDecision:
        """The rules' decision. Only with ``classifier = "model"`` can it be JUDGE (a call that
        the rules leave open); with ``"rules"`` such a call is an ASK ``unclear`` already."""
        context = self._make_context(origin_cwd)
        if self._get_config().auto.classifier == "model":
            return decide(call, context)
        return decide_rules_only(call, context)

    async def _judge(self, call: ToolCall, origin_cwd: str | None) -> AutoVerdict:
        """The model step for a call that the rules left open."""
        request = ClassifierRequest(
            tool=call.name,
            arguments=call.input,
            cwd=origin_cwd or str(self._harness.env.cwd),
            user_messages=tuple(self.user_messages.all()),
        )
        answer = await self.classifier.classify(request)
        return AutoVerdict(
            answer.outcome,
            answer.by,
            answer.reason,
            answer.rule_id,
            classifier="model",
            tokens_in=answer.tokens_in,
            tokens_out=answer.tokens_out,
            cost_usd=answer.cost_usd,
            model=answer.model,
            cancelled=answer.cancelled,
        )

    # ---- the model step: its input and its model

    @property
    def classifier_name(self) -> str:
        """The ``[auto] classifier`` setting now: ``"model"`` or ``"rules"``."""
        return self._get_config().auto.classifier

    def model_description(self) -> str:
        """Which model judges, as words for the user."""
        wanted = self._get_config().auto.model
        if wanted:
            return f"{wanted} (from [auto] model)"
        model = getattr(self._harness, "model", None)
        model_id = getattr(model, "model_id", "")
        return (
            f"the model of this session ({model_id})" if model_id else "the model of this session"
        )

    def note_user_message(self, text: str) -> None:
        """A prompt of the user came in: its text is kept for the model step. Only what the user
        typed is passed here, never text of the assistant or of a tool."""
        try:
            self.user_messages.add(text)
        except Exception:
            logger.warning("Could not keep a message of the user for Auto", exc_info=True)

    async def load_history(self, session: Any) -> None:
        """A session was opened again: take the user's messages from its file (entries of the role
        ``user``). Text that an extension put into the session as a user message is in there too;
        there is no way to tell it apart. No history is the safe side: the model then judges the
        call alone."""
        try:
            entries = await session.get_branch()
            texts = [text for entry in entries if (text := _user_text_of(entry)) is not None]
            self.user_messages.load(texts)
        except Exception:
            logger.warning("Could not read the messages of the user for Auto", exc_info=True)

    def cancel(self) -> None:
        """``session/cancel`` (or a closed session): end a decision that waits for the model."""
        self.classifier.cancel()

    def _model_target(self) -> ModelTarget | None:
        """The model that is asked: the one of the session now (``/model`` can change it), or the
        entry of ``[[models]]`` that ``[auto] model`` names. ``None``: that entry is not there."""
        config = self._get_config()
        harness = self._harness
        wanted = config.auto.model
        if not wanted:
            model = harness.model
            return ModelTarget(model, harness.stream_fn, harness.get_api_key, model.model_id)
        from pi_agent_cli.factory import api_key_for_choice, model_for_choice

        for choice in config.model_choices():
            if choice.id == wanted:
                return ModelTarget(
                    model_for_choice(choice, reasoning=False),
                    harness.stream_fn,
                    api_key_for_choice(choice),
                    choice.id,
                )
        return None

    def _tell(self, text: str) -> None:
        """A line for the user (the breaker opened)."""
        pi = self._pi
        if pi is None:
            logger.warning("%s", text)
            return
        pi.notify(text)
