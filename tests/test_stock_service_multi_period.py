import pandas as pd
import pytest
from src.services.stock_service import StockService


class _FakeManager:
    def __init__(self, df):
        self._df = df
        self.calls = []

    def get_daily_data(self, code, days=30):
        self.calls.append(days)
        return self._df, "fake"

    def get_stock_name(self, code):
        return "测试股"


def _patch(monkeypatch, df):
    fake = _FakeManager(df)
    import data_provider.base as base_mod
    monkeypatch.setattr(base_mod, "DataFetcherManager", lambda: fake)
    return fake


def _ddf(dates, closes):
    return pd.DataFrame({
        "date": pd.to_datetime(dates), "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [1.0] * len(dates), "amount": [10.0] * len(dates),
        "pct_chg": [0.0] * len(dates),
    })


def test_daily_unchanged(monkeypatch):
    _patch(monkeypatch, _ddf(["2024-01-01", "2024-01-02"], [10, 11]))
    out = StockService().get_history_data("600519", period="daily", days=120)
    assert out["period"] == "daily" and len(out["data"]) == 2
    assert out["data"][0]["close"] == 10.0


def test_weekly_resamples_and_warmup_fetch(monkeypatch):
    dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
    fake = _patch(monkeypatch, _ddf(dates, [10, 11, 12, 13, 14, 20]))
    out = StockService().get_history_data("600519", period="weekly", days=120)
    assert out["period"] == "weekly" and len(out["data"]) == 2
    assert out["data"][0]["close"] == 14.0
    assert fake.calls == [120 + 200]   # weekly warmup


def test_invalid_period_raises(monkeypatch):
    with pytest.raises(ValueError):
        StockService().get_history_data("600519", period="hourly")


def test_empty_df_graceful(monkeypatch):
    _patch(monkeypatch, pd.DataFrame())
    out = StockService().get_history_data("600519", period="weekly")
    assert out["data"] == []
