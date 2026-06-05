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


from data_provider.binance_fetcher import BinanceFetcher

# Binance /api/v3/klines 真实结构（截断到本测试需要的列）
_BINANCE_KLINES = [
    [1717200000000, "100", "110", "90", "105", "10.0", 1717286399999, "1050.0", 5, "6", "630", "0"],
    [1717286400000, "105", "120", "100", "115", "20.0", 1717372799999, "2300.0", 8, "12", "1380", "0"],
]
# Binance /api/v3/ticker/24hr（截断）
_BINANCE_TICKER = {"lastPrice": "115.0", "priceChangePercent": "9.52",
                   "volume": "20.0", "quoteVolume": "2300.0", "highPrice": "120.0", "lowPrice": "100.0"}


def test_binance_symbol_and_parse():
    f = BinanceFetcher()
    assert f._to_exchange_symbol("BTC/USDT") == "BTCUSDT"
    df = f._parse_klines(_BINANCE_KLINES)
    norm = f._normalize_data(df, "BTC/USDT")
    assert list(norm.columns)[:3] == ["code", "date", "open"]
    assert norm.iloc[1]["amount"] == 2300.0      # quoteAssetVolume
    assert norm.iloc[1]["volume"] == 20.0        # base volume
    q = f._parse_ticker(_BINANCE_TICKER, "BTC/USDT")
    assert q.price == 115.0 and q.amount == 2300.0


from data_provider.okx_fetcher import OkxFetcher

# OKX /api/v5/market/candles 返回 {"data": [[ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm], ...]}（新→旧）
_OKX_DATA = [
    ["1717286400000", "105", "120", "100", "115", "20", "0.18", "2300", "1"],
    ["1717200000000", "100", "110", "90", "105", "10", "0.1", "1050", "1"],
]
_OKX_TICKER = {"last": "115", "open24h": "105", "vol24h": "20", "volCcy24h": "2300", "high24h": "120", "low24h": "100"}


def test_okx_symbol_and_parse():
    f = OkxFetcher()
    assert f._to_exchange_symbol("BTC/USDT") == "BTC-USDT"
    df = f._parse_klines(_OKX_DATA)
    norm = f._normalize_data(df, "BTC/USDT")   # 内部按 date 升序
    assert norm.iloc[0]["close"] == 105.0 and norm.iloc[1]["close"] == 115.0
    assert norm.iloc[1]["amount"] == 2300.0    # volCcyQuote
    q = f._parse_ticker(_OKX_TICKER, "BTC/USDT")
    assert q.price == 115.0 and round(q.change_pct, 2) == 9.52  # (115-105)/105*100


from data_provider.coinbase_fetcher import CoinbaseFetcher

# Coinbase /products/{id}/candles 返回 [[time, low, high, open, close, volume], ...]（注意列序）
_CB_CANDLES = [
    [1717286400, 100, 120, 105, 115, 20],
    [1717200000, 90, 110, 100, 105, 10],
]
_CB_TICKER = {"price": "115", "volume": "20"}


def test_coinbase_symbol_parse_amount_none():
    f = CoinbaseFetcher()
    assert f._to_exchange_symbol("BTC/USDT") == "BTC-USDT"
    df = f._parse_klines(_CB_CANDLES)
    norm = f._normalize_data(df, "BTC/USDT")
    assert norm.iloc[0]["open"] == 100.0 and norm.iloc[1]["open"] == 105.0  # 升序后
    assert pd.isna(norm.iloc[0]["amount"])      # 无 quote volume -> None/NaN，不伪造
    q = f._parse_ticker(_CB_TICKER, "BTC/USDT")
    assert q.price == 115.0 and q.amount is None


from data_provider.base import DataFetcherManager


def test_filter_keeps_only_crypto_fetchers():
    mgr = DataFetcherManager()
    fetchers = mgr._get_fetchers_snapshot()
    kept = mgr._filter_daily_fetchers_for_market(fetchers, "crypto")
    names = {f.name for f in kept}
    assert names <= {"BinanceFetcher", "OkxFetcher", "CoinbaseFetcher"}
    assert "EfinanceFetcher" not in names and "YfinanceFetcher" not in names
    assert {"BinanceFetcher", "OkxFetcher", "CoinbaseFetcher"} & names


def test_crypto_fetchers_registered():
    mgr = DataFetcherManager()
    names = {f.name for f in mgr._get_fetchers_snapshot()}
    assert {"BinanceFetcher", "OkxFetcher", "CoinbaseFetcher"} <= names
