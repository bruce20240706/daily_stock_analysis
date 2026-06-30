# -*- coding: utf-8 -*-
"""港股分钟取数:AkshareFetcher HK 分支(stock_hk_hist_min_em)+ 门面路由白名单。全程离线 mock。"""
import pandas as pd
import pytest

from data_provider.akshare_fetcher import AkshareFetcher


def _fake_hk_min_df(n=66):
    # stock_hk_hist_min_em kline 路径返回 11 列(含额外 振幅/涨跌幅/涨跌额/换手率)
    return pd.DataFrame([
        {
            "时间": f"2026-05-06 {9 + i // 60:02d}:{i % 60:02d}:00",
            "开盘": 100.0, "收盘": 100.0, "最高": 101.0, "最低": 99.0,
            "成交量": 1.0, "成交额": 100.0,
            "振幅": 0.0, "涨跌幅": 0.0, "涨跌额": 0.0, "换手率": 0.0,
        }
        for i in range(n)
    ])


def test_akshare_hk_intraday_5m(monkeypatch):
    captured = {}

    def fake_min(symbol, period, start_date, end_date, adjust):
        captured.update(symbol=symbol, period=period, adjust=adjust)
        return _fake_hk_min_df()

    import akshare as ak
    monkeypatch.setattr(ak, "stock_hk_hist_min_em", fake_min, raising=True)

    df = AkshareFetcher().get_intraday_data("HK00700", "5m", start_date="2026-05-01", end_date="2026-05-10")
    assert captured["symbol"] == "00700"          # 归一去 hk 前缀 + zfill(5)
    assert captured["period"] == "5"              # _AK_PERIOD 映射
    assert captured["adjust"] == "qfq"
    assert {"datetime", "open", "close", "high", "low", "volume"}.issubset(df.columns)
    assert len(df) == 66                          # 额外列被 normalize 丢弃,行数不变


def test_akshare_hk_intraday_symbol_variants(monkeypatch):
    seen = []
    import akshare as ak
    monkeypatch.setattr(ak, "stock_hk_hist_min_em",
                        lambda symbol, period, start_date, end_date, adjust: (seen.append(symbol), _fake_hk_min_df())[1],
                        raising=True)
    for code in ("HK00700", "hk00700", "00700"):
        AkshareFetcher().get_intraday_data(code, "5m")
    assert seen == ["00700", "00700", "00700"]    # 三形归一一致


def test_akshare_hk_intraday_1m_fail_closed():
    with pytest.raises(NotImplementedError):
        AkshareFetcher().get_intraday_data("HK00700", "1m")


def test_akshare_hk_intraday_1m_independent_guard(monkeypatch):
    """验证 HK 独立守卫:即使 _AK_PERIOD 含 '1m'(A股将来入表),HK 路径仍 fail-closed。"""
    import data_provider.akshare_fetcher as af_mod
    monkeypatch.setitem(af_mod._AK_PERIOD, "1m", "1")
    with pytest.raises(NotImplementedError, match="港股 1m 不支持"):
        AkshareFetcher().get_intraday_data("HK00700", "1m")


def test_intraday_fetchers_for_hk_whitelist():
    from data_provider.base import DataFetcherManager
    mgr = DataFetcherManager()
    names = [f.name for f in mgr._intraday_fetchers_for("HK00700")]
    assert "AkshareFetcher" in names
    assert "YfinanceFetcher" in names
    assert "TushareFetcher" not in names          # A股专用 stk_mins,白名单排除
    assert names.index("AkshareFetcher") < names.index("YfinanceFetcher")  # akshare 主、yfinance 兜底


def test_intraday_fetchers_for_cn_still_excludes_yfinance():
    # market not in (us,hk) 改动不回归 cn:cn 仍排除 yfinance
    from data_provider.base import DataFetcherManager
    names = [f.name for f in DataFetcherManager()._intraday_fetchers_for("600519")]
    assert "YfinanceFetcher" not in names
