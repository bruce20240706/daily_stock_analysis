"""Factor abstraction and a global registry.

A Factor maps one stock's prepared data (indicator-augmented bars +
fundamentals) to a single raw number. Cross-sectional normalization into a
comparable score happens later in the scorer.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd


class Factor(ABC):
    """One named, single-valued factor."""

    name: str = "base"
    category: str = "generic"        # "technical" | "fundamental" | ...
    higher_is_better: bool = True    # direction; the scorer flips when False

    @abstractmethod
    def compute(self, bars: "pd.DataFrame", fundamentals: "pd.DataFrame | None" = None) -> float:
        """Return the raw factor value for one stock (NaN if not computable)."""


_REGISTRY: dict[str, Factor] = {}


def register(factor: Factor) -> Factor:
    """Register a factor instance under its ``name`` (raises on duplicates)."""
    if factor.name in _REGISTRY:
        raise ValueError(f"duplicate factor name: {factor.name}")
    _REGISTRY[factor.name] = factor
    return factor


def get_factor(name: str) -> Factor | None:
    return _REGISTRY.get(name)


def all_factors() -> dict[str, Factor]:
    return dict(_REGISTRY)
