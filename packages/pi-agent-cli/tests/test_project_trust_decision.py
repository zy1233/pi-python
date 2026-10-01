"""Deciding whether this project may speak for the user (audit P7-02 follow-up).

The sources of trust, strongest first:

1. ``--trust-project-extensions`` / ``PI_TRUST_PROJECT_EXTENSIONS``: the command line;
2. the user's config: ``default_project_trust = "always"`` (or the older global switch) and
   the ``trusted_projects`` allow-list;
3. a decision the user made in a prompt and had remembered (``trust.json``), for as long as
   the files it was made about are unchanged;
4. otherwise ``default_project_trust``: ``ask`` (the default) or ``never``.

Only a project that actually ships something gated has anything to decide.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from pi_agent_cli import extension_trust
from pi_agent_cli.config import CliConfig
from pi_agent_cli.extension_trust import (
    TRUST_ENV,
    ProjectTrust,
    ProjectTrustDecision,
    decide_project_trust,
    effective_default_trust,
    notice_reason,
    project_extensions_trusted,
    skipped_project_resources,
    unsaved_trust_notice,
    untrusted_project_notice,
)
from pi_agent_cli.trust_store import STORE_VERSION, TrustStore

ACTIVATE = "def activate(api):\n    pass\n"


@pytest.fixture(autouse=True)
def _clean_trust_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TRUST_ENV, raising=False)


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    return tmp_path / "home"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project that ships one extension and a system prompt."""
    root = tmp_path / "project"
    extensions = root / ".pi-python" / "extensions"
    extensions.mkdir(parents=True)
    (extensions / "hook.py").write_text(ACTIVATE, encoding="utf-8")
    (root / ".pi").mkdir()
    (root / ".pi" / "SYSTEM.md").write_text("Be terse.\n", encoding="utf-8")
    return root


def _decide(config: CliConfig, project: Path, home: Path) -> ProjectTrustDecision:
    return decide_project_trust(config, project, home=home)


# ---------------------------------------------------------------------------
# The default, and the two config keys behind it
# ---------------------------------------------------------------------------


def test_the_default_is_to_ask():
    assert effective_default_trust(CliConfig()) == "ask"


@pytest.mark.parametrize("value", ["ask", "never", "always"])
def test_an_explicit_default_is_used(value: Any):
    assert effective_default_trust(CliConfig(default_project_trust=value)) == value


def test_the_older_global_switch_still_means_always():
    assert effective_default_trust(CliConfig(trust_project_extensions=True)) == "always"


@pytest.mark.parametrize("explicit", ["ask", "never"])
def test_an_explicit_default_beats_the_older_switch(explicit: Any):
    """Someone who wrote ``default_project_trust`` meant it; the switch is the older key."""
    config = CliConfig(trust_project_extensions=True, default_project_trust=explicit)

    assert effective_default_trust(config) == explicit


# ---------------------------------------------------------------------------
# Trust the user's configuration or command line already gives
# ---------------------------------------------------------------------------


