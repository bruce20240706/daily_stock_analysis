"""Application configuration models and loader (pydantic + YAML + env).

Secrets (API keys) are resolved from the environment only - never persisted in
config files. See ``.env.example``.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from quantpick.core.errors import ErrorCode, Result
from quantpick.core.types import Market


class DataConfig(BaseModel):
    # ordered data sources; the manager tries them in order with fallback
    sources: list[str] = Field(default_factory=lambda: ["akshare", "baostock", "adata"])
    cache_dir: str = "data_cache"
    history_days: int = 800     # calendar days of history to keep / fetch per symbol


class UniverseConfig(BaseModel):
    markets: list[Market] = Field(default_factory=lambda: [Market.A_SHARE])
    include_star_market: bool = True    # 科创板 688xxx
    include_chinext: bool = True        # 创业板 30xxxx
    include_bse: bool = False           # 北交所
    min_turnover_amount: float = 2e8    # min daily turnover (CNY / HKD) hard filter
    min_market_cap: float = 0.0


class AIConfig(BaseModel):
    enabled: bool = False
    provider: str = "anthropic"         # "anthropic" | "openai"
    model: str = "claude-sonnet-4-6"
    max_candidates: int = 10            # analyze only the top-N candidates
    # API keys come from env (ANTHROPIC_API_KEY / OPENAI_API_KEY), never config.


class AppConfig(BaseModel):
    data: DataConfig = Field(default_factory=DataConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    strategies_dir: str = "config/strategies"
    reports_dir: str = "reports"
    log_level: str = "INFO"

    # --- secrets resolved from the environment, not persisted ---
    @staticmethod
    def anthropic_api_key() -> str | None:
        return os.environ.get("ANTHROPIC_API_KEY")

    @staticmethod
    def openai_api_key() -> str | None:
        return os.environ.get("OPENAI_API_KEY")


DEFAULT_CONFIG_PATH = Path("config/config.yaml")


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> Result[AppConfig]:
    """Load and validate app config from YAML. Returns a Result (never raises).

    A missing file is not an error: defaults are returned.
    """
    p = Path(path)
    if not p.exists():
        return Result.success(AppConfig())
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return Result.success(AppConfig.model_validate(raw))
    except (yaml.YAMLError, ValueError) as exc:
        return Result.fail(ErrorCode.CONFIG_INVALID, str(exc))
