"""Analyst interface for the AI-enhancement layer."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from quantpick.core.types import AnalysisCard, ScoredStock

#: Standard disclaimer included in every LLM prompt and rendered report.
DISCLAIMER = (
    "This analysis is an automated research aid for personal use only. "
    "It is NOT investment advice. Markets carry risk; invest prudently."
)


class Analyst(ABC):
    """Turns scored candidates into qualitative analysis cards."""

    @abstractmethod
    def analyze(
        self, candidates: "list[ScoredStock]", context: dict | None = None
    ) -> "list[AnalysisCard]":
        """Produce one AnalysisCard per candidate (research aid only)."""
