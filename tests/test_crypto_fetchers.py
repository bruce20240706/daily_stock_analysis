"""crypto fetcher 解析与规范化测试（离线）。"""
import pandas as pd
import pytest
from data_provider.base import STANDARD_COLUMNS
from data_provider.crypto_base import CryptoExchangeBase
from data_provider.realtime_types import UnifiedRealtimeQuote, RealtimeSource


class _FakeCrypto(CryptoExchangeBase):
    name = "FakeCrypto"
    priority = 99

    def _to_exchange_symbol(self, code):
        return code.replace("/", "")

    def _request_klines(self, symbol, days):
        # 两根日线：[date_ms, o, h, l, c, volume, amount]
        return [
            [1717200000000, "100", "110", "90", "105", "10", "1050"],
            [1717286400000, "105", "120", "100", "115", "20", "2300"],
        ]

    def _parse_klines(self, raw):
        return pd.DataFrame(
            [
                {"date": r[0], "open": float(r[1]), "high": float(r[2]),
                 "low": float(r[3]), "close": float(r[4]),
                 "volume": float(r[5]), "amount": float(r[6])}
                for r in raw
            ]
        )

    def _request_ticker(self, symbol):
        return {"price": "115", "pct": "9.52", "vol": "20", "amt": "2300", "high": "120", "low": "100"}

    def _parse_ticker(self, raw, code):
        return UnifiedRealtimeQuote(
            code=code, name=code, source=RealtimeSource.FALLBACK,
            price=float(raw["price"]), change_pct=float(raw["pct"]),
            volume=float(raw["vol"]), amount=float(raw["amt"]),
            high=float(raw["high"]), low=float(raw["low"]),
        )


def test_fetch_and_normalize_to_standard_columns():
    f = _FakeCrypto()
    # 直接测纯解析 + 规范化（不依赖技术指标计算与网络）
    raw = f._fetch_raw_data("BTC/USDT", "2024-06-01", "2024-06-02")
    norm = f._normalize_data(raw, "BTC/USDT")
    for col in STANDARD_COLUMNS:
        assert col in norm.columns
    # 日期已转为 YYYY-MM-DD 字符串
    assert isinstance(norm.iloc[0]["date"], str) and len(norm.iloc[0]["date"]) == 10
    # pct_chg 由 close 计算：第二根 (115-105)/105*100 ≈ 9.52
    assert round(norm.iloc[1]["pct_chg"], 2) == 9.52
    assert norm.iloc[0]["code"] == "BTC/USDT"


def test_realtime_quote_returns_unified():
    f = _FakeCrypto()
    q = f.get_realtime_quote("BTC/USDT")
    assert isinstance(q, UnifiedRealtimeQuote)
    assert q.price == 115.0 and q.pe_ratio is None  # 估值字段保持 None


def test_days_to_limit_capping():
    f = _FakeCrypto()
    assert f._days_to_limit(30) >= 30
    assert f._days_to_limit(5000) <= f.MAX_LIMIT
