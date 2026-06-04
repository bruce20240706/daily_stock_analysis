"""Technical factors (skeletons). Instances register themselves on import."""
from __future__ import annotations

from typing import TYPE_CHECKING

from quantpick.factors.base import Factor, register

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd


class MomentumFactor(Factor):
    name = "momentum_20d"
    category = "technical"
    higher_is_better = True

    def compute(self, bars: "pd.DataFrame", fundamentals: "pd.DataFrame | None" = None) -> float:
        # TODO(phase-1): 20-day return = close[-1] / close[-21] - 1
        raise NotImplementedError(self.name)


class TrendStrengthFactor(Factor):
    name = "trend_adx"
    category = "technical"
    higher_is_better = True

    def compute(self, bars: "pd.DataFrame", fundamentals: "pd.DataFrame | None" = None) -> float:
        # TODO(phase-1): latest ADX value (requires indicators.compute_indicators first)
        raise NotImplementedError(self.name)


class VolumeSurgeFactor(Factor):
    name = "volume_surge"
    category = "technical"
    higher_is_better = True

    def compute(self, bars: "pd.DataFrame", fundamentals: "pd.DataFrame | None" = None) -> float:
        # TODO(phase-1): latest volume / mean(volume[-6:-1])
        raise NotImplementedError(self.name)


register(MomentumFactor())
register(TrendStrengthFactor())
register(VolumeSurgeFactor())
