"""日线 → 周/月线本地聚合（市场无关、确定性纯函数）。

由日线帧重采样到周/月线：open=first/high=max/low=min/close=last/volume,amount=sum，
pct_chg 按本期 close vs 上期 close 重算；每根 bar 的 date 取该周期内最后一个交易日。
仅依赖 date/open/high/low/close（+可选 volume/amount），不要求输入含 pct_chg。
"""
from __future__ import annotations

import pandas as pd

_PERIOD_FREQ = {"weekly": "W", "monthly": "M"}
_OUT_COLS = ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]


def resample_ohlc(df_daily: pd.DataFrame, period: str) -> pd.DataFrame:
    if period not in _PERIOD_FREQ:
        raise ValueError(f"resample_ohlc 不支持周期 '{period}'，仅支持 weekly/monthly")
    if df_daily is None or df_daily.empty:
        return pd.DataFrame(columns=_OUT_COLS)

    df = df_daily.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")

    has_vol = "volume" in df.columns
    has_amt = "amount" in df.columns

    # 强制将 volume/amount 转为 float64（None → NaN），防止 object dtype 0 流入
    # attach_ma_indicators 触发 ZeroDivisionError（S1 回归）。
    if has_vol:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    if has_amt:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")

    key = df["date"].dt.to_period(_PERIOD_FREQ[period])
    grouped = df.groupby(key, sort=True)

    out = pd.DataFrame({
        "date": grouped["date"].last().to_numpy(),
        "open": grouped["open"].first().to_numpy(),
        "high": grouped["high"].max().to_numpy(),
        "low": grouped["low"].min().to_numpy(),
        "close": grouped["close"].last().to_numpy(),
    })
    out["volume"] = grouped["volume"].sum().to_numpy() if has_vol else 0.0
    out["amount"] = grouped["amount"].sum().to_numpy() if has_amt else 0.0
    out["pct_chg"] = out["close"].pct_change() * 100.0
    return out[_OUT_COLS]
