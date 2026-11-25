"""The VS Code extension folds the Auto notices of the agent into one short row.

``vscode/src/core/status-row.ts`` (``autoModeNoticeTitle``) finds a notice by its words. The
words are the words of ``auto_mode.py``. If the agent changes a text and the match does not
change with it, the chat shows the notice as a plain answer. No TypeScript test fails then,
because the TypeScript tests cannot read the agent.

``vscode/test/fixtures/auto-notices.json`` holds the real texts. This test checks that the agent
still writes them, and ``vscode/test/status-row.test.ts`` checks that the match finds them. After
a change of a text, run this test with ``UPDATE_VSCODE_FIXTURES=1`` to write the file again, then
run ``npm test`` in ``vscode``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pi_agent_cli.auto_mode import (
    config_problems_message,
    entry_message,
    problems_message,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = REPO_ROOT / "vscode" / "test" / "fixtures" / "auto-notices.json"

SESSION_PROBLEMS = ["The project folder does not exist."]
CONFIG_PROBLEMS = ["protected in [auto] must be a list of globs."]


def current_notices() -> dict[str, str]:
    """The notices as the agent writes them now, under the names that the TypeScript test uses.

    ``agent.py`` sends the entry message and the problems of the session as one message
    (``"\\n\\n".join(parts)``), so ``entryWithProblems`` is built the same way.
    """
    return {
        "entry": entry_message(),
        "entryRules": entry_message(classifier="rules"),
        "entryFromConfig": entry_message(from_config=True),
        "entryUnknownModel": entry_message(unknown_model="no-such-model"),
        "entryWithProblems": "\n\n".join([entry_message(), problems_message(SESSION_PROBLEMS)]),
        "problems": problems_message(SESSION_PROBLEMS),
        "configProblems": config_problems_message(CONFIG_PROBLEMS),
    }


def test_the_agent_still_writes_the_notices_that_the_extension_folds() -> None:
    if not (REPO_ROOT / "vscode").is_dir():
        pytest.skip("this copy of the tests has no vscode/ folder next to it")
    notices = current_notices()
    if os.environ.get("UPDATE_VSCODE_FIXTURES") == "1":
        FIXTURE.write_text(
            json.dumps(notices, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
    assert FIXTURE.is_file(), f"{FIXTURE} is missing: run this test with UPDATE_VSCODE_FIXTURES=1"
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert stored == notices, (
        "A text in auto_mode.py changed. The extension finds these texts by their words "
        "(autoModeNoticeTitle in vscode/src/core/status-row.ts). Run this test with "
        "UPDATE_VSCODE_FIXTURES=1, then run `npm test` in vscode: if status-row.test.ts fails, "
        "change the match."
    )
