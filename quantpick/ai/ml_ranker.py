"""ML ranking interface (future, Phase 4).

Placeholder for a learned ranker (e.g. LightGBM / qlib-style) that re-ranks
candidates from factor features. Not implemented - the interface is reserved so
the screening pipeline can grow an optional ML stage without restructuring.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from quantpick.core.types import ScoredStock


class MLRanker(ABC):
    """Re-ranks scored candidates using a trained model."""

    @abstractmethod
    def rank(self, candidates: "list[ScoredStock]") -> "list[ScoredStock]":
        """Return candidates re-ordered by predicted forward performance."""
