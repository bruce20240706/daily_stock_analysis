"""LLM-backed Analyst (Claude / OpenAI-compatible).

Provider SDKs are lazy-imported. API keys are read from the environment only -
never from source code or config files. This is a Phase-3 skeleton.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from quantpick.ai.base import DISCLAIMER, Analyst
from quantpick.core.config import AIConfig
from quantpick.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from quantpick.core.types import AnalysisCard, ScoredStock

log = get_logger(__name__)


class LLMAnalyst(Analyst):
    """Summarizes each candidate for an LLM and parses a structured card back."""

    def __init__(self, config: AIConfig) -> None:
        self.config = config
        self.disclaimer = DISCLAIMER

    def _build_prompt(self, candidate: "ScoredStock", context: dict | None) -> str:
        # TODO(phase-3): compose a compact, cache-friendly prompt from the score
        # breakdown + key indicators + fundamentals + recent news/announcements.
        # Always include self.disclaimer; request structured JSON
        # (conclusion / bull_points / bear_points / risks) for AnalysisCard.
        raise NotImplementedError("LLMAnalyst._build_prompt")

    def _client(self):  # noqa: ANN202 - provider-specific client, typed in impl
        # TODO(phase-3): lazy-import the provider SDK (anthropic / openai);
        # resolve the key from env via AppConfig; enable prompt caching for the
        # shared system/instruction prefix to cut cost.
        raise NotImplementedError("LLMAnalyst._client")

    def analyze(
        self, candidates: "list[ScoredStock]", context: dict | None = None
    ) -> "list[AnalysisCard]":
        # TODO(phase-3): analyze the top config.max_candidates; parse responses
        # into AnalysisCard. Fail soft (return what succeeded) so the report
        # still renders if the provider errors.
        raise NotImplementedError("LLMAnalyst.analyze")
