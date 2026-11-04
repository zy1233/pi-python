"""Reason codes of Auto and the fixed sentences that go into a question title."""

from __future__ import annotations

import pytest

from pi_agent_cli.auto_reasons import (
    ALLOW_REASONS,
    MODEL_CODES,
    SENTENCES,
    Reason,
    ask_title,
    from_model_code,
    sentence,
)


def test_every_reason_has_one_sentence() -> None:
    assert set(SENTENCES) == set(Reason)


@pytest.mark.parametrize("reason", list(Reason))
def test_sentences_are_short_plain_ascii_text(reason: Reason) -> None:
    text = sentence(reason)
    assert text.isascii()
    assert 0 < len(text) <= 60
    assert text == text.strip()
    assert not set(text) & set("{}()<>`$\n\t")


def test_sentences_are_all_different() -> None:
    assert len(set(SENTENCES.values())) == len(SENTENCES)


def test_ask_title_shape() -> None:
    assert ask_title("bash", Reason.OUTSIDE_PROJECT) == "bash (Auto: leaves the project folder)"
    assert ask_title("write", Reason.PROTECTED_PATH) == "write (Auto: touches a protected file)"


@pytest.mark.parametrize("reason", sorted(ALLOW_REASONS))
def test_an_allow_reason_has_no_question_title(reason: Reason) -> None:
    with pytest.raises(ValueError):
        ask_title("bash", reason)


def test_allow_reasons_are_exactly_the_four_allow_codes() -> None:
    assert {
        Reason.HARMLESS,
        Reason.IN_PROJECT_EDIT,
        Reason.READ_ONLY_COMMAND,
        Reason.MODEL_ALLOWED,
    } == ALLOW_REASONS


def test_the_model_code_list_is_closed() -> None:
    assert set(MODEL_CODES) == {
        "DESTRUCTIVE",
        "OUTSIDE_SCOPE",
        "NETWORK",
        "SECRETS",
        "EXEC_UNKNOWN",
        "PERSISTENCE",
        "UNCLEAR",
    }
    for code, reason in MODEL_CODES.items():
        assert from_model_code(code) is reason
        assert reason not in ALLOW_REASONS


@pytest.mark.parametrize(
    "text",
    ["ALLOW", "allow", "destructive", "UNAVAILABLE", "UNPARSEABLE", "INTERNAL", "", " NETWORK"],
)
def test_a_code_outside_the_list_is_not_a_reason(text: str) -> None:
    assert from_model_code(text) is None
