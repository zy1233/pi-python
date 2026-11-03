"""Tests for ~/.pi-python/agent.toml loading."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pi_agent_cli.config import (
    DEFAULT_AUTO_BASH_MAX_OUTPUT_MB,
    DEFAULT_AUTO_BASH_TIMEOUT_SECONDS,
    AutoConfig,
    CliConfig,
    expand_config_path,
    load_config,
    load_local_env,
    make_get_api_key,
    read_local_env,
)


def test_the_cli_and_the_extension_loader_share_one_pi_home():
    """The CLI honoured PI_HOME while the extension loader and the workflow extension went
    to ``Path.home()`` directly; one resolver keeps them from drifting apart again."""
    from pi_agent_cli import config as cli_config
    from pi_agent_core import home as core_home

    assert cli_config.pi_home is core_home.pi_home


def test_load_config_defaults_when_missing(tmp_path: Path):
    assert load_config(tmp_path) == CliConfig()


def test_load_config_prefers_agent_toml(tmp_path: Path):
    (tmp_path / "config.toml").write_text('permission = "auto"\n', encoding="utf-8")
    (tmp_path / "agent.toml").write_text(
        '[model]\nprovider = "deepseek"\nid = "from-agent"\n',
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    assert config.model_id == "from-agent"


def test_load_config_parses_model_skills_and_agent(tmp_path: Path):
    config_path = tmp_path / "agent.toml"
    config_path.write_text(
        """
permission = "auto"
thinking_level = "low"
max_turns = 12

[model]
provider = "deepseek"
id = "deepseek-chat"
base_url = "https://api.example/v1"
api_key_env = "MY_KEY"

[skills]
paths = ["~/.pi-python/skills", ".pi/skills"]

[agent]
command = "python -m pi_agent_cli"
""".strip(),
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    assert config == CliConfig(
        permission="auto",
        provider="deepseek",
        model_id="deepseek-chat",
        base_url="https://api.example/v1",
        thinking_level="low",
        max_turns=12,
        api_key_env="MY_KEY",
        skills_dirs=("~/.pi-python/skills", ".pi/skills"),
        agent_command="python -m pi_agent_cli",
    )


def test_make_get_api_key_reads_env(monkeypatch):
    monkeypatch.setenv("MY_KEY", "secret")
    getter = make_get_api_key(CliConfig(provider="deepseek", api_key_env="MY_KEY"))
    assert getter is not None
    assert getter("deepseek") == "secret"


def test_make_get_api_key_is_scoped_to_the_configured_provider(monkeypatch):
    """``api_key_env`` is the key of ``provider`` only.

    A sub-agent routed to another provider must not receive it; returning ``None`` lets
    that provider's SDK fall back to its own standard env var (e.g. ANTHROPIC_API_KEY).
    """
    monkeypatch.setenv("MY_KEY", "secret")
    getter = make_get_api_key(CliConfig(provider="deepseek", api_key_env="MY_KEY"))
    assert getter is not None
    assert getter("anthropic") is None
    assert getter("openai") is None
    assert getter("DeepSeek") == "secret"  # provider names compare case-insensitively


def test_make_get_api_key_returns_none_when_env_is_unset(monkeypatch):
    monkeypatch.delenv("MY_KEY", raising=False)
    getter = make_get_api_key(CliConfig(provider="deepseek", api_key_env="MY_KEY"))
    assert getter is not None
    assert getter("deepseek") is None


def test_make_get_api_key_is_none_without_api_key_env():
    assert make_get_api_key(CliConfig(provider="deepseek")) is None


def test_load_local_env_does_not_override_existing(tmp_path: Path, monkeypatch):
    env_path = tmp_path / "local.env"
    env_path.write_text("REAL_LLM_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("REAL_LLM_API_KEY", "from-env")
    load_local_env(tmp_path)
    assert os.environ["REAL_LLM_API_KEY"] == "from-env"


def test_load_local_env_sets_missing_keys(tmp_path: Path, monkeypatch):
    env_path = tmp_path / "local.env"
    env_path.write_text('export REAL_LLM_API_KEY="from-file"\n', encoding="utf-8")
    monkeypatch.delenv("REAL_LLM_API_KEY", raising=False)
    load_local_env(tmp_path)
    assert os.environ.get("REAL_LLM_API_KEY") == "from-file"


def test_read_local_env_returns_the_pairs_and_leaves_the_environment_alone(
    tmp_path: Path, monkeypatch
):
    (tmp_path / "local.env").write_text(
        '# comment\nA=1\nexport B="two"\nC=\nno equals sign\n=no name\nA=second\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("A", raising=False)
    monkeypatch.delenv("B", raising=False)
    assert read_local_env(tmp_path) == {"A": "1", "B": "two", "C": ""}  # the first line of A counts
    assert "A" not in os.environ
    assert "B" not in os.environ


def test_read_local_env_of_a_missing_file_is_empty(tmp_path: Path):
    assert read_local_env(tmp_path) == {}
    rel = tmp_path / "skills"
    rel.mkdir()
    got = expand_config_path(".pi/skills", cwd=tmp_path / "proj")
    assert got == str((tmp_path / "proj" / ".pi" / "skills").resolve())


def test_load_config_parses_selectable_models(tmp_path: Path):
    (tmp_path / "agent.toml").write_text(
        """
