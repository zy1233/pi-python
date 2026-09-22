"""Model tier routing for workflows."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ModelRoute:
    phase_pattern: str
    model: str
    use_regex: bool = False


@dataclass
class ModelRoutingConfig:
    default_model: str | None = None
    routes: list[ModelRoute] = field(default_factory=list)


def resolve_model_for_phase(
    phase: str | None,
    config: ModelRoutingConfig,
) -> str | None:
    if not phase or not config.routes:
        return config.default_model
    for route in config.routes:
        if route.use_regex:
            try:
                if re.search(route.phase_pattern, phase, re.IGNORECASE):
                    return route.model
            except re.error:
                pass
        elif phase == route.phase_pattern:
            return route.model
    return config.default_model


DEFAULT_MODEL_TIERS: dict[str, str] = {
    "small": "anthropic/claude-sonnet-4-20250514",
    "medium": "anthropic/claude-sonnet-4-20250514",
    "big": "anthropic/claude-opus-4-20250514",
}


def resolve_tier(tier: str | None, tiers: dict[str, str] | None = None) -> str | None:
    if not tier:
        return None
    mapping = tiers or DEFAULT_MODEL_TIERS
    return mapping.get(tier)
