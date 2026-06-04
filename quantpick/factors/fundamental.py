"""Fundamental factors (skeletons). Instances register themselves on import."""
from __future__ import annotations

from typing import TYPE_CHECKING

from quantpick.factors.base import Factor, register

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd


class ValuePEFactor(Factor):
    name = "value_pe"
    category = "fundamental"
    higher_is_better = False     # lower PE preferred

    def compute(self, bars: "pd.DataFrame", fundamentals: "pd.DataFrame | None" = None) -> float:
        # TODO(phase-1): latest trailing PE (ttm)
        raise NotImplementedError(self.name)


class QualityROEFactor(Factor):
    name = "quality_roe"
    category = "fundamental"
    higher_is_better = True

    def compute(self, bars: "pd.DataFrame", fundamentals: "pd.DataFrame | None" = None) -> float:
        # TODO(phase-1): latest ROE
        raise NotImplementedError(self.name)


class GrowthRevenueFactor(Factor):
    name = "growth_revenue"
    category = "fundamental"
    higher_is_better = True

    def compute(self, bars: "pd.DataFrame", fundamentals: "pd.DataFrame | None" = None) -> float:
        # TODO(phase-1): YoY revenue growth
        raise NotImplementedError(self.name)


register(ValuePEFactor())
register(QualityROEFactor())
register(GrowthRevenueFactor())