[model]
provider = "openai"
id = "base"
base_url = "https://one.example/v1"
api_key_env = "KEY_ONE"

[[models]]
id = "same-provider"
name = "Same Provider"

[[models]]
id = "other-provider"
provider = "deepseek"

[[models]]
id = "base"

[[models]]
name = "missing id is ignored"
""".strip(),
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    same, other = config.model_choices()[1:]
    assert [c.id for c in config.model_choices()] == ["base", "same-provider", "other-provider"]
    # unset fields inherit from [model] only for the same provider
    assert (same.provider, same.base_url, same.api_key_env, same.name) == (
        "openai",
        "https://one.example/v1",
        "KEY_ONE",
        "Same Provider",
    )
    assert (other.provider, other.base_url, other.api_key_env) == ("deepseek", None, None)


def test_load_config_without_models_has_single_choice():
    config = CliConfig(provider="mock", model_id="mock")
    assert [c.id for c in config.model_choices()] == ["mock"]


def test_load_config_parses_git_section(tmp_path: Path):
    (tmp_path / "agent.toml").write_text(
        """
[git]
enabled = false
timeout_seconds = 1.5
max_status_lines = 10
""".strip(),
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    assert config.git_enabled is False
    assert config.git_timeout_seconds == 1.5
    assert config.git_max_status_lines == 10


def test_load_config_parses_supports_images(tmp_path: Path):
    config_path = tmp_path / "agent.toml"
    config_path.write_text(
        """
[model]
provider = "deepseek"
id = "deepseek-flash"
supports_images = true
""".strip(),
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    assert config.supports_images is True

    # Test false
    config_path.write_text(
        """
[model]
supports_images = false
""".strip(),
        encoding="utf-8",
    )
    config2 = load_config(tmp_path)
    assert config2.supports_images is False


def test_reasoning_is_not_set_unless_the_file_says_so(tmp_path: Path):
    assert load_config(tmp_path).reasoning is None
    (tmp_path / "agent.toml").write_text('[model]\nid = "m"\n', encoding="utf-8")

    assert load_config(tmp_path).reasoning is None


def test_load_config_parses_reasoning(tmp_path: Path):
    config_path = tmp_path / "agent.toml"

    for text, expected in [
        ("[model]\nreasoning = true\n", True),
        ("[model]\nreasoning = false\n", False),
        ('[model]\nreasoning = "false"\n', False),
        ('[model]\nreasoning = "yes"\n', True),
        ("reasoning = true\n", True),
        ("reasoning = true\n[model]\nreasoning = false\n", False),
    ]:
        config_path.write_text(text, encoding="utf-8")

        assert load_config(tmp_path).reasoning is expected, text


def test_model_reasoning_follows_the_thinking_level_unless_set():
    """Asking for a thinking level is how a user says the model can reason; an explicit
    ``reasoning`` setting overrides that either way."""
    assert CliConfig().model_reasoning is False
    assert CliConfig(thinking_level="off").model_reasoning is False
    for level in ("minimal", "low", "medium", "high", "xhigh"):
        assert CliConfig(thinking_level=level).model_reasoning is True  # type: ignore[arg-type]
    assert CliConfig(thinking_level="high", reasoning=False).model_reasoning is False
    assert CliConfig(thinking_level="off", reasoning=True).model_reasoning is True


# ---------------------------------------------------------------------------------------------
# The ``[auto]`` table and ``permission`` (the Auto permission mode)
# ---------------------------------------------------------------------------------------------


def _load(tmp_path: Path, toml: str) -> CliConfig:
    (tmp_path / "agent.toml").write_text(toml, encoding="utf-8")
    return load_config(tmp_path)


def test_without_an_auto_table_the_defaults_are_used(tmp_path: Path):
    config = _load(tmp_path, 'permission = "auto"\n')

    assert config.auto == AutoConfig()
    assert config.auto.classifier == "rules"
    assert config.auto.bash_timeout_seconds == DEFAULT_AUTO_BASH_TIMEOUT_SECONDS == 300.0
    assert config.auto.bash_max_output_mb == DEFAULT_AUTO_BASH_MAX_OUTPUT_MB == 64
    assert config.auto.notes == ()
    assert config.auto.problems == ()


def test_the_auto_table_is_read(tmp_path: Path):
    config = _load(
        tmp_path,
        """
