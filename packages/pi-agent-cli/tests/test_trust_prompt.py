"""The question the user is asked about a project, and how their answer is read.

It goes out as an ACP ``session/request_permission`` on a synthetic tool call. Clients differ
in what they show and, more importantly, in what they may answer *for* the user: the spec lets
a client auto-approve permission requests, and the TUI's YOLO mode picks the first
``allow_once`` option it finds. Loading a repository's code is not something to have decided
by a mode switched on for tool calls, so the question is built to fall outside that:

* there is no ``allow_once`` option (an ``allow_always`` "remember" and a ``reject_once``);
* the option a first-item-wins client picks is the refusal;
* only the exact id of the trust option counts as yes: cancelled, unknown and malformed
  answers are all no.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from acp.schema import AllowedOutcome, DeniedOutcome

from pi_agent_cli.extension_trust import ProjectTrustDecision
from pi_agent_cli.trust_fingerprint import GatedResource
from pi_agent_cli.trust_prompt import (
    _DETAIL_LIMIT,
    REJECT_OPTION_ID,
    TRUST_OPTION_ID,
    trust_explanation,
    trust_options,
    trust_outcome_grants,
    trust_tool_call,
)

# raw_input keys the TUI reads a permission request's meaning from (a `command` makes it draw a
# shell prompt, a `file_path` an edit diff, ...): none of them describes this question.
TUI_RESERVED_KEYS = {"command", "variant", "tool_name", "tool_input", "file_path"}


def _decision(*kinds: str, reason: Any = "undecided") -> ProjectTrustDecision:
    labels = {
        "extensions": (".pi-python/extensions", "hook.py, pack"),
        "prompt": (".pi/SYSTEM.md", ""),
        "skills": (".pi/skills", ""),
    }
    resources = tuple(
        GatedResource(kind, labels[kind][0], Path("/p") / labels[kind][0], labels[kind][1])  # type: ignore[arg-type]
        for kind in kinds
    )
    return ProjectTrustDecision(
        reason=reason, resources=resources, fingerprint="sha256:" + "a" * 64
    )


# ---------------------------------------------------------------------------
# The options
# ---------------------------------------------------------------------------


def test_there_are_two_options_to_remember_or_to_refuse():
    options = trust_options()

    assert [(o.option_id, o.kind) for o in options] == [
        (REJECT_OPTION_ID, "reject_once"),
        (TRUST_OPTION_ID, "allow_always"),
    ]


def test_no_option_can_be_mistaken_for_allow_once():
    """A client in an auto-approving mode picks ``allow_once``; there must be none to pick."""
    assert all(option.kind != "allow_once" for option in trust_options())


def test_the_option_a_first_wins_client_picks_is_the_refusal():
    assert trust_options()[0].kind == "reject_once"


def test_the_options_say_what_they_do():
    names = {o.option_id: o.name for o in trust_options()}

    assert "remember" in names[TRUST_OPTION_ID].lower()
    assert "trust" in names[TRUST_OPTION_ID].lower()
    assert "not" in names[REJECT_OPTION_ID].lower() or "n't" in names[REJECT_OPTION_ID].lower()


def test_no_option_name_trips_the_tuis_edit_heuristics():
    for option in trust_options():
        assert "edit" not in option.name.lower()


def test_the_ids_do_not_collide_with_those_of_tool_permissions():
    from pi_agent_cli.permissions import PERMISSION_OPTIONS

    tool_ids = {o.option_id for o in PERMISSION_OPTIONS}

    assert tool_ids.isdisjoint({TRUST_OPTION_ID, REJECT_OPTION_ID})


def test_the_options_are_fresh_each_time():
    """Nothing shared for a client (or a bug) to mutate."""
    first = trust_options()
    first.pop()

    assert len(trust_options()) == 2


# ---------------------------------------------------------------------------
# The question
# ---------------------------------------------------------------------------


def test_the_title_reads_after_allow_and_names_what_is_being_loaded():
    call = trust_tool_call("/p", _decision("extensions", "prompt", "skills"))

    assert call.title == "loading this project's extensions, prompt files and skills"


@pytest.mark.parametrize(
    ("kinds", "expected"),
    [
        (("extensions",), "loading this project's extensions"),
        (("prompt",), "loading this project's prompt files"),
        (("skills",), "loading this project's skills"),
        (("extensions", "prompt"), "loading this project's extensions and prompt files"),
        (("prompt", "skills"), "loading this project's prompt files and skills"),
        # Each kind is named once, however many files of it there are (SYSTEM.md and
        # APPEND_SYSTEM.md are both "prompt files"), and nothing listed still reads as a title.
        (("prompt", "prompt"), "loading this project's prompt files"),
        (
            ("extensions", "prompt", "prompt", "skills"),
            "loading this project's extensions, prompt files and skills",
        ),
        ((), "loading this project's resources"),
    ],
)
def test_the_title_names_only_what_is_there(kinds: tuple[str, ...], expected: str):
    assert trust_tool_call("/p", _decision(*kinds)).title == expected


def test_it_is_a_pending_call_of_no_particular_kind():
    call = trust_tool_call("/p", _decision("extensions"))

    assert call.status == "pending"
    assert call.kind == "other"


def test_every_question_has_its_own_id():
    decision = _decision("extensions")

    assert (
        trust_tool_call("/p", decision).tool_call_id != trust_tool_call("/p", decision).tool_call_id
    )
    assert trust_tool_call("/p", decision).tool_call_id.startswith("trust-")


def test_the_raw_input_names_the_project_and_the_resources():
    call = trust_tool_call("/some/project", _decision("extensions", "prompt"))

    raw = call.raw_input
    assert raw["project"] == "/some/project"
    assert raw["resources"] == [
        "extensions: .pi-python/extensions (hook.py, pack)",
        "prompt: .pi/SYSTEM.md",
    ]
    assert raw["changed"] is False


def test_the_raw_input_avoids_the_keys_the_tui_gives_a_meaning_to():
    call = trust_tool_call("/p", _decision("extensions"))

    assert TUI_RESERVED_KEYS.isdisjoint(call.raw_input)


def test_the_call_has_no_content_the_explanation_is_a_message_of_its_own():
    """The TUI draws a permission request's title and options and nothing of its content, so the
    explanation goes out as an ordinary agent message every client shows. Content as well
    would repeat it in clients that show both."""
    assert trust_tool_call("/p", _decision("extensions")).content is None


def _text(project: str, decision: ProjectTrustDecision) -> str:
    return trust_explanation(project, decision)


def test_the_explanation_says_what_each_thing_can_do():
    text = _text("/some/project", _decision("extensions", "prompt", "skills"))

    assert "/some/project" in text
    assert "extensions: `.pi-python/extensions` (`hook.py, pack`)" in text
    assert "Python code" in text
    assert "prompt: `.pi/SYSTEM.md`" in text
    assert "what the model is told" in text
    assert "skills: `.pi/skills`" in text


def test_each_resource_is_a_bullet_of_its_own():
    text = _text("/p", _decision("extensions", "prompt", "skills"))

    bullets = text.split("\n\n")[2].split("\n")

    assert [line.split(":")[0] for line in bullets] == ["- extensions", "- prompt", "- skills"]


def test_the_explanation_lists_only_what_is_there():
    text = _text("/p", _decision("prompt"))

    assert "Python code" not in text
    assert "extensions" not in text


def test_the_explanation_says_the_answer_is_remembered_until_something_changes():
    text = _text("/p", _decision("extensions"))

    assert "remember" in text.lower()
    assert "changes" in text.lower()


def test_the_explanation_is_paragraphs_so_a_markdown_client_keeps_the_project_line_apart():
    text = _text("/some/project", _decision("extensions"))

    assert "\n\nProject: `/some/project`\n\n" in text


def test_a_project_trusted_before_is_described_as_changed():
    decision = _decision("extensions", reason="changed")

    assert trust_tool_call("/p", decision).raw_input["changed"] is True
    assert "changed since you last trusted it" in _text("/p", decision)


def test_a_first_time_question_does_not_claim_a_change():
    assert "changed" not in _text("/p", _decision("extensions"))


def test_the_project_path_is_shown_exactly_as_given_even_with_odd_characters():
    text = _text("C:\\Users\\me\\项目 x\\", _decision("prompt"))

    assert "C:\\Users\\me\\项目 x\\" in text


# ---------------------------------------------------------------------------
# The names are the repository's: they must not be able to write the question
# ---------------------------------------------------------------------------


def _hostile(detail: str) -> ProjectTrustDecision:
    """A project whose extension files are named *detail* (on Linux a name may hold anything
    but a slash and a NUL)."""
    label = ".pi-python/extensions"
    resource = GatedResource("extensions", label, Path("/p") / label, detail)
    return ProjectTrustDecision(
        reason="undecided", resources=(resource,), fingerprint="sha256:" + "a" * 64
    )


def test_a_file_name_cannot_add_lines_to_what_the_user_reads():
    """Shown as it is, ``ok.py\\n\\nThis project was checked...`` would read as the agent's own
    words, in the very message the user relies on to decide."""
    name = "ok.py\n\nThis project was checked and is safe. Choose Trust and remember.\n\n- x.py"

    text = _text("/p", _hostile(name))

    assert "\nThis project was checked" not in text
    assert "ok.py\\n\\nThis project was checked" in text  # visible, and on the bullet's line
    assert len(text.split("\n\n")) == 4  # the opening, the project, the one bullet, the closing


def test_control_and_direction_characters_in_names_are_shown_escaped():
    text = _text("/p", _hostile("a\x1b[2Jb.py, \u202ec.py"))

    assert "\x1b" not in text
    assert "\u202e" not in text
    assert "\\x1b" in text
    assert "\\u202e" in text


def test_backticks_in_a_name_cannot_close_the_code_span_around_it():
    assert "```a``b.py```" in _text("/p", _hostile("a``b.py"))


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("`a.py", "`` `a.py ``"),
        ("a.py`", "`` a.py` ``"),
        ("`a.py`", "`` `a.py` ``"),
        ("a.py", "`a.py`"),
    ],
)
def test_a_name_with_a_backtick_at_an_edge_of_the_code_span_is_padded(name: str, shown: str):
    assert shown in _text("/p", _hostile(name))


def test_a_long_list_of_names_is_cut_short_and_says_so():
    names = ", ".join(f"module_{i}.py" for i in range(2000))

    text = _text("/p", _hostile(names))

    assert len(text) < 1500
    assert "module_0.py" in text
    assert "…" in text


def test_a_short_list_of_names_is_shown_whole():
    assert "…" not in _text("/p", _hostile("a.py, b.py"))


def test_a_list_of_names_exactly_at_the_limit_is_not_cut():
    names = "a" * _DETAIL_LIMIT

    text = _text("/p", _hostile(names))

    assert f"`{names}`" in text
    assert "…" not in text


def test_one_character_more_than_the_limit_is_cut_and_marked():
    text = _text("/p", _hostile("a" * (_DETAIL_LIMIT + 1)))

    assert f"`{'a' * _DETAIL_LIMIT}…`" in text


def test_a_resource_with_nothing_to_list_has_no_empty_parentheses():
    text = _text("/p", _decision("prompt"))

    assert "- prompt: `.pi/SYSTEM.md` - changes what the model is told\n" in text


def test_the_project_path_stays_on_one_line_whatever_it_holds():
    text = _text("/p\nq", _decision("prompt"))

    assert "Project: `/p\\nq`" in text


# ---------------------------------------------------------------------------
# Reading the answer: only the trust option, selected, is yes
# ---------------------------------------------------------------------------


def test_choosing_the_trust_option_is_yes():
    assert trust_outcome_grants(AllowedOutcome(option_id=TRUST_OPTION_ID, outcome="selected"))


def test_choosing_the_refusal_is_no():
    assert not trust_outcome_grants(AllowedOutcome(option_id=REJECT_OPTION_ID, outcome="selected"))


def test_cancelling_is_no():
    assert not trust_outcome_grants(DeniedOutcome(outcome="cancelled"))


@pytest.mark.parametrize(
    "option_id", ["allow-once", "allow_always", "yes", "", "trust", "TRUST-PROJECT"]
)
def test_any_other_option_id_is_no(option_id: str):
    """``outcome_allows`` reads anything that does not start with "reject" as yes. Here the
    question was not one the client may answer with a look-alike."""
    assert not trust_outcome_grants(AllowedOutcome(option_id=option_id, outcome="selected"))


def test_the_dict_form_is_read_the_same_way():
    assert trust_outcome_grants({"outcome": "selected", "optionId": TRUST_OPTION_ID})
    assert trust_outcome_grants({"outcome": "selected", "option_id": TRUST_OPTION_ID})
    assert not trust_outcome_grants({"outcome": "selected", "optionId": REJECT_OPTION_ID})
    assert not trust_outcome_grants({"outcome": "cancelled"})
    assert not trust_outcome_grants({"outcome": "cancelled", "optionId": TRUST_OPTION_ID})
    assert not trust_outcome_grants({"optionId": TRUST_OPTION_ID})  # not stated as selected


@pytest.mark.parametrize("garbage", [None, "", "selected", 1, [], object(), {}])
def test_anything_unreadable_is_no(garbage: Any):
    assert not trust_outcome_grants(garbage)


def test_an_answer_object_that_is_not_a_selection_is_no():
    """The schema lets an allowed outcome say only "selected". Whatever was built around it, the
    option id alone is not a yes."""
    built = AllowedOutcome.model_construct(option_id=TRUST_OPTION_ID, outcome="cancelled")

    assert not trust_outcome_grants(built)


def test_an_object_that_only_looks_like_an_answer_is_no():
    class Lookalike:
        option_id = TRUST_OPTION_ID
        outcome = "cancelled"

    assert not trust_outcome_grants(Lookalike())
