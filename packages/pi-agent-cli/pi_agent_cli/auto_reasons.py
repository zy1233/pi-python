"""Reason codes of the Auto permission mode, and the fixed sentence for each.

Every Auto decision carries one ``Reason``. When Auto asks the user, the question title shows
the sentence of that reason, for example ``bash (Auto: leaves the project folder)``. The
sentences are fixed text: no model output, no command text and no file name ever goes into
them. This keeps the title safe to show in any client, and it keeps a hostile command from
writing its own explanation.

Model answers (``ASK <CODE>``) map to the same codes through ``from_model_code``; a code that
is not in the closed list gives ``None``. A model ``ALLOW`` has one reason of its own,
``MODEL_ALLOWED``.
"""

from __future__ import annotations

from enum import StrEnum


class Reason(StrEnum):
    HARMLESS = "harmless"
    IN_PROJECT_EDIT = "in_project_edit"
    READ_ONLY_COMMAND = "read_only_command"
    MODEL_ALLOWED = "model_allowed"

    OUTSIDE_PROJECT = "outside_project"
    PROTECTED_PATH = "protected_path"
    DESTRUCTIVE = "destructive"
    NETWORK = "network"
    SECRETS = "secrets"
    HIDDEN_COMMAND = "hidden_command"
    UNTRUSTED_PROJECT_CODE = "untrusted_project_code"
    UNDECLARED_TOOL = "undeclared_tool"
    WORKFLOW = "workflow"
    UNCLEAR = "unclear"
    COMMENT = "comment"
    TOO_LARGE = "too_large"
    OVERWRITE_NO_GIT = "overwrite_no_git"
    PRIVILEGE = "privilege"
    PERSISTENCE = "persistence"
    UNKNOWN_CODE = "unknown_code"
    CHECKER_UNAVAILABLE = "checker_unavailable"
    CHECKER_UNREADABLE = "checker_unreadable"
    INTERNAL_ERROR = "internal_error"


ALLOW_REASONS = frozenset(
    {Reason.HARMLESS, Reason.IN_PROJECT_EDIT, Reason.READ_ONLY_COMMAND, Reason.MODEL_ALLOWED}
)

SENTENCES: dict[Reason, str] = {
    Reason.HARMLESS: "tool declares itself harmless",
    Reason.IN_PROJECT_EDIT: "edit inside the project folder",
    Reason.READ_ONLY_COMMAND: "read-only command",
    Reason.MODEL_ALLOWED: "the checker found it safe",
    Reason.OUTSIDE_PROJECT: "leaves the project folder",
    Reason.PROTECTED_PATH: "touches a protected file",
    Reason.DESTRUCTIVE: "deletes or overwrites data",
    Reason.NETWORK: "uses the network or installs software",
    Reason.SECRETS: "touches secrets or credentials",
    Reason.HIDDEN_COMMAND: "command hides what it does",
    Reason.UNTRUSTED_PROJECT_CODE: "runs code of an untrusted project",
    Reason.UNDECLARED_TOOL: "tool does not declare itself harmless",
    Reason.WORKFLOW: "starts a workflow",
    Reason.UNCLEAR: "cannot tell if it is safe",
    Reason.COMMENT: "has a shell comment and no rule proves it safe",
    Reason.TOO_LARGE: "command is too long to check",
    Reason.OVERWRITE_NO_GIT: "overwrites a file that git cannot restore",
    Reason.PRIVILEGE: "changes the system or needs more rights",
    Reason.PERSISTENCE: "sets up something that runs later",
    Reason.UNKNOWN_CODE: "runs code that cannot be checked",
    Reason.CHECKER_UNAVAILABLE: "the checker did not answer",
    Reason.CHECKER_UNREADABLE: "the checker answer was not valid",
    Reason.INTERNAL_ERROR: "internal error in the checker",
}

# The closed list of codes a model may answer with (``ASK <CODE>``). ``UNAVAILABLE``,
# ``UNPARSEABLE`` and ``INTERNAL`` are never answered by a model: Auto sets them itself.
MODEL_CODES: dict[str, Reason] = {
    "DESTRUCTIVE": Reason.DESTRUCTIVE,
    "OUTSIDE_SCOPE": Reason.OUTSIDE_PROJECT,
    "NETWORK": Reason.NETWORK,
    "SECRETS": Reason.SECRETS,
    "EXEC_UNKNOWN": Reason.UNKNOWN_CODE,
    "PERSISTENCE": Reason.PERSISTENCE,
    "UNCLEAR": Reason.UNCLEAR,
}

assert set(SENTENCES) == set(Reason)


def from_model_code(code: str) -> Reason | None:
    """The reason for a model's ``ASK <CODE>``; ``None`` when the code is not in the list."""
    return MODEL_CODES.get(code)


def sentence(reason: Reason) -> str:
    return SENTENCES[reason]


def ask_title(tool_name: str, reason: Reason) -> str:
    """The question title when Auto asks. Only for an ASK reason."""
    if reason in ALLOW_REASONS:
        raise ValueError(f"{reason.value} is an allow reason; it has no question title")
    return f"{tool_name} (Auto: {SENTENCES[reason]})"
