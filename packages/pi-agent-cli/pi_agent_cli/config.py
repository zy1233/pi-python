"""`~/.pi-python/agent.toml` (Python ACP agent). Override with PI_HOME.

Do not put Python agent settings in ``config.toml`` — the Rust TUI reads that
file with its own typed schema and stops on keys like ``permission = \"ask\"``.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from pi_agent_core.home import pi_home
from pi_agent_core.types import ThinkingLevel

PermissionMode = Literal["ask", "auto", "always-approve"]
# What to do about a project nobody has vouched for: ask the user, leave it alone, or trust it.
DefaultProjectTrust = Literal["ask", "never", "always"]

_VALID_PERMISSION: set[str] = {"ask", "auto", "always-approve"}
_VALID_DEFAULT_TRUST: set[str] = {"ask", "never", "always"}
_VALID_THINKING: set[str] = {"off", "minimal", "low", "medium", "high", "xhigh"}

# ``pi_home`` (imported above and re-exported from here) is the one place that decides where
# pi-python keeps its files; the extension loader and the extensions use the same function.


def _parse_local_env_line(raw_line: str) -> tuple[str, str] | None:
    """``(name, value)`` of one ``local.env`` line, or ``None`` for a line that sets nothing."""
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[7:].strip()
    key, sep, value = line.partition("=")
    key = key.strip()
    if not sep or not key:
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return key, value


def local_env_line_key(raw_line: str) -> str | None:
    """The variable name that one line of ``local.env`` sets (``None``: not an assignment)."""
    parsed = _parse_local_env_line(raw_line)
    return parsed[0] if parsed else None


def read_local_env(home: Path | str | None = None) -> dict[str, str]:
    """The ``KEY=value`` pairs of ``~/.pi-python/local.env``; the first line of a key counts.

    Nothing is read when the file is not there. The environment is not touched.
    """
    path = pi_home(home) / "local.env"
    if not path.is_file():
        return {}
    pairs: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_local_env_line(raw_line)
        if parsed is not None:
            pairs.setdefault(*parsed)
    return pairs


def load_local_env(home: Path | str | None = None) -> None:
    """Load ``~/.pi-python/local.env`` (KEY=value) without overwriting existing env."""
    for key, value in read_local_env(home).items():
        if key not in os.environ:
            os.environ[key] = value


def agent_config_path(home: Path | str | None = None) -> Path:
    return pi_home(home) / "agent.toml"


def legacy_config_path(home: Path | str | None = None) -> Path:
    """Legacy path; breaks the Rust TUI if it contains Python-only keys."""
    return pi_home(home) / "config.toml"


# The provider of a configuration that sets no model (``CliConfig``'s default). It is the name
# of the scripted test LLM (``PI_USE_MOCK=1``), not of a real provider.
MOCK_PROVIDER = "mock"


NO_MODEL = "No model is set."

# The variable that the SDK of a provider reads when it is given no key (see ``api_key_getter``).
STANDARD_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


def no_model_message(home: Path | str | None = None) -> str:
    """What to tell a user whose configuration sets no model."""
    return (
        f"{NO_MODEL} Run the agent with --setup, "
        f"or add a [model] table to {agent_config_path(home)}."
    )


def expand_config_path(raw: str, *, cwd: str | Path) -> str:
    """Expand ~ and resolve relative skill/config paths against cwd."""
    expanded = os.path.expanduser(raw)
    path = Path(expanded)
    if not path.is_absolute():
        path = Path(cwd) / path
    return str(path.resolve())


def is_project_relative_path(raw: str) -> bool:
    """Does ``expand_config_path`` resolve *raw* against the project (cwd)?

    Such an entry points into whatever project is open, so what it finds is the project's
    own content, not the user's.
    """
    return not Path(os.path.expanduser(raw)).is_absolute()


@dataclass(frozen=True)
class ModelChoice:
    """One selectable model (the ``/model`` picker; ACP session config option ``model``).

    In ``[[models]]`` entries unset fields inherit from the default ``[model]`` table, but
    ``base_url`` / ``api_key_env`` / ``supports_images`` are only inherited when ``provider``
    is unset or equals the default provider (credentials never leak across providers).
    """

    id: str
    name: str | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    supports_images: bool | None = None


AutoClassifier = Literal["rules", "model"]

# What a ``bash`` call that Auto let through is held to (``[auto] bash_timeout_seconds`` and
# ``bash_max_output_mb``). Auto runs only commands that it can show to be harmless, and none of
# those should take minutes or print without end; the limits are what stops one that does.
DEFAULT_AUTO_BASH_TIMEOUT_SECONDS = 300.0
DEFAULT_AUTO_BASH_MAX_OUTPUT_MB = 64


@dataclass(frozen=True)
class AutoConfig:
    """The ``[auto]`` table of ``agent.toml``: settings of the Auto permission mode.

    It is read from the home file only, never from a file of the project: what a project can
    set, a project can loosen (invariant 9). ``classifier`` is ``"rules"`` or ``"model"``; only
    ``"rules"`` works in this version, and ``"model"`` is read and then decided as ``"rules"``
    (``notes`` says so). ``extra_roots`` are folders that count like the project folder;
    ``protected`` are globs that always ask, on top of the built-in list. A folder that is not
    absolute is kept as it is, so that Auto can see it is wrong and ask about every call.
    ``notes`` are values of ``agent.toml`` that were read and not used (also a ``permission``
    that is not one of the three modes), as sentences for the user. ``problems``
    are values that Auto cannot work with safely (an entry of ``protected`` that is not text,
    for example): while there is one, Auto asks about every call.
    """

    classifier: AutoClassifier = "rules"
    extra_roots: tuple[str, ...] = ()
    protected: tuple[str, ...] = ()
    bash_timeout_seconds: float = DEFAULT_AUTO_BASH_TIMEOUT_SECONDS
    bash_max_output_mb: int = DEFAULT_AUTO_BASH_MAX_OUTPUT_MB
    notes: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class CliConfig:
    permission: PermissionMode = "ask"
    provider: str = MOCK_PROVIDER
    model_id: str = "mock"
    base_url: str | None = None
    thinking_level: ThinkingLevel = "off"
    max_turns: int | None = None
    api_key_env: str | None = None
    supports_images: bool | None = None
    # Whether the model can reason (``Model.reasoning``). ``None``: not set, and then asking
    # for a thinking level is taken as saying so (``model_reasoning``).
    reasoning: bool | None = None
    skills_dirs: tuple[str, ...] = ()
    agent_command: str | None = None
    no_context_files: bool = False
    custom_system_prompt: str | None = None
    custom_system_prompt_file: str | None = None
    append_system_prompt: str | None = None
    append_system_prompt_file: str | None = None
    git_enabled: bool = True
    git_timeout_seconds: float = 2.0
    git_max_status_lines: int = 40
    # Project-local extensions (<project>/.pi-python/extensions) run arbitrary Python when
    # loaded, and the project's saved workflows, prompt files and skills decide what the model
    # runs or is told. All are skipped unless the project is trusted; see ``extension_trust``.
    trust_project_extensions: bool = False
    trusted_projects: tuple[str, ...] = ()
    # ``None``: not set. Then the older ``trust_project_extensions`` decides ("always" if it is
    # on), and otherwise the answer is "ask". See ``extension_trust.effective_default_trust``.
    default_project_trust: DefaultProjectTrust | None = None
    models: tuple[ModelChoice, ...] = ()
    auto: AutoConfig = field(default_factory=AutoConfig)

    @property
    def model_reasoning(self) -> bool:
        """``Model.reasoning`` for the configured model.

        The adapter asks a provider to think only when the model can (this) and the request
        asks for it (``thinking_level`` other than ``off``). The level comes from the same
        file, so a user who sets one has said the model can reason, unless ``reasoning``
        says otherwise.
        """
        if self.reasoning is not None:
            return self.reasoning
        return self.thinking_level != "off"

    def default_choice(self) -> ModelChoice:
        """The ``[model]`` table as a fully-resolved choice."""
        return ModelChoice(
            id=self.model_id,
            provider=self.provider,
            base_url=self.base_url,
            api_key_env=self.api_key_env,
            supports_images=self.supports_images,
        )

    def model_choices(self) -> tuple[ModelChoice, ...]:
        """Selectable models: the default first, then ``[[models]]`` (resolved, unique by id)."""
        out = [self.default_choice()]
        seen = {self.model_id}
        for item in self.models:
            if item.id in seen:
                continue
            seen.add(item.id)
            same_provider = item.provider is None or item.provider == self.provider
            out.append(
                ModelChoice(
                    id=item.id,
                    name=item.name,
                    provider=item.provider or self.provider,
                    base_url=item.base_url
                    if item.base_url is not None or not same_provider
                    else self.base_url,
                    api_key_env=item.api_key_env
                    if item.api_key_env is not None or not same_provider
                    else self.api_key_env,
                    supports_images=item.supports_images
                    if item.supports_images is not None or not same_provider
                    else self.supports_images,
                )
            )
        return tuple(out)


def load_config(home: Path | str | None = None) -> CliConfig:
    import tomllib

    agent_path = agent_config_path(home)
    if agent_path.is_file():
        data = tomllib.loads(agent_path.read_text(encoding="utf-8"))
        return _from_toml(data)
    legacy = legacy_config_path(home)
    if legacy.is_file():
        data = tomllib.loads(legacy.read_text(encoding="utf-8"))
        return _from_toml(data)
    return CliConfig()


def api_key_getter(env_name: str | None, provider: str) -> Callable[[str], str | None] | None:
    """Build the ``get_api_key`` callback for a model of ``provider``.

    ``None`` when no ``env_name`` is configured. ``env_name`` is the key of ``provider`` only.
    Any other provider (e.g. a sub-agent routed elsewhere) gets ``None`` so its SDK falls back
    to its own standard env var (``ANTHROPIC_API_KEY`` ...) instead of receiving a credential
    meant for a different vendor.
    """
    if not env_name:
        return None

    def get_api_key(requested: str) -> str | None:
        if requested.casefold() != provider.casefold():
            return None
        return os.environ.get(env_name) or None

    return get_api_key


def make_get_api_key(
    config: CliConfig,
) -> Callable[[str], str | None] | None:
    """Build the ``get_api_key`` callback for the ``[model]`` default.

    ``None`` when no ``api_key_env`` is configured. ``api_key_env`` is the key of
    ``config.provider`` only (see ``api_key_getter``).
    """
    return api_key_getter(config.api_key_env, config.provider)


def config_problem(config: CliConfig, environ: Mapping[str, str] | None = None) -> str | None:
    """Why ``config`` cannot start a turn, as a sentence (``None``: nothing is missing).

    This looks at what the files say and uses no network: is a model set, and has the variable
    that holds its key a value? A key that the provider turns down is not found here. The
    scripted test LLM (``PI_USE_MOCK=1``) has no problem. The variable that the provider's SDK
    reads when it gets no key counts as a key too (``STANDARD_KEY_ENV``).
    """
    env = os.environ if environ is None else environ
    if env.get("PI_USE_MOCK") == "1":
        return None
    provider = config.provider.casefold()
    if provider == MOCK_PROVIDER:
        return NO_MODEL
    name = config.api_key_env
    if not name or env.get(name):
        return None
    standard = STANDARD_KEY_ENV.get(provider)
    if standard and env.get(standard):
        return None
    return f"The variable {name} has no value."


def _from_toml(data: dict[str, Any]) -> CliConfig:
    model = data.get("model") if isinstance(data.get("model"), dict) else {}
    skills = data.get("skills") if isinstance(data.get("skills"), dict) else {}
    agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
    prompt = data.get("prompt") if isinstance(data.get("prompt"), dict) else {}
    git = data.get("git") if isinstance(data.get("git"), dict) else {}
    extensions = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}

    permission = data.get("permission", "ask")
    permission_note: str | None = None
    if not isinstance(permission, str) or permission not in _VALID_PERMISSION:
        permission_note = (
            'permission in agent.toml must be "ask", "auto" or "always-approve". '
            'The agent uses "ask".'
        )
        permission = "ask"
    thinking = data.get("thinking_level", "off")
    if thinking not in _VALID_THINKING:
        thinking = "off"
    max_turns = data.get("max_turns")
    if max_turns is not None:
        max_turns = int(max_turns)

    raw_paths = skills.get("paths") or skills.get("dirs") or []
    skills_dirs: tuple[str, ...] = ()
    if isinstance(raw_paths, list):
        skills_dirs = tuple(str(item) for item in raw_paths if str(item).strip())

    supports_images_raw = model.get("supports_images")
    if supports_images_raw is None:
        supports_images_raw = data.get("supports_images")
    supports_images = bool(supports_images_raw) if supports_images_raw is not None else None

    reasoning_raw = model.get("reasoning")
    if reasoning_raw is None:
        reasoning_raw = data.get("reasoning")
    reasoning = _as_bool(reasoning_raw, False) if reasoning_raw is not None else None

    agent_command = agent.get("command")
    if agent_command is not None:
        agent_command = str(agent_command).strip() or None

    def _optional_str(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    raw_trusted = extensions.get("trusted_projects")
    trusted_projects: tuple[str, ...] = ()
    if isinstance(raw_trusted, list):
        trusted_projects = tuple(str(item).strip() for item in raw_trusted if str(item).strip())

    models: list[ModelChoice] = []
    raw_models = data.get("models")
    if isinstance(raw_models, list):
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            model_id = _optional_str(item.get("id") or item.get("model_id"))
            if model_id is None:
                continue
            images = item.get("supports_images")
            models.append(
                ModelChoice(
                    id=model_id,
                    name=_optional_str(item.get("name")),
                    provider=_optional_str(item.get("provider")),
                    base_url=_optional_str(item.get("base_url")),
                    api_key_env=_optional_str(item.get("api_key_env")),
                    supports_images=bool(images) if images is not None else None,
                )
            )

    auto = _auto_config(data.get("auto"))
    if permission_note is not None:
        auto = replace(auto, notes=(permission_note, *auto.notes))

    return CliConfig(
        permission=permission,  # type: ignore[arg-type]
        provider=str(model.get("provider") or data.get("provider") or MOCK_PROVIDER),
        model_id=str(model.get("id") or model.get("model_id") or data.get("model_id") or "mock"),
        base_url=model.get("base_url") or data.get("base_url"),
        thinking_level=thinking,  # type: ignore[arg-type]
        max_turns=max_turns,
        api_key_env=model.get("api_key_env") or data.get("api_key_env"),
        supports_images=supports_images,
        reasoning=reasoning,
        skills_dirs=skills_dirs,
        agent_command=agent_command,
        no_context_files=bool(prompt.get("no_context_files", False)),
        custom_system_prompt=_optional_str(prompt.get("custom_system_prompt")),
        custom_system_prompt_file=_optional_str(prompt.get("custom_system_prompt_file")),
        append_system_prompt=_optional_str(prompt.get("append_system_prompt")),
        append_system_prompt_file=_optional_str(prompt.get("append_system_prompt_file")),
        git_enabled=_as_bool(git.get("enabled"), True),
        git_timeout_seconds=_as_float(git.get("timeout_seconds"), 2.0),
        git_max_status_lines=_as_int(git.get("max_status_lines"), 40),
        trust_project_extensions=_as_bool(extensions.get("trust_project_extensions"), False),
        trusted_projects=trusted_projects,
        default_project_trust=_default_project_trust(extensions.get("default_project_trust")),
        models=tuple(models),
        auto=auto,
    )


_AUTO_KEYS = frozenset(
    {"classifier", "extra_roots", "protected", "bash_timeout_seconds", "bash_max_output_mb"}
)


def _auto_config(value: object) -> AutoConfig:
    """The ``[auto]`` table. A value that cannot be used is dropped, and ``notes`` names it.

    Dropping is the safe side for what loosens Auto (a folder in ``extra_roots``, a limit). What
    tightens Auto (an entry of ``protected``) is not dropped silently: it goes to ``problems``,
    and Auto asks about every call until the file is right.
    """
    if value is None:
        return AutoConfig()
    if not isinstance(value, dict):
        return AutoConfig(notes=("auto in agent.toml is not a table, and it is ignored.",))
    notes: list[str] = []
    problems: list[str] = []
    notes.extend(
        f'The key "{key}" in [auto] is not known, and it is ignored.'
        for key in value
        if key not in _AUTO_KEYS
    )

    classifier: AutoClassifier = "rules"
    raw_classifier = value.get("classifier")
    if raw_classifier is not None:
        name = raw_classifier.strip().lower() if isinstance(raw_classifier, str) else ""
        if name == "model":
            classifier = "model"
            notes.append(
                'classifier = "model" does not work yet. Auto uses the rules only, and it asks '
                "you about a command that the rules cannot prove."
            )
        elif name != "rules":
            notes.append('classifier in [auto] must be "rules" or "model". Auto uses "rules".')

    extra_roots: list[str] = []
    raw_roots = value.get("extra_roots")
    if isinstance(raw_roots, list):
        for item in raw_roots:
            if isinstance(item, str) and item.strip():
                extra_roots.append(os.path.expanduser(item.strip()))
            else:
                notes.append(
                    "An entry of extra_roots in [auto] is not a folder, and it is ignored."
                )
    elif raw_roots is not None:
        notes.append("extra_roots in [auto] must be a list of folders, and it is ignored.")

    protected: list[str] = []
    raw_protected = value.get("protected")
    if isinstance(raw_protected, list):
        for item in raw_protected:
            if not isinstance(item, str):
                problems.append("An entry of protected in [auto] is not text.")
            elif item.strip():
                protected.append(os.path.expanduser(item.strip()))
            else:
                notes.append("An entry of protected in [auto] is empty, and it is ignored.")
    elif raw_protected is not None:
        problems.append("protected in [auto] must be a list of globs.")

    timeout = DEFAULT_AUTO_BASH_TIMEOUT_SECONDS
    raw_timeout = value.get("bash_timeout_seconds")
    if raw_timeout is not None:
        if (
            isinstance(raw_timeout, (int, float))
            and not isinstance(raw_timeout, bool)
            and math.isfinite(raw_timeout)
            and raw_timeout > 0
        ):
            timeout = float(raw_timeout)
        else:
            notes.append(
                "bash_timeout_seconds in [auto] must be a number above 0. "
                f"Auto uses {DEFAULT_AUTO_BASH_TIMEOUT_SECONDS:g} seconds."
            )

    max_output_mb = DEFAULT_AUTO_BASH_MAX_OUTPUT_MB
    raw_output = value.get("bash_max_output_mb")
    if raw_output is not None:
        if isinstance(raw_output, int) and not isinstance(raw_output, bool) and raw_output >= 1:
            max_output_mb = raw_output
        else:
            notes.append(
                "bash_max_output_mb in [auto] must be a whole number of 1 or more. "
                f"Auto uses {DEFAULT_AUTO_BASH_MAX_OUTPUT_MB} MB."
            )

    return AutoConfig(
        classifier=classifier,
        extra_roots=tuple(extra_roots),
        protected=tuple(protected),
        bash_timeout_seconds=timeout,
        bash_max_output_mb=max_output_mb,
        notes=tuple(notes),
        problems=tuple(problems),
    )


def _default_project_trust(value: object) -> DefaultProjectTrust | None:
    """``ask``, ``never`` or ``always``; anything else is treated as if it were not set, so a
    typo never quietly picks a side."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text if text in _VALID_DEFAULT_TRUST else None  # type: ignore[return-value]


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_float(value: object, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
