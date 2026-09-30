"""A plain ``pytest`` run must not reach a live LLM API (audit P6-03).

Live tests carry the ``real_llm`` marker. The marker used to be documented as "deselected by
default" with nothing behind it: they only skipped when their key was missing, so a shell with
``OPENAI_API_KEY`` set turned ``pytest`` into paid calls. ``addopts`` in ``pyproject.toml`` now
leaves them out, and ``-m real_llm`` (the workflow, the docs) still selects them.

These tests start a fresh pytest that only collects, so they see the configuration exactly as
a developer's or CI's run does.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MATRIX_TESTS = Path(__file__).resolve().with_name("test_provider_matrix.py")


def _collect(*options: str) -> str:
    """The test ids a fresh pytest run collects from the matrix tests."""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            *options,
            str(_MATRIX_TESTS),
        ],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def test_a_plain_run_leaves_the_live_cases_out_and_keeps_the_offline_ones():
    collected = _collect()

    assert "test_matrix_rows_cover_spec" in collected
    assert "test_provider_capability" not in collected


def test_asking_for_the_live_cases_selects_them_and_only_them():
    """``-m real_llm`` on the command line has to win over the default in ``addopts``."""
    collected = _collect("-m", "real_llm")

    assert "test_provider_capability[deepseek-thinking]" in collected
    assert "test_matrix_rows_cover_spec" not in collected
