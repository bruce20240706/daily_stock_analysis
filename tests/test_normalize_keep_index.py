import pandas as pd
from datetime import datetime
from src.services.alert_indicators import normalize_ohlcv

_COLS = ("open", "high", "low", "close", "volume")
_NOW = datetime(2020, 1, 1, 17, 0, 0)  # >=16:00, 禁用 _drop_partial_today


def _df(dates, closes):
    return pd.DataFrame({
        "date": dates, "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [100.0] * len(closes),
    })


def test_keep_original_index_default_off_unchanged():
    df = _df(["2020-01-03", "2020-01-01", "2020-01-02"], [3.0, 1.0, 2.0])
    out = normalize_ohlcv(df, required_columns=_COLS, now=_NOW)
    assert "_orig_idx" not in out.columns
    assert list(out["close"]) == [1.0, 2.0, 3.0]  # 已按 date 排序


def test_keep_original_index_maps_sorted_rows_to_raw():
    # 原始行号 0,1,2 对应 close 3,1,2;按 date 排序后顺序变 1,2,3 → _orig_idx 应为 1,2,0
    df = _df(["2020-01-03", "2020-01-01", "2020-01-02"], [3.0, 1.0, 2.0])
    out = normalize_ohlcv(df, required_columns=_COLS, now=_NOW, keep_original_index=True)
    assert list(out["close"]) == [1.0, 2.0, 3.0]
    assert list(out["_orig_idx"]) == [1, 2, 0]


def test_keep_original_index_survives_dropna():
    # 第 1 行(close=NaN)被 dropna 删除;_orig_idx 跳过 1
    df = _df(["2020-01-01", "2020-01-02", "2020-01-03"], [1.0, float("nan"), 3.0])
    out = normalize_ohlcv(df, required_columns=_COLS, now=_NOW, keep_original_index=True)
    assert list(out["_orig_idx"]) == [0, 2]
