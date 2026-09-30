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


# Built-in tier defaults, keyed by the *parent's* provider. A default may only name a model
# on the provider it is keyed under: routing must never silently move a sub-agent to a
# vendor the user has not configured (and hand it another vendor's credentials).
DEFAULT_MODEL_TIERS_BY_PROVIDER: dict[str, dict[str, str]] = {
    "anthropic": {
        "small": "anthropic/claude-sonnet-4-20250514",
        "medium": "anthropic/claude-sonnet-4-20250514",
        "big": "anthropic/claude-opus-4-20250514",
    },
}


def resolve_tier(
    tier: str | None,
    tiers: dict[str, str] | None = None,
    *,
    provider: str | None = None,
) -> str | None:
    """Map a cost tier to a ``"provider/model"`` id; ``None`` means inherit the parent model.

    An explicit *tiers* mapping always wins (the user chose it and may name any provider)
    and has no fallback to the defaults. Without one, the built-in defaults apply only
    when *provider* (the parent's) has an entry in ``DEFAULT_MODEL_TIERS_BY_PROVIDER``.
    """
    if not tier:
        return None
    if tiers:
        return tiers.get(tier)
    return DEFAULT_MODEL_TIERS_BY_PROVIDER.get((provider or "").casefold(), {}).get(tier)
