"""Core domain types shared across layers.

Kept dependency-light (stdlib only) so that importing types never pulls pandas.
DataFrame-typed payloads are referenced only under TYPE_CHECKING.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd  # noqa: F401


class Market(str, Enum):
    """Supported markets."""

    A_SHARE = "A"   # Shanghai / Shenzhen A-shares
    HK = "HK"       # Hong Kong


class Action(str, Enum):
    """Coarse operational suggestion attached to a recommendation."""

    WATCH = "watch"
    BUY = "buy"
    HOLD = "hold"
    AVOID = "avoid"


@dataclass(slots=True)
class StockInfo:
    """Static / slow-changing metadata for one instrument."""

    code: str                       # normalized symbol, e.g. "600519.SH" / "00700.HK"
    name: str
    market: Market
    industry: str = ""
    list_date: date | None = None


@dataclass(slots=True)
class FactorValue:
    """One factor's raw value and its normalized (cross-sectional) score."""

    name: str
    raw: float
    score: float                    # normalized, typically z-score or [0, 1] rank


@dataclass(slots=True)
class ScoredStock:
    """A screened candidate with an explainable score breakdown."""

    code: str
    name: str
    market: Market
    total_score: float
    factors: list[FactorValue] = field(default_factory=list)
    rank: int = 0
    # snapshot of selected indicator/price values, for reporting (small flat dict)
    snapshot: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class AnalysisCard:
    """Structured LLM qualitative analysis for one candidate (AI layer).

    Research aid only - never investment advice.
    """

    code: str
    name: str
    conclusion: str
    bull_points: list[str] = field(default_factory=list)
    bear_points: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    model: str = ""


@dataclass(slots=True)
class Recommendation:
    """Final per-stock output combining score and optional AI analysis."""

    stock: ScoredStock
    action: Action = Action.WATCH
    card: AnalysisCard | None = None
