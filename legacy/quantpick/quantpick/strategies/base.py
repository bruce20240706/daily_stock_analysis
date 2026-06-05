"""Strategy model: a named bundle of universe filters + factor weights."""
from __future__ import annotations

from pydantic import BaseModel, Field

from quantpick.core.types import Market


class Strategy(BaseModel):
    """A config-driven strategy. Loaded from a YAML file in config/strategies/."""

    name: str
    description: str = ""
    markets: list[Market] = Field(default_factory=lambda: [Market.A_SHARE])
    # factor name -> weight (negative inverts the factor); need not sum to 1
    factor_weights: dict[str, float] = Field(default_factory=dict)
    top_n: int = 30
    # optional per-strategy override of the global universe turnover floor
    min_turnover_amount: float | None = None
