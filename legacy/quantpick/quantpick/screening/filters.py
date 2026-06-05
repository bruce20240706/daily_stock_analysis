"""Hard filters: remove ineligible stocks before scoring.

Filters operate on the universe DataFrame (one row per stock) and are
fail-safe: a missing column is treated as "do not filter on it".
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from quantpick.core.config import UniverseConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd

_ST_TOKENS = ("ST", "退")


def is_st_or_delisting(name: str) -> bool:
    """True if the stock name marks it as ST / *ST / delisting (risk warning)."""
    up = (name or "").upper()
    return any(tok in up for tok in _ST_TOKENS)


def board_of(code: str) -> str:
    """Classify an A-share code into a board by prefix."""
    c = code.split(".")[0]
    if c.startswith("688"):
        return "star"      # 科创板
    if c.startswith(("300", "301")):
        return "chinext"   # 创业板
    if c.startswith(("43", "83", "87", "88")):
        return "bse"       # 北交所
    return "main"


def apply_filters(universe: "pd.DataFrame", cfg: UniverseConfig) -> "pd.DataFrame":
    """Return the subset of ``universe`` passing all hard filters.

    Expected columns when available: ``code``, ``name``, ``turnover_amount``,
    ``market_cap``. Board inclusion is derived from ``code`` via :func:`board_of`.
    """
    # TODO(phase-1): chain the predicates below, guarding on column presence -
    #   drop is_st_or_delisting(name)
    #   drop suspended (no recent bar / zero volume)
    #   drop turnover_amount < cfg.min_turnover_amount
    #   drop market_cap < cfg.min_market_cap
    #   drop boards excluded by cfg (star / chinext / bse)
    raise NotImplementedError("apply_filters")
