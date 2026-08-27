"""Pelican-on-a-bicycle benchmark (Simon Willison / Karpathy-style SVG smoke test).

See docs/benchmarks/PELCAN-BICYCLE.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

PELICAN_PROMPT = (
    "Generate an SVG of a pelican riding a bicycle. "
    "Output only valid SVG markup with xmlns and a viewBox, no markdown fences or explanation."
)

_FENCE_RE = re.compile(r"```(?:svg|xml)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)
_SVG_RE = re.compile(r"(<svg[\s\S]*?</svg>)", re.IGNORECASE)


@dataclass(frozen=True)
class PelicanSvgReport:
    ok: bool
    checks: dict[str, bool]
    svg: str | None
    notes: tuple[str, ...] = ()


def extract_svg(text: str) -> str | None:
    """Pull the first SVG document from model text (raw or fenced)."""
    raw = text.strip()
    if not raw:
        return None
    for match in _FENCE_RE.finditer(raw):
        inner = match.group(1).strip()
        svg = _SVG_RE.search(inner)
        if svg:
            return svg.group(1).strip()
        if inner.lower().startswith("<svg"):
            return inner
    svg = _SVG_RE.search(raw)
    if svg:
        return svg.group(1).strip()
    if raw.lower().startswith("<svg"):
        return raw
    return None


def validate_pelican_svg(svg: str | None) -> PelicanSvgReport:
    """Heuristic pass/fail for the pelican bicycle benchmark (not artistic scoring)."""
    notes: list[str] = []
    if svg is None:
        return PelicanSvgReport(
            ok=False,
            checks={"extracted": False},
            svg=None,
            notes=("no SVG found in model output",),
        )
    lower = svg.lower()
    checks = {
        "extracted": True,
        "svg_root": "<svg" in lower and "</svg>" in lower,
        "xmlns": "xmlns" in lower,
        "viewbox": "viewbox" in lower,
        "geometry": any(
            tag in lower for tag in ("<path", "<circle", "<rect", "<ellipse", "<polygon")
        ),
        "min_size": len(svg.strip()) >= 200,
    }
    if not checks["geometry"]:
        notes.append("no path/circle/rect/ellipse/polygon elements")
    if not checks["min_size"]:
        notes.append("SVG shorter than 200 characters")
    return PelicanSvgReport(ok=all(checks.values()), checks=checks, svg=svg, notes=tuple(notes))


def default_artifact_dir(home: Path | str | None = None) -> Path:
    from pi_agent_cli.config import pi_home

    return pi_home(home) / "benchmarks" / "pelican"


def save_pelican_artifact(svg: str, *, home: Path | str | None = None) -> Path:
    out_dir = default_artifact_dir(home)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"pelican-{stamp}.svg"
    path.write_text(svg, encoding="utf-8")
    return path
