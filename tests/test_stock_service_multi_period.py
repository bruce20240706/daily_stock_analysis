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


# T1: change_percent contract at get_history_data boundary
def _ddf_pct(dates, closes, pcts):
    """Build DataFrame with explicit pct_chg values."""
    return pd.DataFrame({
        "date": pd.to_datetime(dates), "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [1.0] * len(dates), "amount": [10.0] * len(dates),
        "pct_chg": pcts,
    })


def test_daily_change_percent_nonzero(monkeypatch):
    """daily: pct_chg=3.5 -> change_percent == 3.5."""
    _patch(monkeypatch, _ddf_pct(["2024-01-01", "2024-01-02"], [10.0, 10.35], [0.0, 3.5]))
    out = StockService().get_history_data("600519", period="daily", days=120)
    assert out["period"] == "daily"
    assert out["data"][1]["change_percent"] == pytest.approx(3.5)


def test_daily_change_percent_zero_is_none(monkeypatch):
    """daily: pct_chg=0.0 -> change_percent is None (zero 視為缺失)."""
    _patch(monkeypatch, _ddf_pct(["2024-01-01"], [10.0], [0.0]))
    out = StockService().get_history_data("600519", period="daily", days=120)
    assert out["data"][0]["change_percent"] is None


def test_weekly_first_bar_change_percent_none(monkeypatch):
    """weekly: 第一根聚合 bar 的 change_percent 應為 None（首根 pct_chg=NaN→None）。"""
    dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
             "2024-01-08", "2024-01-09", "2024-01-10"]
    closes = [10.0] * 5 + [11.0] * 3
    pcts = [0.0] * 8
    _patch(monkeypatch, _ddf_pct(dates, closes, pcts))
    out = StockService().get_history_data("600519", period="weekly", days=120)
    assert out["period"] == "weekly"
    # 首根 bar pct_chg 由 pct_change() 重算为 NaN，归一为 None
    assert out["data"][0]["change_percent"] is None


def test_weekly_later_bar_change_percent_float(monkeypatch):
    """weekly: 后续聚合 bar 有重算的 change_percent（非 None）。"""
    dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
             "2024-01-08", "2024-01-09", "2024-01-10"]
    closes = [10.0] * 5 + [11.0] * 3
    pcts = [0.0] * 8
    _patch(monkeypatch, _ddf_pct(dates, closes, pcts))
    out = StockService().get_history_data("600519", period="weekly", days=120)
    assert len(out["data"]) >= 2
    # 第二根 bar: close=11.0 vs close=10.0 -> 10% rise
    assert out["data"][1]["change_percent"] is not None
    assert isinstance(out["data"][1]["change_percent"], float)
