"""Multi-factor weighted scoring.

Combines per-stock normalized factor scores into a single total using
configured weights. Pure and unit-tested.
"""
from __future__ import annotations

from quantpick.core.types import FactorValue, Market, ScoredStock


def weighted_score(factor_scores: dict[str, float], weights: dict[str, float]) -> float:
    """Weighted sum of factor scores, normalized by total absolute weight.

    Missing factors contribute 0. Weights need not sum to 1; normalizing by the
    total absolute weight keeps totals comparable across strategies.
    """
    total_w = sum(abs(w) for w in weights.values())
    if total_w == 0:
        return 0.0
    s = sum(w * factor_scores.get(name, 0.0) for name, w in weights.items())
    return s / total_w


def score_and_rank(
    rows: list[tuple[str, str, Market, dict[str, float]]],
    weights: dict[str, float],
    top_n: int | None = None,
) -> list[ScoredStock]:
    """Score each row, sort by total descending, assign 1-based ranks.

    Args:
        rows: ``(code, name, market, factor_scores)`` per candidate, where
            ``factor_scores`` are already cross-sectionally normalized.
        weights: factor name -> weight.
        top_n: keep only the best N (None keeps all).
    """
    scored: list[ScoredStock] = []
    for code, name, market, fscores in rows:
        total = weighted_score(fscores, weights)
        factors = [
            FactorValue(name=n, raw=float("nan"), score=fscores.get(n, 0.0)) for n in weights
        ]
        scored.append(
            ScoredStock(code=code, name=name, market=market, total_score=total, factors=factors)
        )
    scored.sort(key=lambda s: s.total_score, reverse=True)
    for i, stock in enumerate(scored, start=1):
        stock.rank = i
    return scored if top_n is None else scored[:top_n]
