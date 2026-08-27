"""Unit tests for pelican SVG extraction/validation (no LLM)."""

from __future__ import annotations

from pi_agent_cli.benchmarks.pelican import PELICAN_PROMPT, extract_svg, validate_pelican_svg

_MIN_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <circle cx="20" cy="80" r="10"/>
  <circle cx="60" cy="80" r="10"/>
  <path d="M10 80 L50 40 L90 80"/>
  <path d="M30 50 Q40 20 55 35"/>
</svg>"""


def test_pelican_prompt_is_canonical_willison_style():
    assert "pelican" in PELICAN_PROMPT.lower()
    assert "bicycle" in PELICAN_PROMPT.lower()
    assert "svg" in PELICAN_PROMPT.lower()


def test_extract_svg_from_fenced_block():
    text = f"Here you go:\n```svg\n{_MIN_SVG}\n```"
    assert extract_svg(text) == _MIN_SVG.strip()


def test_extract_svg_from_raw_markup():
    assert extract_svg(_MIN_SVG) == _MIN_SVG.strip()


def test_validate_pelican_svg_passes_minimal_geometry():
    report = validate_pelican_svg(_MIN_SVG)
    assert report.ok is True
    assert report.svg is not None
    assert report.checks["geometry"] is True


def test_validate_pelican_svg_fails_when_missing():
    report = validate_pelican_svg(None)
    assert report.ok is False
    assert report.checks.get("extracted") is False