[auto]
classifier = "rules"
extra_roots = ["/srv/shared", "/data/work"]
protected = ["secrets/*", "*.pem"]
bash_timeout_seconds = 90
bash_max_output_mb = 8
""",
    )

    assert config.auto == AutoConfig(
        classifier="rules",
        extra_roots=("/srv/shared", "/data/work"),
        protected=("secrets/*", "*.pem"),
        bash_timeout_seconds=90.0,
        bash_max_output_mb=8,
    )


def test_a_home_folder_in_extra_roots_is_expanded(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    config = _load(tmp_path, '[auto]\nextra_roots = ["~/shared"]\n')

    assert config.auto.extra_roots == (os.path.expanduser("~/shared"),)


def test_a_folder_that_is_not_absolute_is_kept_so_that_auto_can_see_it_is_wrong(tmp_path: Path):
    config = _load(tmp_path, '[auto]\nextra_roots = ["relative/dir"]\n')

    assert config.auto.extra_roots == ("relative/dir",)


def test_the_model_classifier_is_read_and_says_it_does_not_work_yet(tmp_path: Path):
    config = _load(tmp_path, '[auto]\nclassifier = "model"\n')

    assert config.auto.classifier == "model"
    (note,) = config.auto.notes
    assert "does not work yet" in note
    assert config.auto.problems == ()


@pytest.mark.parametrize("value", ['"bogus"', "3", "true", '""'])
def test_a_classifier_that_is_not_known_falls_back_to_the_rules_with_a_note(
    tmp_path: Path, value: str
):
    config = _load(tmp_path, f"[auto]\nclassifier = {value}\n")

    assert config.auto.classifier == "rules"
    (note,) = config.auto.notes
    assert "classifier" in note


def test_a_key_that_is_not_known_is_named_in_a_note(tmp_path: Path):
    config = _load(tmp_path, '[auto]\ncolour = "red"\n')

    (note,) = config.auto.notes
    assert "colour" in note
    assert "not known" in note


@pytest.mark.parametrize("key", ["model", "timeout_seconds"])
def test_a_key_of_the_model_step_is_read_and_says_that_nothing_uses_it_yet(
    tmp_path: Path, key: str
):
    """The design lists these keys. A file that follows it must not be told that they are not
    known, only that nothing uses them yet."""
    config = _load(tmp_path, f'[auto]\n{key} = "x"\n')

    (note,) = config.auto.notes
    assert key in note
    assert "model step" in note
    assert "not known" not in note
    assert config.auto.problems == ()


def test_a_value_that_loosens_auto_is_dropped_with_a_note(tmp_path: Path):
    config = _load(tmp_path, '[auto]\nextra_roots = ["/ok", 3, "", "  "]\n')

    assert config.auto.extra_roots == ("/ok",)
    assert len(config.auto.notes) == 3
    assert config.auto.problems == ()


def test_extra_roots_that_is_not_a_list_is_dropped_with_a_note(tmp_path: Path):
    config = _load(tmp_path, '[auto]\nextra_roots = "/srv"\n')

    assert config.auto.extra_roots == ()
    assert any("extra_roots" in note for note in config.auto.notes)


def test_a_protected_entry_that_is_not_text_is_a_problem_not_a_silent_drop(tmp_path: Path):
    """What tightens Auto is not dropped without a word: Auto asks about every call instead."""
    config = _load(tmp_path, '[auto]\nprotected = ["secrets/*", 3]\n')

    assert config.auto.protected == ("secrets/*",)
    (problem,) = config.auto.problems
    assert "protected" in problem


def test_protected_that_is_not_a_list_is_a_problem(tmp_path: Path):
    config = _load(tmp_path, '[auto]\nprotected = "secrets/*"\n')

    assert config.auto.protected == ()
    (problem,) = config.auto.problems
    assert "protected" in problem


def test_an_empty_protected_entry_is_ignored_with_a_note(tmp_path: Path):
    config = _load(tmp_path, '[auto]\nprotected = ["secrets/*", " "]\n')

    assert config.auto.protected == ("secrets/*",)
    assert config.auto.problems == ()
    assert len(config.auto.notes) == 1


@pytest.mark.parametrize("value", ["0", "-5", "true", '"fast"', "nan", "inf", "-inf"])
def test_a_time_limit_that_is_not_a_positive_number_falls_back_with_a_note(
    tmp_path: Path, value: str
):
    config = _load(tmp_path, f"[auto]\nbash_timeout_seconds = {value}\n")

    assert config.auto.bash_timeout_seconds == DEFAULT_AUTO_BASH_TIMEOUT_SECONDS
    (note,) = config.auto.notes
    assert "bash_timeout_seconds" in note


@pytest.mark.parametrize(
    "value", ["1e300", "3000000", "2147483.648", "9" * 400, "99999999999999999999"]
)
def test_a_time_limit_that_the_bash_tool_would_refuse_falls_back_with_a_note(
    tmp_path: Path, value: str
):
    """The Bash tool refuses a limit above its own maximum with an error, so every call that
    Auto let through would fail. A whole number too big for a float used to stop the loading."""
    config = _load(tmp_path, f"[auto]\nbash_timeout_seconds = {value}\n")

    assert config.auto.bash_timeout_seconds == DEFAULT_AUTO_BASH_TIMEOUT_SECONDS
    (note,) = config.auto.notes
    assert "bash_timeout_seconds" in note


def test_the_largest_time_limit_that_the_bash_tool_takes_is_kept(tmp_path: Path):
    config = _load(tmp_path, "[auto]\nbash_timeout_seconds = 2147483\n")

    assert config.auto.bash_timeout_seconds == 2147483.0
    assert config.auto.notes == ()


def test_a_time_limit_may_be_a_fraction_of_a_second(tmp_path: Path):
    assert _load(tmp_path, "[auto]\nbash_timeout_seconds = 0.5\n").auto.bash_timeout_seconds == 0.5


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "true", '"big"'])
def test_an_output_limit_that_is_not_a_whole_number_of_one_or_more_falls_back_with_a_note(
    tmp_path: Path, value: str
):
    config = _load(tmp_path, f"[auto]\nbash_max_output_mb = {value}\n")

    assert config.auto.bash_max_output_mb == DEFAULT_AUTO_BASH_MAX_OUTPUT_MB
    (note,) = config.auto.notes
    assert "bash_max_output_mb" in note


def test_an_auto_that_is_not_a_table_is_ignored_with_a_note(tmp_path: Path):
    config = _load(tmp_path, "auto = 3\n")

    assert config.auto == AutoConfig(notes=config.auto.notes)
    (note,) = config.auto.notes
    assert "not a table" in note


@pytest.mark.parametrize("mode", ["ask", "auto", "always-approve"])
def test_each_of_the_three_permission_modes_is_read_without_a_note(tmp_path: Path, mode: str):
    config = _load(tmp_path, f'permission = "{mode}"\n')

    assert config.permission == mode
    assert config.auto.notes == ()


@pytest.mark.parametrize("value", ['"yolo"', '"Auto"', "true", '["auto"]', "3", '""'])
def test_a_permission_that_is_not_one_of_the_three_falls_back_to_ask_and_says_so(
    tmp_path: Path, value: str
):
    config = _load(tmp_path, f"permission = {value}\n")

    assert config.permission == "ask"
    (note,) = config.auto.notes
    assert "permission" in note
    assert '"ask"' in note


def test_a_file_of_the_project_cannot_set_the_auto_table(tmp_path: Path):
    """What a project can set, a project can loosen: ``[auto]`` is read from the home folder
    only (invariant 9). There is no project-level ``agent.toml``, and this keeps it so."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".pi-python").mkdir(parents=True)
    home.mkdir()
    for folder in (project, project / ".pi-python"):
        (folder / "agent.toml").write_text(
            'permission = "always-approve"\n[auto]\nextra_roots = ["/"]\nprotected = []\n',
            encoding="utf-8",
        )

    config = load_config(home)

    assert config.permission == "ask"
    assert config.auto == AutoConfig()
