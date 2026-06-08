# -*- coding: utf-8 -*-
"""
TDD: get_main_indices(region="crypto") crypto 篮子分支测试

- test_crypto_basket_uses_config_symbols: 按 env var 拉取指定币对，映射为指数行
- test_crypto_basket_skips_invalid_and_failed: 非法代码 / 取价失败均跳过
"""
from data_provider.base import DataFetcherManager
from data_provider.realtime_types import UnifiedRealtimeQuote, RealtimeSource


def _fake_quote(code):
    return UnifiedRealtimeQuote(
        code=code, name=code, source=RealtimeSource.FALLBACK,
        price=100.0, change_pct=2.5, volume=10.0, amount=1000.0, high=105.0, low=95.0,
    )


def test_crypto_basket_uses_config_symbols(monkeypatch):
    # 实现就地读 os.getenv（与 data_provider 既有风格一致），env 立即生效
    monkeypatch.setenv("CRYPTO_MARKET_REVIEW_SYMBOLS", "BTC/USDT,ETH/USDT")
    mgr = DataFetcherManager()
    monkeypatch.setattr(mgr, "get_realtime_quote", lambda code: _fake_quote(code))

    rows = mgr.get_main_indices(region="crypto")
    assert len(rows) == 2
    codes = {r["code"] for r in rows}
    assert codes == {"BTC/USDT", "ETH/USDT"}
    row = rows[0]
    for key in ("code", "name", "current", "change", "change_pct", "open",
                "high", "low", "prev_close", "volume", "amount", "amplitude"):
        assert key in row
    assert row["current"] == 100.0
    assert row["change_pct"] == 2.5


def test_crypto_basket_skips_invalid_and_failed(monkeypatch):
    monkeypatch.setenv("CRYPTO_MARKET_REVIEW_SYMBOLS", "BTC/USDT,NOTACOIN,ETH/USDT")
    mgr = DataFetcherManager()

    def quote(code):
        return None if code == "ETH/USDT" else _fake_quote(code)

    monkeypatch.setattr(mgr, "get_realtime_quote", quote)
    rows = mgr.get_main_indices(region="crypto")
    # NOTACOIN 非法跳过；ETH 取价失败跳过；仅剩 BTC
    assert [r["code"] for r in rows] == ["BTC/USDT"]
