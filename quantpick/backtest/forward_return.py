"""Forward-return validation: did past picks actually outperform?

Lightweight (non match-engine) validation: for picks made on a date, measure
T+N returns, win rate, and excess return vs a benchmark index.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd


@dataclass(slots=True)
class ForwardReturnReport:
    """Aggregate result of a forward-return check for one pick date / horizon."""

    horizon_days: int
    n_picks: int = 0
    win_rate: float = 0.0
    mean_return: float = 0.0
    mean_excess: float = 0.0                          # vs benchmark
    per_stock: dict[str, float] = field(default_factory=dict)


def evaluate(
    picks: list[str],
    pick_date: date,
    horizon_days: int,
    price_loader: "Callable[[str], pd.DataFrame]",
    benchmark: str | None = None,
) -> ForwardReturnReport:
    """Compute forward returns for ``picks`` over ``horizon_days``.

    Args:
        picks: candidate codes selected on ``pick_date``.
        price_loader: returns a date-indexed close-price frame for a code.
        benchmark: optional index code for excess-return computation.
    """
    # TODO(phase-2): per pick, ret = close[pick_date + H] / close[pick_date] - 1;
    # aggregate win_rate / mean_return; subtract benchmark return for excess.
    raise NotImplementedError("forward_return.evaluate")
