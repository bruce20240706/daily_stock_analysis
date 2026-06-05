"""DataManager: multi-source fallback + local cache + incremental update."""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from quantpick.core.config import DataConfig
from quantpick.core.logging import get_logger
from quantpick.core.types import Market
from quantpick.data.base import DataSource
from quantpick.data.cache import LocalCache

if TYPE_CHECKING:  # pragma: no cover - typing only
    from quantpick.core.errors import Result
    import pandas as pd

log = get_logger(__name__)


def build_source(name: str) -> DataSource | None:
    """Construct a source by id (lazy import keeps backends optional)."""
    if name == "akshare":
        from quantpick.data.akshare_source import AkshareSource

        return AkshareSource()
    if name == "baostock":
        from quantpick.data.baostock_source import BaostockSource

        return BaostockSource()
    if name == "adata":
        from quantpick.data.adata_source import AdataSource

        return AdataSource()
    log.warning("unknown data source '%s' - skipped", name)
    return None


class DataManager:
    """Front door for market data: cache-first, with ordered source fallback."""

    def __init__(self, config: DataConfig | None = None) -> None:
        self.config = config or DataConfig()
        self.cache = LocalCache(self.config.cache_dir)
        self.sources: list[DataSource] = [
            s for n in self.config.sources if (s := build_source(n)) is not None
        ]

    def get_daily_bars(
        self,
        code: str,
        market: Market,
        start: date,
        end: date,
        adjust: str = "qfq",
        use_cache: bool = True,
    ) -> "Result[pd.DataFrame]":
        # TODO(phase-1):
        #   1) if use_cache and cache.has_bars: read; incrementally top up to `end`
        #   2) else iterate self.sources where source.supports(market); first OK wins
        #   3) write back to cache; return normalized frame
        raise NotImplementedError("DataManager.get_daily_bars")

    def update_universe(self, market: Market) -> "Result[pd.DataFrame]":
        # TODO(phase-1): fetch stock list via first supporting source; persist to meta db
        raise NotImplementedError("DataManager.update_universe")
