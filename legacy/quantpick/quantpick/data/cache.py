"""Local cache: parquet for bars, SQLite for metadata.

Layout::

    <cache_dir>/bars/<market>/<code>.parquet   # daily OHLCV per symbol
    <cache_dir>/meta.sqlite                     # stock lists, fetch bookkeeping
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from quantpick.core.types import Market

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd


class LocalCache:
    """File-backed cache. No third-party import needed to construct."""

    def __init__(self, cache_dir: str = "data_cache") -> None:
        self.root = Path(cache_dir)

    def _bars_path(self, market: Market, code: str) -> Path:
        return self.root / "bars" / market.value / f"{code}.parquet"

    @property
    def meta_db(self) -> Path:
        return self.root / "meta.sqlite"

    def has_bars(self, market: Market, code: str) -> bool:
        return self._bars_path(market, code).exists()

    def read_bars(self, market: Market, code: str) -> "pd.DataFrame | None":
        # TODO(phase-1): return pd.read_parquet(path) if it exists else None
        raise NotImplementedError("LocalCache.read_bars")

    def write_bars(self, market: Market, code: str, df: "pd.DataFrame") -> None:
        # TODO(phase-1): mkdir parents; df.to_parquet(path, index=...)
        raise NotImplementedError("LocalCache.write_bars")

    def last_cached_date(self, market: Market, code: str) -> date | None:
        # TODO(phase-1): read max(date) from cached parquet for incremental fetch
        raise NotImplementedError("LocalCache.last_cached_date")
