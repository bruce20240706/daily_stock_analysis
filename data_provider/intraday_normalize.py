"""分钟数据共享标准化(crypto + A股 通用)。

约定输入 df 已含 `datetime` 列(各 fetcher 先从自身源构建:crypto 由 ms 时间戳、
A股由 datetime 字符串)。本函数只做数值化/去空/升序/pct_chg/选列,不算技术指标——
与 CryptoExchangeBase._normalize_intraday 原语义逐字一致,供 crypto 与 A股 fetcher 复用。
"""
from __future__ import annotations

import pandas as pd

_KEEP = ["code", "datetime", "open", "high", "low", "close", "volume", "amount", "pct_chg"]


def normalize_intraday_df(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """标准化分钟 df:数值化 OHLCV、去空、按 datetime 升序、pct_chg、选列(不算指标)。

    入参 df 必须已含 `datetime` 列(任意可被 pandas 解析的时间)与 OHLCV 列。
    """
    if "datetime" not in df.columns:
        raise ValueError("normalize_intraday_df 需要 df 已含 datetime 列")
    df = df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"])
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "amount" not in df.columns:
        df["amount"] = None
    df = df.dropna(subset=["close", "volume"])
    df = df.sort_values("datetime").reset_index(drop=True)
    df["pct_chg"] = (df["close"].pct_change() * 100).fillna(0.0)
    df["code"] = code
    return df[[c for c in _KEEP if c in df.columns]]