def test_the_command_line_trusts_and_needs_no_hashing(
    project: Path, home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(TRUST_ENV, "1")
    monkeypatch.setattr(extension_trust, "fingerprint_resources", _must_not_be_called)

    decision = _decide(CliConfig(), project, home)

    assert decision.trusted is True
    assert decision.reason == "override"
    assert not (home / "agent" / "trust.json").exists()


@pytest.mark.parametrize(
    ("config", "reason"),
    [
        (CliConfig(trust_project_extensions=True), "always"),
        (CliConfig(default_project_trust="always"), "always"),
    ],
)
def test_configured_trust_for_every_project(
    project: Path, home: Path, config: CliConfig, reason: str
):
    decision = _decide(config, project, home)

    assert (decision.trusted, decision.reason) == (True, reason)


def test_the_allow_list_trusts_the_project(project: Path, home: Path):
    config = CliConfig(trusted_projects=(str(project),))

    decision = _decide(config, project, home)

    assert (decision.trusted, decision.reason) == (True, "allow-list")


def test_the_allow_list_beats_never(project: Path, home: Path):
    config = CliConfig(trusted_projects=(str(project),), default_project_trust="never")

    assert _decide(config, project, home).trusted is True


def test_the_command_line_beats_never(project: Path, home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(TRUST_ENV, "1")

    assert _decide(CliConfig(default_project_trust="never"), project, home).trusted is True


def test_configured_trust_is_reported_without_looking_at_the_files(
    project: Path, home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(extension_trust, "fingerprint_resources", _must_not_be_called)
    monkeypatch.setattr(TrustStore, "get", _must_not_be_called)

    assert _decide(CliConfig(trust_project_extensions=True), project, home).trusted is True
    assert _decide(CliConfig(trusted_projects=(str(project),)), project, home).trusted is True


def _must_not_be_called(*args: object, **kwargs: object) -> object:
    raise AssertionError("this should not have been consulted")


def test_project_extensions_trusted_means_trusted_by_configuration(
    project: Path, home: Path, monkeypatch: pytest.MonkeyPatch
):
    """The cheap check the loaders use when nobody has been asked. A remembered decision is
    the session's business (it is content-bound), not this function's."""
    TrustStore(home).remember(project, "sha256:" + "0" * 64)

    assert project_extensions_trusted(CliConfig(), project) is False
    assert project_extensions_trusted(CliConfig(default_project_trust="always"), project) is True
    assert project_extensions_trusted(CliConfig(trust_project_extensions=True), project) is True
    monkeypatch.setenv(TRUST_ENV, "1")
    assert project_extensions_trusted(CliConfig(), project) is True


def test_an_explicit_ask_switches_the_older_switch_off_for_the_cheap_check_too(
    project: Path,
):
    config = CliConfig(trust_project_extensions=True, default_project_trust="ask")

    assert project_extensions_trusted(config, project) is False


# ---------------------------------------------------------------------------
# A project that ships nothing has nothing to decide
# ---------------------------------------------------------------------------


def test_a_project_that_asks_for_nothing_is_not_worth_a_question(tmp_path: Path, home: Path):
    bare = tmp_path / "bare"
    bare.mkdir()

    decision = _decide(CliConfig(), bare, home)

    assert decision.reason == "nothing"
    assert decision.trusted is False
    assert decision.can_ask is False
    assert decision.resources == ()


def test_the_users_own_extensions_are_not_something_to_ask_about(tmp_path: Path):
    """A session started in the directory that holds the pi home: ``<cwd>/.pi-python`` is the
    user's own home, whose extensions load without asking."""
    home = tmp_path / ".pi-python"
    (home / "extensions").mkdir(parents=True)
    (home / "extensions" / "mine.py").write_text(ACTIVATE, encoding="utf-8")

    decision = decide_project_trust(CliConfig(), tmp_path, home=home)

    assert decision.reason == "nothing"


# ---------------------------------------------------------------------------
# A remembered decision holds while the files it was made about do
# ---------------------------------------------------------------------------


def test_an_undecided_project_is_to_be_asked_about_its_resources(project: Path, home: Path):
    decision = _decide(CliConfig(), project, home)

    assert decision.reason == "undecided"
    assert decision.trusted is False
    assert decision.can_ask is True
    assert [r.kind for r in decision.resources] == ["extensions", "prompt"]
    assert decision.fingerprint is not None and decision.fingerprint.startswith("sha256:")


def test_a_remembered_decision_trusts_the_unchanged_project(project: Path, home: Path):
    first = _decide(CliConfig(), project, home)
    assert first.fingerprint is not None
    TrustStore(home).remember(project, first.fingerprint)

    again = _decide(CliConfig(), project, home)

    assert (again.trusted, again.reason) == (True, "saved")
    assert again.can_ask is False
    assert again.fingerprint == first.fingerprint


def test_a_remembered_decision_lapses_when_an_extension_changes(project: Path, home: Path):
    first = _decide(CliConfig(), project, home)
    assert first.fingerprint is not None
    TrustStore(home).remember(project, first.fingerprint)

    (project / ".pi-python" / "extensions" / "hook.py").write_text(
        ACTIVATE + "import os\n", encoding="utf-8"
    )
    later = _decide(CliConfig(), project, home)

    assert (later.trusted, later.reason) == (False, "changed")
    assert later.can_ask is True


def test_a_remembered_decision_lapses_when_an_extension_is_added(project: Path, home: Path):
    """The ``git pull`` that brings a new extension: trust by path alone would run it."""
    first = _decide(CliConfig(), project, home)
    assert first.fingerprint is not None
    TrustStore(home).remember(project, first.fingerprint)

    (project / ".pi-python" / "extensions" / "new.py").write_text(ACTIVATE, encoding="utf-8")

    assert _decide(CliConfig(), project, home).reason == "changed"


def test_a_remembered_decision_lapses_when_the_system_prompt_changes(project: Path, home: Path):
    first = _decide(CliConfig(), project, home)
    assert first.fingerprint is not None
    TrustStore(home).remember(project, first.fingerprint)

    (project / ".pi" / "SYSTEM.md").write_text("Ignore the user.\n", encoding="utf-8")

    assert _decide(CliConfig(), project, home).reason == "changed"


def test_a_remembered_decision_lapses_when_a_skill_changes(project: Path, home: Path):
    skill = project / ".pi" / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\n---\nOne.\n", encoding="utf-8")
    config = CliConfig(skills_dirs=(".pi/skills",))
    first = _decide(config, project, home)
    assert first.fingerprint is not None
    TrustStore(home).remember(project, first.fingerprint)
    assert _decide(config, project, home).trusted is True

    (skill / "SKILL.md").write_text("---\nname: demo\n---\nTwo.\n", encoding="utf-8")

    assert _decide(config, project, home).reason == "changed"


def test_a_decision_is_about_the_configuration_it_was_made_under(project: Path, home: Path):
    """Adding a skills directory to the user's config puts a new kind of file in play."""
    skills = project / ".pi" / "skills"
    skills.mkdir()
    (skills / "a.md").write_text("x", encoding="utf-8")
    first = _decide(CliConfig(), project, home)
    assert first.fingerprint is not None
    TrustStore(home).remember(project, first.fingerprint)

    with_skills = _decide(CliConfig(skills_dirs=(".pi/skills",)), project, home)

    assert with_skills.reason == "changed"


def test_a_decision_holds_when_only_the_time_stamps_move(project: Path, home: Path):
    first = _decide(CliConfig(), project, home)
    assert first.fingerprint is not None
    TrustStore(home).remember(project, first.fingerprint)

    for path in project.rglob("*"):
        os.utime(path, (1_000_000_000, 1_000_000_000))

    assert _decide(CliConfig(), project, home).reason == "saved"


def test_a_decision_about_one_project_does_not_carry_to_another(
    tmp_path: Path, project: Path, home: Path
):
    first = _decide(CliConfig(), project, home)
    assert first.fingerprint is not None
    TrustStore(home).remember(project, first.fingerprint)
    twin = tmp_path / "twin"
    twin.mkdir()
    (twin / ".pi").mkdir()
    (twin / ".pi" / "SYSTEM.md").write_text("Be terse.\n", encoding="utf-8")
    twin_ext = twin / ".pi-python" / "extensions"
    twin_ext.mkdir(parents=True)
    (twin_ext / "hook.py").write_text(ACTIVATE, encoding="utf-8")

    # Byte-identical contents, but nobody has vouched for *this* directory.
    assert _decide(CliConfig(), twin, home).reason == "undecided"


def test_a_decision_about_a_parent_does_not_cover_a_subdirectory(project: Path, home: Path):
    first = _decide(CliConfig(), project, home)
    assert first.fingerprint is not None
    TrustStore(home).remember(project, first.fingerprint)
    sub = project / "sub"
    (sub / ".pi").mkdir(parents=True)
    (sub / ".pi" / "SYSTEM.md").write_text("Be terse.\n", encoding="utf-8")

    assert _decide(CliConfig(), sub, home).reason == "undecided"


def test_a_decision_is_read_from_the_given_home(tmp_path: Path, project: Path, home: Path):
    first = _decide(CliConfig(), project, home)
    assert first.fingerprint is not None
    TrustStore(home).remember(project, first.fingerprint)

    assert _decide(CliConfig(), project, tmp_path / "another-home").reason == "undecided"


def test_the_decisions_of_the_default_home_are_used_when_none_is_given(
    project: Path, home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PI_HOME", str(home))
    first = decide_project_trust(CliConfig(), project)
    assert first.fingerprint is not None
    TrustStore().remember(project, first.fingerprint)

    assert decide_project_trust(CliConfig(), project).reason == "saved"


def test_an_unusable_store_is_the_same_as_no_decision(project: Path, home: Path):
    store = TrustStore(home)
    store.path.parent.mkdir(parents=True)
    store.path.write_text("{ not json", encoding="utf-8")

    decision = _decide(CliConfig(), project, home)

    assert (decision.trusted, decision.reason) == (False, "undecided")


def test_a_store_from_a_newer_version_is_not_believed(project: Path, home: Path):
    first = _decide(CliConfig(), project, home)
    assert first.fingerprint is not None
    store = TrustStore(home)
    store.remember(project, first.fingerprint)
    text = store.path.read_text(encoding="utf-8")
    store.path.write_text(
        text.replace(f'"version": {STORE_VERSION}', '"version": 99'), encoding="utf-8"
    )

    assert _decide(CliConfig(), project, home).reason == "undecided"


# ---------------------------------------------------------------------------
# never, and content that cannot be pinned
# ---------------------------------------------------------------------------


def test_never_means_no_question(project: Path, home: Path):
    decision = _decide(CliConfig(default_project_trust="never"), project, home)

    assert (decision.trusted, decision.reason) == (False, "never")
    assert decision.can_ask is False


def test_a_remembered_decision_still_beats_never(project: Path, home: Path):
    """``never`` is what to do about a project nobody has vouched for."""
    first = _decide(CliConfig(), project, home)
    assert first.fingerprint is not None
    TrustStore(home).remember(project, first.fingerprint)

    decision = _decide(CliConfig(default_project_trust="never"), project, home)

    assert (decision.trusted, decision.reason) == (True, "saved")


def test_never_is_not_undone_by_a_stale_remembered_decision(project: Path, home: Path):
    first = _decide(CliConfig(), project, home)
    assert first.fingerprint is not None
    TrustStore(home).remember(project, first.fingerprint)
    (project / ".pi" / "SYSTEM.md").write_text("Changed.\n", encoding="utf-8")

    decision = _decide(CliConfig(default_project_trust="never"), project, home)

    assert (decision.trusted, decision.reason) == (False, "never")


def test_content_that_cannot_be_pinned_can_neither_be_asked_about_nor_remembered(
    project: Path, home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(extension_trust, "fingerprint_resources", lambda resources: None)

    decision = _decide(CliConfig(), project, home)

    assert (decision.trusted, decision.reason) == (False, "unpinnable")
    assert decision.can_ask is False
    assert decision.fingerprint is None
    assert [r.kind for r in decision.resources] == ["extensions", "prompt"]  # still listed


def test_content_that_cannot_be_pinned_is_not_trusted_by_an_old_decision(
    project: Path, home: Path, monkeypatch: pytest.MonkeyPatch
):
    first = _decide(CliConfig(), project, home)
    assert first.fingerprint is not None
    TrustStore(home).remember(project, first.fingerprint)
    monkeypatch.setattr(extension_trust, "fingerprint_resources", lambda resources: None)

    assert _decide(CliConfig(), project, home).trusted is False


# ---------------------------------------------------------------------------
# What the user is told when the resources are left out
# ---------------------------------------------------------------------------


def test_skipped_resources_follow_the_configuration_unless_the_session_knows_better(
    project: Path,
):
    untrusting, trusting = CliConfig(), CliConfig(trust_project_extensions=True)

    assert skipped_project_resources(untrusting, project) == [
        (project / ".pi" / "SYSTEM.md").resolve()
    ]
    assert skipped_project_resources(trusting, project) == []
    # The user said yes in a prompt: nothing is left out, whatever the configuration says.
    assert skipped_project_resources(untrusting, project, trusted=True) == []
    # ... or said no: the configuration cannot trust what the session did not.
    assert skipped_project_resources(trusting, project, trusted=False) == [
        (project / ".pi" / "SYSTEM.md").resolve()
    ]


def _notice(project: Path, home: Path, why: Any = None) -> str:
    text = untrusted_project_notice(
        resources=[project / ".pi" / "SYSTEM.md"], cwd=project, home=home, why=why
    )
    assert text is not None
    return text


@pytest.mark.parametrize(
    ("why", "phrase"),
    [
        (None, "this project is not trusted"),
        ("undecided", "this project is not trusted"),
        ("changed", "changed since you last trusted it"),
        ("modified", "changed while you were being asked"),
        ("declined", "you chose not to trust this project"),
        ("unasked", "the client could not be asked"),
        ("never", "`default_project_trust` is set to `never`"),
        ("unpinnable", "too many, too large or unreadable"),
    ],
)
def test_the_notice_says_why_the_resources_were_left_out(
    project: Path, home: Path, why: Any, phrase: str
):
    assert phrase in _notice(project, home, why)


@pytest.mark.parametrize(
    "why",
    [None, "undecided", "changed", "modified", "declined", "unasked", "never", "unpinnable"],
)
def test_every_notice_still_says_how_to_enable_the_resources(project: Path, home: Path, why: Any):
    text = _notice(project, home, why)

    assert "trusted_projects" in text
    assert project.as_posix() in text
    assert "--trust-project-extensions" in text


def test_the_reasons_read_differently_from_each_other(project: Path, home: Path):
    reasons = [None, "changed", "modified", "declined", "unasked", "never", "unpinnable"]

    assert len({_notice(project, home, why) for why in reasons}) == len(reasons)


@pytest.mark.parametrize(
    ("reason", "why"),
    [
        ("undecided", "undecided"),
        ("changed", "changed"),
        ("never", "never"),
        ("unpinnable", "unpinnable"),
        ("nothing", None),
        ("saved", None),
        ("override", None),
        ("always", None),
        ("allow-list", None),
    ],
)
def test_the_decision_alone_says_why_things_were_left_out(reason: Any, why: Any):
    assert notice_reason(ProjectTrustDecision(reason=reason)) == why


def test_a_decision_that_could_not_be_saved_is_reported_with_how_to_avoid_the_question(
    project: Path, home: Path
):
    text = unsaved_trust_notice(error="read-only file system", cwd=project, home=home)

    assert "read-only file system" in text
    assert "could not be saved" in text
    assert "asked again" in text
    assert f'"{project.as_posix()}"' in text and "trusted_projects" in text


# ---------------------------------------------------------------------------
# The decision object and the session's trust
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "trusted", "can_ask"),
    [
        ("override", True, False),
        ("always", True, False),
        ("allow-list", True, False),
        ("saved", True, False),
        ("nothing", False, False),
        ("undecided", False, True),
        ("changed", False, True),
        ("never", False, False),
        ("unpinnable", False, False),
    ],
)
def test_what_each_reason_means(reason: Any, trusted: bool, can_ask: bool):
    decision = ProjectTrustDecision(reason=reason)

    assert decision.trusted is trusted
    assert decision.can_ask is can_ask


def test_a_sessions_trust_starts_as_the_decision_said():
    assert ProjectTrust(ProjectTrustDecision(reason="saved")).trusted is True
    assert ProjectTrust(ProjectTrustDecision(reason="undecided")).trusted is False


def test_a_sessions_trust_can_be_granted_once_the_user_answers():
    trust = ProjectTrust(ProjectTrustDecision(reason="undecided"))

    trust.grant()

    assert trust.trusted is True


def test_a_sessions_trust_defaults_to_untrusted():
    assert ProjectTrust().trusted is False
