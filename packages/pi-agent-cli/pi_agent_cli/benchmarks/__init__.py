"""Product-level smoke benchmarks for pi-agent-cli / pi TUI."""

from pi_agent_cli.benchmarks.pelican import (
    PELICAN_PROMPT,
    PelicanSvgReport,
    extract_svg,
    validate_pelican_svg,
)

__all__ = [
    "PELICAN_PROMPT",
    "PelicanSvgReport",
    "extract_svg",
    "validate_pelican_svg",
]
