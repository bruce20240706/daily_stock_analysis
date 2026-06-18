# M4-A 周/月线多周期 + 多周期共振 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 解锁周/月线 K 线（本地聚合）并给日线信号附「多周期共振」标记，全部建立在 M3 日线可信度之上、不触碰回测管线。

**Architecture:** 两个新增纯逻辑单元（`data_provider/resample.py` 日线→周/月聚合、`src/services/multi_period_resonance.py` 趋势+共振判定）+ `stock_service` 解锁非日线 + `signal_board_service` 按方向门控算共振并经共享编排函数透传到 `/signals` 与 `/board` + 前端图表周期切换与共振徽标。

**Tech Stack:** Python(pandas/FastAPI/Pydantic/SQLAlchemy) 后端；React+TS+vitest 前端；klinecharts 图表。

**基线分支：** `feat/m4a-multi-period` ← `feat/m3-signal-credibility@3767d61a`（worktree `/root/dsa-m4a`）。Spec：`docs/superpowers/specs/2026-06-18-m4a-multi-period-design.md`。

## Global Constraints

- commit message：英文类型前缀 + 中文描述（如 `feat: 新增日线->周/月线本地聚合`）；**不加 `Co-Authored-By`**；不加工具/agent 前缀。
- **零新增配置**：共振无开关；不新增 `.env`/`config_registry`/`settingsHelp` 项。
- 不写死密钥/账号/路径/端口/模型名。
- 共振档位枚举（后端 Literal / 前端 type）统一为 `'none' | 'weekly' | 'weekly_monthly'`。
- 趋势方向枚举统一为 `'bullish' | 'bearish' | 'neutral'`（与现有 `SignalDirection`/`rule_direction` 一致）。
- 追加式 schema：新字段一律带默认值（后端 `Field("none", ...)`，前端 `?? 'none'`），不破坏旧客户端。
- daily 路径与 M3 信号抓取路径**保持字节级不变**；周/月与共振为新增/门控路径。
- 前端命令在无空格 worktree `/root/dsa-m4a/apps/dsa-web` 下执行。
- 门禁：后端 `./scripts/ci_gate.sh`（flake8 + `pytest -m "not network"`）；前端 `npm ci && npm run lint && npm run build` + vitest。
- 标注/徽标文案为**内联中文**（仓库无 i18n 框架，与现有 `SignalBoardGroup`/`SignalDrilldownPanel` 内联中文一致）。

---

### Task 1: 日线→周/月线本地聚合 + 共享 MA helper

**Files:**
- Create: `data_provider/resample.py`
- Modify: `data_provider/base.py`（抽出模块级 `attach_ma_indicators`，复用于 `_calculate_indicators:573-575/581-583`）
- Test: `tests/test_resample.py`

**Interfaces:**
- Produces: `resample_ohlc(df_daily: pd.DataFrame, period: str) -> pd.DataFrame`（列 `['date','open','high','low','close','volume','amount','pct_chg']`，date 为该周期最后交易日 datetime，升序）；`attach_ma_indicators(df: pd.DataFrame) -> pd.DataFrame`（附 `ma5/ma10/ma20`，含 volume 时附 `volume_ratio`，不四舍五入）。

- [ ] **Step 1: 写失败测试** `tests/test_resample.py`

```python
import pandas as pd
import pytest
from data_provider.resample import resample_ohlc


def _daily(dates, closes, *, opens=None, highs=None, lows=None, vols=None, amts=None):
    n = len(dates)
    return pd.DataFrame({
        "date": dates,
        "open": opens if opens is not None else closes,
        "high": highs if highs is not None else closes,
        "low": lows if lows is not None else closes,
        "close": closes,
        "volume": vols if vols is not None else [1.0] * n,
        "amount": amts if amts is not None else [10.0] * n,
    })


def test_weekly_aggregation_ohlcv():
    df = _daily(
        ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"],
        closes=[10, 11, 12, 13, 14, 20],
        opens=[10, 10, 10, 10, 10, 20], highs=[10, 11, 12, 13, 15, 21],
        lows=[9, 9, 9, 9, 9, 19], vols=[1, 1, 1, 1, 1, 5], amts=[100, 100, 100, 100, 100, 500],
    )
    out = resample_ohlc(df, "weekly")
    assert len(out) == 2
    wk1 = out.iloc[0]
    assert wk1["open"] == 10 and wk1["high"] == 15 and wk1["low"] == 9 and wk1["close"] == 14
    assert wk1["volume"] == 5 and wk1["amount"] == 500
    assert pd.Timestamp(wk1["date"]) == pd.Timestamp("2024-01-05")  # 该周最后交易日


def test_pct_chg_recomputed():
    out = resample_ohlc(_daily(["2024-01-01", "2024-01-08"], [100, 110]), "weekly")
    assert pd.isna(out.iloc[0]["pct_chg"])
    assert out.iloc[1]["pct_chg"] == pytest.approx(10.0)


def test_monthly_groups_by_calendar_month():
    out = resample_ohlc(_daily(["2024-01-31", "2024-02-01", "2024-02-29"], [10, 11, 12]), "monthly")
    assert len(out) == 2
    assert out.iloc[1]["close"] == 12
    assert pd.Timestamp(out.iloc[1]["date"]) == pd.Timestamp("2024-02-29")


def test_string_dates_and_missing_volume_amount():
    df = pd.DataFrame({"date": ["2024-01-01", "2024-01-08"],
                       "open": [10, 20], "high": [10, 20], "low": [10, 20], "close": [10, 20]})
    out = resample_ohlc(df, "weekly")
    assert list(out.columns) == ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]
    assert (out["volume"] == 0.0).all()


def test_empty_input_returns_empty_with_columns():
    out = resample_ohlc(pd.DataFrame(), "weekly")
    assert out.empty and list(out.columns) == ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]


def test_invalid_period_raises():
    with pytest.raises(ValueError):
        resample_ohlc(_daily(["2024-01-01"], [10]), "hourly")
```

- [ ] **Step 2: 运行确认失败**

Run: `/root/AI/WorkSpace/cursor/AI\ _Trading_System/.venv/bin/python -m pytest tests/test_resample.py -q`（在 `/root/dsa-m4a` 下）
Expected: FAIL（`ModuleNotFoundError: data_provider.resample`）

- [ ] **Step 3: 实现 `data_provider/resample.py`**

```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `… -m pytest tests/test_resample.py -q`
Expected: PASS

- [ ] **Step 5: 抽出 `attach_ma_indicators`（`data_provider/base.py`）**

在 `base.py` 模块级（`STANDARD_COLUMNS` 附近）新增函数：

```python
def attach_ma_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """在含 close（可选 volume）的帧上附 ma5/ma10/ma20 与 volume_ratio。

    与日线 _calculate_indicators 同口径（rolling min_periods=1；volume_ratio 用前5日均量 shift(1)、NaN→1.0），
    供日线与周/月线复用，避免均线公式漂移。不就地四舍五入（调用方按需 round）。
    """
    df = df.copy()
    df['ma5'] = df['close'].rolling(window=5, min_periods=1).mean()
    df['ma10'] = df['close'].rolling(window=10, min_periods=1).mean()
    df['ma20'] = df['close'].rolling(window=20, min_periods=1).mean()
    if 'volume' in df.columns:
        avg_volume_5 = df['volume'].rolling(window=5, min_periods=1).mean()
        df['volume_ratio'] = df['volume'] / avg_volume_5.shift(1)
        df['volume_ratio'] = df['volume_ratio'].fillna(1.0)
    return df
```

把 `_calculate_indicators`（562-590）改为复用它，**保留既有 2 位小数四舍五入**：

```python
    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算技术指标（MA5/10/20、volume_ratio），复用 attach_ma_indicators 同口径。"""
        df = attach_ma_indicators(df)
        for col in ['ma5', 'ma10', 'ma20', 'volume_ratio']:
            if col in df.columns:
                df[col] = df[col].round(2)
        return df
```

- [ ] **Step 6: 追加 MA helper 校验测试到 `tests/test_resample.py`**

```python
from data_provider.base import attach_ma_indicators


def test_attach_ma_matches_daily_formula():
    df = pd.DataFrame({"close": [10, 11, 12, 13, 14, 15], "volume": [1, 2, 3, 4, 5, 6]})
    out = attach_ma_indicators(df)
    assert out["ma5"].iloc[-1] == pytest.approx(sum([11, 12, 13, 14, 15]) / 5)
    assert "ma10" in out and "ma20" in out and "volume_ratio" in out
    assert out["volume_ratio"].iloc[0] == pytest.approx(1.0)  # 首根 shift NaN→1.0
```

- [ ] **Step 7: 跑 resample 测试 + 现有 data_provider 测试做 MA 回归**

Run: `… -m pytest tests/test_resample.py -q && … -m pytest tests/ -q -k "base or fetch or daily" -m "not network"`
Expected: PASS（确认抽 helper 未改变日线 MA 输出）

- [ ] **Step 8: 提交**

```bash
git add data_provider/resample.py data_provider/base.py tests/test_resample.py
git commit -m "feat: 新增日线->周/月线本地聚合 resample + 抽出共享 attach_ma_indicators"
```

---

### Task 2: 多周期共振判定（纯逻辑）

**Files:**
- Create: `src/services/multi_period_resonance.py`
- Test: `tests/test_multi_period_resonance.py`

**Interfaces:**
- Consumes: `data_provider.resample.resample_ohlc`，`data_provider.base.attach_ma_indicators`（Task 1）。
- Produces:
  - `period_trend(df_period) -> str`（`'bullish'|'bearish'|'neutral'`）
  - `resonance_level(signal_direction, weekly_trend, monthly_trend) -> str`（`'none'|'weekly'|'weekly_monthly'`）
  - `resonance_from_daily(df_daily, signal_direction) -> str`
  - 常量 `RESONANCE_NONE/RESONANCE_WEEKLY/RESONANCE_WEEKLY_MONTHLY`

- [ ] **Step 1: 写失败测试** `tests/test_multi_period_resonance.py`

```python
import pandas as pd
import pytest
from src.services.multi_period_resonance import (
    period_trend, resonance_level, resonance_from_daily,
)


def _pf(ma5, ma10, ma20, close):
    return pd.DataFrame([{"ma5": ma5, "ma10": ma10, "ma20": ma20, "close": close}])


def test_period_trend_bullish_needs_arrangement_and_close_confirm():
    assert period_trend(_pf(12, 11, 10, 13)) == "bullish"


def test_period_trend_arrangement_but_close_below_ma20_is_neutral():
    assert period_trend(_pf(12, 11, 10, 9)) == "neutral"


def test_period_trend_bearish():
    assert period_trend(_pf(8, 9, 10, 7)) == "bearish"


def test_period_trend_tangled_and_nan_and_empty_are_neutral():
    assert period_trend(_pf(10, 12, 11, 13)) == "neutral"
    assert period_trend(_pf(float("nan"), 11, 10, 13)) == "neutral"
    assert period_trend(pd.DataFrame()) == "neutral"


@pytest.mark.parametrize("direction,weekly,monthly,expected", [
    ("bullish", "bullish", "bullish", "weekly_monthly"),
    ("bullish", "bullish", "neutral", "weekly"),
    ("bullish", "neutral", "bullish", "none"),    # 周线门控
    ("bullish", "bearish", "bullish", "none"),
    ("bearish", "bearish", "bearish", "weekly_monthly"),
    ("bearish", "bearish", "neutral", "weekly"),
    ("neutral", "bullish", "bullish", "none"),
    (None, "bullish", "bullish", "none"),
])
def test_resonance_level(direction, weekly, monthly, expected):
    assert resonance_level(direction, weekly, monthly) == expected


def test_resonance_from_daily_neutral_skips():
    df = pd.DataFrame({"date": ["2024-01-01"], "open": [1], "high": [1], "low": [1], "close": [1]})
    assert resonance_from_daily(df, "neutral") == "none"


def test_resonance_from_daily_uptrend_is_weekly_monthly():
    dates = pd.date_range("2022-01-03", periods=400, freq="B").strftime("%Y-%m-%d").tolist()
    closes = [100 + i for i in range(400)]
    df = pd.DataFrame({"date": dates, "open": closes, "high": closes, "low": closes,
                       "close": closes, "volume": [1] * 400, "amount": [1] * 400})
    assert resonance_from_daily(df, "bullish") == "weekly_monthly"
    assert resonance_from_daily(df, "bearish") == "none"
```

- [ ] **Step 2: 运行确认失败**

Run: `… -m pytest tests/test_multi_period_resonance.py -q`
Expected: FAIL（模块缺失）

- [ ] **Step 3: 实现 `src/services/multi_period_resonance.py`**

```python
"""多周期共振判定（纯逻辑，无 I/O）。

高周期趋势 = 均线排列（多头/空头）+ 收盘 vs MA20 确认（确认门，无阈值参数）。
共振 = 日线信号方向 × 高周期同向；周线门控、月线加强。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from data_provider.base import attach_ma_indicators
from data_provider.resample import resample_ohlc

RESONANCE_NONE = "none"
RESONANCE_WEEKLY = "weekly"
RESONANCE_WEEKLY_MONTHLY = "weekly_monthly"


def period_trend(df_period: pd.DataFrame) -> str:
    """高周期帧 → 趋势方向（取最后一根 bar）。

    bullish: MA5>MA10>MA20 且 close>=MA20；bearish: MA5<MA10<MA20 且 close<=MA20；
    其余（纠缠/收盘未确认/MA 缺失/历史不足）: neutral。
    """
    if df_period is None or df_period.empty:
        return "neutral"
    last = df_period.iloc[-1]
    ma5, ma10, ma20, close = last.get("ma5"), last.get("ma10"), last.get("ma20"), last.get("close")
    if any(v is None or pd.isna(v) for v in (ma5, ma10, ma20, close)):
        return "neutral"
    if ma5 > ma10 > ma20 and close >= ma20:
        return "bullish"
    if ma5 < ma10 < ma20 and close <= ma20:
        return "bearish"
    return "neutral"


def resonance_level(signal_direction: Optional[str], weekly_trend: str, monthly_trend: str) -> str:
    """日线信号方向 × 高周期趋势 → 共振档位（周线门控、月线加强）。"""
    if signal_direction not in ("bullish", "bearish"):
        return RESONANCE_NONE
    if weekly_trend != signal_direction:
        return RESONANCE_NONE
    if monthly_trend == signal_direction:
        return RESONANCE_WEEKLY_MONTHLY
    return RESONANCE_WEEKLY


def resonance_from_daily(df_daily: pd.DataFrame, signal_direction: Optional[str]) -> str:
    """日线帧 + 信号方向 → 共振档位。非方向性信号直接返回 none（调用方据此跳过深抓）。"""
    if signal_direction not in ("bullish", "bearish"):
        return RESONANCE_NONE
    weekly = period_trend(attach_ma_indicators(resample_ohlc(df_daily, "weekly")))
    monthly = period_trend(attach_ma_indicators(resample_ohlc(df_daily, "monthly")))
    return resonance_level(signal_direction, weekly, monthly)
```

- [ ] **Step 4: 运行确认通过**

Run: `… -m pytest tests/test_multi_period_resonance.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/services/multi_period_resonance.py tests/test_multi_period_resonance.py
git commit -m "feat: 新增多周期共振判定(高周期趋势=均线排列+收盘确认;周线门控月线加强)"
```

---

### Task 3: stock_service 解锁周/月线

**Files:**
- Modify: `src/services/stock_service.py:88-161`（`get_history_data`，新增 `import math`）
- Test: `tests/test_stock_service_multi_period.py`

**Interfaces:**
- Consumes: `data_provider.resample.resample_ohlc`（Task 1）。
- Produces: `get_history_data(stock_code, period, days)` 支持 `period ∈ {daily,weekly,monthly}`，weekly/monthly 返回与 daily **同结构** dict（bar 键 `date/open/high/low/close/volume/amount/change_percent`，无 MA）。

- [ ] **Step 1: 写失败测试** `tests/test_stock_service_multi_period.py`

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `… -m pytest tests/test_stock_service_multi_period.py -q`
Expected: FAIL（weekly 抛 ValueError / warmup 断言不符）

- [ ] **Step 3: 改 `get_history_data`**

在 `stock_service.py` 顶部确保 `import math`。在类外（模块级）加常量与 `_pct` helper：

```python
_HISTORY_WARMUP_DAYS = {"daily": 0, "weekly": 200, "monthly": 800}
_MAX_HISTORY_FETCH_DAYS = 3650
_PERIOD_CAL_DAYS = {"weekly": 7, "monthly": 30}


def _pct(v):
    """涨跌幅取值：保持日线既有口径（0/缺失→None），并对周/月线首根 NaN 归 None。"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return float(v) if v else None
```

把 `get_history_data` 替换为（签名不变）：

```python
    def get_history_data(self, stock_code: str, period: str = "daily", days: int = 30) -> Dict[str, Any]:
        """获取股票历史行情（period ∈ daily/weekly/monthly；周/月线本地聚合自日线）。"""
        if period not in ("daily", "weekly", "monthly"):
            raise ValueError(f"暂不支持 '{period}' 周期，目前支持 daily/weekly/monthly。")
        try:
            from data_provider.base import DataFetcherManager
            manager = DataFetcherManager()
            fetch_days = (days if period == "daily"
                          else min(days + _HISTORY_WARMUP_DAYS[period], _MAX_HISTORY_FETCH_DAYS))
            df, source = manager.get_daily_data(stock_code, days=fetch_days)
            if df is None or df.empty:
                return {"stock_code": stock_code, "period": period, "data": []}
            stock_name = manager.get_stock_name(stock_code)
            if period != "daily":
                from data_provider.resample import resample_ohlc
                df = resample_ohlc(df, period)
                if df.empty:
                    return {"stock_code": stock_code, "stock_name": stock_name,
                            "period": period, "data": []}
                display_n = max(1, math.ceil(days / _PERIOD_CAL_DAYS[period]))
                df = df.tail(display_n)
            data = []
            for _, row in df.iterrows():
                date_val = row.get("date")
                date_str = date_val.strftime("%Y-%m-%d") if hasattr(date_val, "strftime") else str(date_val)
                data.append({
                    "date": date_str,
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "close": float(row.get("close", 0)),
                    "volume": float(row.get("volume", 0)) if row.get("volume") else None,
                    "amount": float(row.get("amount", 0)) if row.get("amount") else None,
                    "change_percent": _pct(row.get("pct_chg")),
                })
            return {"stock_code": stock_code, "stock_name": stock_name, "period": period, "data": data}
        except ImportError:
            logger.warning("DataFetcherManager 未找到，返回空数据")
            return {"stock_code": stock_code, "period": period, "data": []}
        except Exception as e:
            logger.error(f"获取历史数据失败: {e}", exc_info=True)
            return {"stock_code": stock_code, "period": period, "data": []}
```

> 注：daily 分支逻辑等价于原实现（`_pct` 对 daily 非 NaN 值行为与原 `if row.get("pct_chg")` 一致：0→None、缺失→None）。

- [ ] **Step 4: 运行确认通过**

Run: `… -m pytest tests/test_stock_service_multi_period.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/services/stock_service.py tests/test_stock_service_multi_period.py
git commit -m "feat: stock_service 解锁周/月线(本地聚合自日线,daily 路径不变)"
```

---

### Task 4: history 端点 days 上限放宽 + 端到端

**Files:**
- Modify: `api/v1/endpoints/stocks.py:507-512`（`days` Query `le=365` → `le=1825`，更新描述）
- Test: `tests/test_stock_history_multi_period.py`

**Interfaces:**
- Consumes: `StockService.get_history_data`（Task 3）。

- [ ] **Step 1: 写失败测试** `tests/test_stock_history_multi_period.py`（沿用 `tests/test_stock_history_days.py` 的 TestClient + monkeypatch 模式）

```python
from src.services.stock_service import StockService


def _fake_history(self, stock_code, period="daily", days=30):
    return {"stock_code": stock_code, "stock_name": "X", "period": period,
            "data": [{"date": "2024-01-05", "open": 1.0, "high": 1.0, "low": 1.0,
                      "close": 1.0, "volume": 1.0, "amount": 1.0, "change_percent": None}]}


def test_history_weekly_returns_200(client, monkeypatch):
    monkeypatch.setattr(StockService, "get_history_data", _fake_history)
    r = client.get("/api/v1/stocks/600519/history?period=weekly&days=365")
    assert r.status_code == 200
    body = r.json()
    assert body["period"] == "weekly" and len(body["data"]) == 1


def test_history_days_cap_widened_to_1825(client, monkeypatch):
    monkeypatch.setattr(StockService, "get_history_data", _fake_history)
    assert client.get("/api/v1/stocks/600519/history?period=monthly&days=1825").status_code == 200
    assert client.get("/api/v1/stocks/600519/history?period=monthly&days=1826").status_code == 422  # FastAPI 校验
```

> `client` fixture 复用 `tests/test_stock_history_days.py` 的现有写法（若为局部 fixture 则照抄到本文件）。

- [ ] **Step 2: 运行确认失败**

Run: `… -m pytest tests/test_stock_history_multi_period.py -q`
Expected: FAIL（days=1825 当前被 `le=365` 拦为 422）

- [ ] **Step 3: 放宽 `days` 上限**

`api/v1/endpoints/stocks.py` 的 `get_stock_history`：

```python
    days: int = Query(
        120,
        ge=1,
        le=1825,
        description="获取天数（日历回看；日线默认 120；周/月线前端送更大窗口，上限放宽至 1825）",
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `… -m pytest tests/test_stock_history_multi_period.py tests/test_stock_history_days.py -q`
Expected: PASS（含旧 days 测试回归）

- [ ] **Step 5: 提交**

```bash
git add api/v1/endpoints/stocks.py tests/test_stock_history_multi_period.py
git commit -m "feat: history 端点放宽 days 上限至 1825 以支撑周/月线窗口(纯加宽,向后兼容)"
```

---

### Task 5: 后端共振接线（schema + 编排 + /signals）

**Files:**
- Modify: `api/v1/schemas/stocks.py`（`SignalsResponse` + `BoardEntry` 加 `resonance`）
- Modify: `src/services/signal_board_service.py`（`BoardSignals`、`build_signals_for_code`、`_entry_from_board_signals`、`_degraded_entry`）
- Modify: `api/v1/endpoints/stocks.py`（`get_stock_signals` 构造 `SignalsResponse` 时填 `resonance`）
- Test: `tests/test_signal_board_resonance.py`

**Interfaces:**
- Consumes: `multi_period_resonance.resonance_from_daily`（Task 2）；`StockService.get_history_data`（Task 3）。
- Produces: `signals_payload["resonance"]`、`BoardEntry.resonance`、`SignalsResponse.resonance`（值域 `'none'|'weekly'|'weekly_monthly'`，默认 `'none'`）。

- [ ] **Step 1: 加 schema 字段**

`api/v1/schemas/stocks.py` — `SignalsResponse`（143-162）在 `degraded_reason` 后加：

```python
    resonance: Literal["none", "weekly", "weekly_monthly"] = Field(
        "none", description="多周期共振档位（M4-A）：高周期趋势与日线信号同向"
    )
```

`BoardEntry`（165-193）在 `baseline_excess` 后加同样字段：

```python
    resonance: Literal["none", "weekly", "weekly_monthly"] = Field(
        "none", description="多周期共振档位（M4-A）"
    )
```

- [ ] **Step 2: 写失败测试** `tests/test_signal_board_resonance.py`

```python
import types
import pandas as pd
import pytest
from src.services import signal_board_service as sbs


@pytest.fixture(autouse=True)
def _clear_cache():
    sbs._BOARD_CACHE.clear()
    yield
    sbs._BOARD_CACHE.clear()


def _engine_ok():
    return types.SimpleNamespace(markers=[], status="ok", degraded_reason=None)


def _install_common(monkeypatch, rule_direction):
    """把引擎/规则/LLM/价位/payload 都 mock 成确定值，仅留共振路径真实。"""
    monkeypatch.setattr(sbs, "compute_volume_price_signals", lambda df, config=None: _engine_ok())
    monkeypatch.setattr(sbs.StockTrendAnalyzer, "analyze",
                        lambda self, df, code: types.SimpleNamespace(buy_signal=object()))
    monkeypatch.setattr(sbs, "buy_signal_to_direction", lambda s: rule_direction)
    monkeypatch.setattr(sbs.DatabaseManager, "get_instance",
                        lambda: types.SimpleNamespace(get_latest_analysis_by_code=lambda code: None))
    monkeypatch.setattr(sbs, "build_signals_payload",
                        lambda **kw: {"status": "ok", "markers": [], "consistency": "unknown",
                                      "price_lines": {"entry": None, "stop": None, "target": None}})
    # 价位反算 + 端点 helper（延迟 import）
    monkeypatch.setattr("api.v1.endpoints.stocks.build_price_lines",
                        lambda lv: types.SimpleNamespace(model_dump=lambda: {"entry": None, "stop": None, "target": None}))
    monkeypatch.setattr("api.v1.endpoints.stocks._elapsed_trading_days", lambda rec, rows: 0)
    monkeypatch.setattr(sbs, "derive_price_levels", lambda df, **kw: None)


def _spy_history(monkeypatch, *, deep_direction_payload):
    """spy get_history_data：记录 (period, days)，返回少量日线行。"""
    calls = []
    base_rows = [{"date": "2024-01-0%d" % d, "open": 1, "high": 1, "low": 1, "close": 1,
                  "volume": 1, "amount": 1, "change_percent": None} for d in range(1, 6)]

    def fake(self, stock_code, period="daily", days=30):
        calls.append((period, days))
        return {"stock_code": stock_code, "stock_name": "X", "period": period, "data": base_rows}

    monkeypatch.setattr(sbs.StockService, "get_history_data", fake)
    return calls


def test_directional_entry_triggers_gated_deep_fetch_and_resonance(monkeypatch):
    _install_common(monkeypatch, rule_direction="bullish")
    calls = _spy_history(monkeypatch, deep_direction_payload="bullish")
    monkeypatch.setattr(sbs, "resonance_from_daily", lambda df, d: "weekly_monthly")
    bs = sbs.build_signals_for_code("600519", days=120)
    entry = sbs._entry_from_board_signals("600519", bs)
    assert entry["resonance"] == "weekly_monthly"
    # 两次抓取：信号 days=120 + 共振深抓 days=RESONANCE_DAILY_DAYS
    assert ("daily", 120) in calls
    assert ("daily", sbs.RESONANCE_DAILY_DAYS) in calls


def test_hold_entry_skips_resonance_fetch(monkeypatch):
    _install_common(monkeypatch, rule_direction="neutral")
    calls = _spy_history(monkeypatch, deep_direction_payload="x")
    bs = sbs.build_signals_for_code("600519", days=120)
    entry = sbs._entry_from_board_signals("600519", bs)
    assert entry["resonance"] == "none"
    assert [c for c in calls if c[1] == sbs.RESONANCE_DAILY_DAYS] == []   # 未触发深抓


def test_resonance_failure_degrades_to_none(monkeypatch):
    _install_common(monkeypatch, rule_direction="bullish")
    _spy_history(monkeypatch, deep_direction_payload="bullish")

    def boom(df, d):
        raise RuntimeError("resample failed")

    monkeypatch.setattr(sbs, "resonance_from_daily", boom)
    bs = sbs.build_signals_for_code("600519", days=120)
    entry = sbs._entry_from_board_signals("600519", bs)
    assert entry["resonance"] == "none"          # 降级
    assert entry["action_group"] == "buy"        # 其余字段正常
```

- [ ] **Step 3: 运行确认失败**

Run: `… -m pytest tests/test_signal_board_resonance.py -q`
Expected: FAIL（`RESONANCE_DAILY_DAYS`/`resonance_from_daily` 未引入；entry 无 `resonance`）

- [ ] **Step 4: 接线 `signal_board_service.py`**

顶部 import 区加：`from src.services.multi_period_resonance import resonance_from_daily`。模块级常量区加：`RESONANCE_DAILY_DAYS = 750  # 共振深抓日线天数(够月线 MA20 暖机)`。

`BoardSignals` dataclass 加字段：`resonance: str = "none"`。

`build_signals_for_code`：在 `rule_signal` 计算之后、`build_signals_payload` 之前，加门控深抓块；并在算出 `rule_dir` 后复用它：

```python
    rule_dir = buy_signal_to_direction(rule_signal) if rule_signal is not None else "neutral"

    # 多周期共振：仅对方向性(bullish/bearish)做一次独立深抓(门控);hold/中性不抓;失败降级 none。
    resonance = "none"
    if rule_dir in ("bullish", "bearish"):
        try:
            deep = service.get_history_data(stock_code=code, period="daily", days=RESONANCE_DAILY_DAYS)
            deep_rows = deep.get("data", []) or []
            if deep_rows:
                resonance = resonance_from_daily(pd.DataFrame(deep_rows), rule_dir)
        except Exception as exc:
            logger.warning("共振计算失败 code=%s err=%s", code, exc)
            resonance = "none"
```

`build_signals_payload(...)` 之后填 `payload["resonance"] = resonance`。`return BoardSignals(...)` 用 `rule_direction=rule_dir, resonance=resonance`（不再在 return 处重算 rule_direction）。无 rows 的早退分支（68-75）给 `payload["resonance"] = "none"`、`BoardSignals(..., resonance="none")`。

`_entry_from_board_signals`（168-178 的返回 dict）加：`"resonance": payload.get("resonance", "none"),`。

`_degraded_entry`（181+）的返回 dict 加：`"resonance": "none",`。

- [ ] **Step 5: /signals 端点填 resonance**

`api/v1/endpoints/stocks.py` 的 `get_stock_signals` 在构造 `SignalsResponse(...)` 处补 `resonance=payload.get("resonance", "none")`（payload 来自 `build_signals_for_code(...).signals_payload`）。

- [ ] **Step 6: 运行确认通过 + 看板/单股回归**

Run: `… -m pytest tests/test_signal_board_resonance.py tests/test_signal_board_service.py tests/test_signals_endpoint.py -q`
Expected: PASS（确认 M3 看板/单股测试不回归）

- [ ] **Step 7: 提交**

```bash
git add api/v1/schemas/stocks.py src/services/signal_board_service.py api/v1/endpoints/stocks.py tests/test_signal_board_resonance.py
git commit -m "feat: 信号编排算多周期共振并透传 /signals 与 /board(按方向门控深抓,失败降级,schema 追加 resonance)"
```

---

### Task 6: 前端类型 + API 客户端

**Files:**
- Modify: `apps/dsa-web/src/types/kline.ts`
- Modify: `apps/dsa-web/src/api/stocks.ts`
- Test: `apps/dsa-web/src/api/__tests__/stocks.board.test.ts`、`apps/dsa-web/src/api/__tests__/stocks.signals.test.ts`、新增 `apps/dsa-web/src/api/__tests__/stocks.kline.test.ts`

**Interfaces:**
- Produces: `ResonanceLevel`；`SignalsResponse.resonance`、`BoardEntry.resonance`；`getKlineHistory(code, days, period)`。

- [ ] **Step 1: 写失败测试**

在 `stocks.board.test.ts` 加（mock raw entry 带/不带 resonance）：

```ts
it('maps resonance, defaulting to none when absent', async () => {
  // raw entry 含 resonance: 'weekly_monthly' → 映射保留；缺失 → 'none'
  // （沿用本文件既有 mock：apiClient.get 返回 { data: { entries: [...] } }）
  // 断言：result.entries[0].resonance === 'weekly_monthly'；缺失项 === 'none'
});
```

新增 `stocks.kline.test.ts`：

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest';

const get = vi.fn();
vi.mock('../index', () => ({ default: { get } }));

beforeEach(() => { get.mockReset(); get.mockResolvedValue({ data: { data: [] } }); });

describe('getKlineHistory period', () => {
  it('passes period and days to /history', async () => {
    const { stocksApi } = await import('../stocks');
    await stocksApi.getKlineHistory('600519', 365, 'weekly');
    expect(get).toHaveBeenCalledWith(
      '/api/v1/stocks/600519/history',
      { params: { days: 365, period: 'weekly' } },
    );
  });

  it('defaults to daily', async () => {
    const { stocksApi } = await import('../stocks');
    await stocksApi.getKlineHistory('600519');
    expect(get.mock.calls[0][1]).toEqual({ params: { days: 120, period: 'daily' } });
  });
});
```

在 `stocks.signals.test.ts` 加：raw signals 带 `resonance: 'weekly'` → `result.resonance === 'weekly'`；缺失 → `'none'`。

- [ ] **Step 2: 运行确认失败**

Run（`/root/dsa-m4a/apps/dsa-web`）: `npx vitest run src/api/__tests__/stocks.kline.test.ts src/api/__tests__/stocks.board.test.ts src/api/__tests__/stocks.signals.test.ts`
Expected: FAIL

- [ ] **Step 3: 改类型** `types/kline.ts`

`SignalDirection` 附近加：

```ts
export type ResonanceLevel = 'none' | 'weekly' | 'weekly_monthly';
```

`SignalsResponse` 加 `resonance: ResonanceLevel;`；`BoardEntry` 加 `resonance: ResonanceLevel;`。

- [ ] **Step 4: 改 API 客户端** `api/stocks.ts`

`RawSignalsResponse`（44-50）加 `resonance?: ResonanceLevel | null;`；`getSignals` 返回对象（172-182）加 `resonance: data.resonance ?? 'none',`。
`RawBoardEntry`（74-83）加 `resonance?: ResonanceLevel | null;`；`mapBoardEntry`（89-98）加 `resonance: r.resonance ?? 'none',`。
`getKlineHistory`（145）签名与请求：

```ts
  async getKlineHistory(
    code: string,
    days: number = KLINE_DEFAULT_DAYS,
    period: 'daily' | 'weekly' | 'monthly' = 'daily',
  ): Promise<KLine[]> {
    const response = await apiClient.get(
      `/api/v1/stocks/${encodeURIComponent(code)}/history`,
      { params: { days, period } },
    );
    const data = response.data as { data?: KLineDataRaw[] };
    const rows = data.data ?? [];
    return rows.map(mapKLineDataToKLine).sort((a, b) => a.timestamp - b.timestamp);
  },
```

引入 `ResonanceLevel` 类型 import。

- [ ] **Step 5: 运行确认通过**

Run: `npx vitest run src/api/__tests__/stocks.kline.test.ts src/api/__tests__/stocks.board.test.ts src/api/__tests__/stocks.signals.test.ts`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add apps/dsa-web/src/types/kline.ts apps/dsa-web/src/api/stocks.ts apps/dsa-web/src/api/__tests__/
git commit -m "feat: 前端补 resonance 契约(SignalsResponse/BoardEntry)与 getKlineHistory period 入参"
```

---

### Task 7: 共振徽标工具 + 看板行渲染

**Files:**
- Create: `apps/dsa-web/src/utils/resonance.ts`
- Modify: `apps/dsa-web/src/components/board/SignalBoardGroup.tsx`
- Test: `apps/dsa-web/src/utils/__tests__/resonance.test.ts`、`apps/dsa-web/src/components/board/__tests__/SignalBoard.test.tsx`

**Interfaces:**
- Consumes: `ResonanceLevel`、`BoardEntry.resonance`（Task 6）。
- Produces: `resonanceLabel(level) -> string | null`、`resonanceTooltip(level) -> string`。

- [ ] **Step 1: 写失败测试** `utils/__tests__/resonance.test.ts`

```ts
import { describe, it, expect } from 'vitest';
import { resonanceLabel, resonanceTooltip } from '../resonance';

describe('resonance labels', () => {
  it('labels by level', () => {
    expect(resonanceLabel('none')).toBeNull();
    expect(resonanceLabel('weekly')).toBe('共振·周');
    expect(resonanceLabel('weekly_monthly')).toBe('共振·周月');
  });
  it('tooltips are non-empty for active levels', () => {
    expect(resonanceTooltip('weekly').length).toBeGreaterThan(0);
    expect(resonanceTooltip('weekly_monthly').length).toBeGreaterThan(0);
  });
});
```

在 `SignalBoard.test.tsx` 加：渲染含 `resonance: 'weekly_monthly'` 的 entry → 出现 `共振·周月`；`resonance: 'none'` → 无 `data-testid="board-resonance"`。

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/utils/__tests__/resonance.test.ts src/components/board/__tests__/SignalBoard.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现 `utils/resonance.ts`**

```ts
import type { ResonanceLevel } from '../types/kline';

/** 共振徽标文案：none → null（不渲染）；weekly → '共振·周'；weekly_monthly → '共振·周月'。 */
export function resonanceLabel(level: ResonanceLevel): string | null {
  if (level === 'weekly') return '共振·周';
  if (level === 'weekly_monthly') return '共振·周月';
  return null;
}

/** 徽标 tooltip 文案。 */
export function resonanceTooltip(level: ResonanceLevel): string {
  if (level === 'weekly') return '周线趋势与日线信号同向（多周期共振）';
  if (level === 'weekly_monthly') return '周线与月线趋势均与日线信号同向（强共振）';
  return '无多周期共振';
}
```

- [ ] **Step 4: 改 `SignalBoardGroup.tsx`**

import：`import { resonanceLabel, resonanceTooltip } from '../../utils/resonance';`。在「命中率」`<td>`（47-59）内、`verifiedLabel` span 之后追加共振徽标（accent 色块，区别于可信度的文字色）：

```tsx
                {resonanceLabel(e.resonance) && (
                  <span
                    data-testid="board-resonance"
                    title={resonanceTooltip(e.resonance)}
                    className="ml-1 rounded bg-accent/15 px-1 text-accent"
                  >{resonanceLabel(e.resonance)}</span>
                )}
```

- [ ] **Step 5: 运行确认通过**

Run: `npx vitest run src/utils/__tests__/resonance.test.ts src/components/board/__tests__/SignalBoard.test.tsx`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add apps/dsa-web/src/utils/resonance.ts apps/dsa-web/src/utils/__tests__/resonance.test.ts apps/dsa-web/src/components/board/
git commit -m "feat: 看板行渲染多周期共振徽标(accent 色块,区别可信度徽标)"
```

---

### Task 8: 钻取面板共振头部徽标

**Files:**
- Modify: `apps/dsa-web/src/components/kline/SignalDrilldownPanel.tsx`
- Test: `apps/dsa-web/src/components/kline/__tests__/SignalDrilldownPanel.test.tsx`

**Interfaces:**
- Consumes: `ResonanceLevel`、`resonanceLabel`/`resonanceTooltip`（Task 6/7）。
- Produces: `SignalDrilldownPanel` 新增可选 prop `resonance?: ResonanceLevel`（默认 `'none'`）。

- [ ] **Step 1: 写失败测试**（加到 `SignalDrilldownPanel.test.tsx`）

```tsx
it('renders resonance header badge when active', () => {
  render(<SignalDrilldownPanel markers={[]} resonance="weekly" onClose={() => {}} />);
  expect(screen.getByTestId('drilldown-resonance')).toHaveTextContent('共振·周');
});

it('omits resonance badge when none', () => {
  render(<SignalDrilldownPanel markers={[]} resonance="none" onClose={() => {}} />);
  expect(screen.queryByTestId('drilldown-resonance')).toBeNull();
});
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/components/kline/__tests__/SignalDrilldownPanel.test.tsx`
Expected: FAIL（prop 不存在 / badge 缺失）

- [ ] **Step 3: 改 `SignalDrilldownPanel.tsx`**

import：`import type { ResonanceLevel } from '../../types/kline';` 与 `import { resonanceLabel, resonanceTooltip } from '../../utils/resonance';`。

props 接口加 `resonance?: ResonanceLevel;`；组件签名解构 `resonance = 'none'`。头部（85-96）改为：

```tsx
    <div className="flex items-center justify-between">
      <span className="label-uppercase">SIGNAL EVIDENCE</span>
      <div className="flex items-center gap-2">
        {resonanceLabel(resonance) && (
          <span
            data-testid="drilldown-resonance"
            title={resonanceTooltip(resonance)}
            className="rounded bg-accent/15 px-1.5 py-0.5 text-xs text-accent"
          >{resonanceLabel(resonance)}</span>
        )}
        <button
          type="button"
          onClick={onClose}
          className="home-surface-button rounded-lg px-3 py-1 text-xs text-secondary-text"
        >
          关闭依据
        </button>
      </div>
    </div>
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/components/kline/__tests__/SignalDrilldownPanel.test.tsx`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/dsa-web/src/components/kline/SignalDrilldownPanel.tsx apps/dsa-web/src/components/kline/__tests__/SignalDrilldownPanel.test.tsx
git commit -m "feat: 钻取面板头部加多周期共振徽标(可选 resonance prop)"
```

---

### Task 9: KLineChartPanel 周期切换 + 共振接线

**Files:**
- Modify: `apps/dsa-web/src/components/kline/KLineChartPanel.tsx`
- Test: `apps/dsa-web/src/components/kline/__tests__/KLineChartPanel.signals.test.tsx`（加用例）

**Interfaces:**
- Consumes: `getKlineHistory(code, days, period)`、`getSignals(...).resonance`（Task 6）；`SignalDrilldownPanel` 的 `resonance` prop（Task 8）。

- [ ] **Step 1: 写失败测试**（加到 `KLineChartPanel.signals.test.tsx`，沿用其 `vi.doMock` 模式）

```tsx
it('switches period: refetches kline with period, skips signals on non-daily', async () => {
  // 初始 daily：getKlineHistory(code, 120, 'daily') + getSignals 被调用
  // 点击「周」按钮(aria-pressed 切换) → getKlineHistory(code, 365, 'weekly') 被调用，getSignals 不再调用
  // 断言 getKlineHistory 最近一次调用参数含 'weekly'；signal overlay 不再绘制
});

it('passes resonance from signals to drilldown panel', async () => {
  // getSignals mock 返回 resonance:'weekly_monthly' + 一个 marker
  // 触发 glyph onClick → SignalDrilldownPanel 收到 resonance='weekly_monthly'
});
```

> 具体断言用本文件既有的 hoisted `getKlineHistory`/`getSignals` vi.fn 句柄；周期按钮用 `getByRole('button', { name: '周' })`。

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/components/kline/__tests__/KLineChartPanel.signals.test.tsx`
Expected: FAIL（无周期按钮 / period 未透传 / drilldown 无 resonance）

- [ ] **Step 3: 改 `KLineChartPanel.tsx`**

imports 加：`import { cn } from '../../utils/cn';` 与 `import type { ResonanceLevel } from '../../types/kline';`（`SignalMarker` 已 import）。

state 加：

```tsx
  const [period, setPeriod] = useState<'daily' | 'weekly' | 'monthly'>('daily');
  const [resonance, setResonance] = useState<ResonanceLevel>('none');
```

`applySignalsLayer`（155-178）改为：非日视图不取信号、清空标注与共振；日视图照旧并捕获 `resonance`：

```tsx
  const applySignalsLayer = useCallback(() => {
    const chart = chartRef.current;
    if (!chart) return;
    if (period !== 'daily') {            // 信号是日线概念，仅日视图渲染
      setSignalsAvailable(true);
      setResonance('none');
      return;
    }
    stocksApi
      .getSignals(stockCode, days)
      .then((signals) => {
        setResonance(signals.resonance);
        if (signals.status === 'degraded') {
          setSignalsAvailable(false);
          return;
        }
        setSignalsAvailable(true);
        registerSignalGlyphTemplate((markers) => setDrilldownMarkers(markers));
        for (const glyph of buildSignalGlyphs(signals.markers)) {
          drawGlyphOverlay(chart, glyph);
        }
        drawPriceLines(chart, signals.priceLines);
      })
      .catch((error) => {
        console.error('Failed to load signals overlay:', error);
        setSignalsAvailable(false);
      });
  }, [stockCode, days, period]);
```

数据加载 effect（180-218）：K 线按周期取数（周/月送更大窗口），deps 加 `period`：

```tsx
    (async () => {
      try {
        const klineDays = period === 'daily' ? days : period === 'weekly' ? 365 : 1825;
        const klines = await stocksApi.getKlineHistory(stockCode, klineDays, period);
        if (disposed) return;
        if (klines.length === 0) { setState('empty'); return; }
        chartRef.current?.applyNewData(klines as KLine[]);
        setState('ready');
        applySignalsLayer();
      } catch (error) {
        if (disposed) return;
        console.error('KLine history load failed:', error);
        setState('error');
      }
    })();
```

effect 依赖数组：`}, [stockCode, days, period, applySignalsLayer]);`。

顶栏（230-239）左侧加周期切换：

```tsx
      <div className="mb-2 flex items-center justify-between">
        <div className="flex gap-1" role="group" aria-label="K线周期">
          {(['daily', 'weekly', 'monthly'] as const).map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setPeriod(p)}
              aria-pressed={period === p}
              className={cn('rounded-lg px-3 py-1.5 text-xs',
                period === p ? 'bg-accent/20 text-accent' : 'text-secondary-text')}
            >
              {p === 'daily' ? '日' : p === 'weekly' ? '周' : '月'}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={toggleColors}
          aria-label="切换涨跌颜色"
          className="home-surface-button rounded-lg px-3 py-1.5 text-xs text-secondary-text"
        >
          {upRedDownGreen ? '红涨绿跌' : '绿涨红跌'}
        </button>
      </div>
```

降级提示横幅（257-264）改为仅日视图渲染：`{period === 'daily' && !signalsAvailable && (` …。

钻取面板（265-269）传入 resonance：

```tsx
        {drilldownMarkers && (
          <div className="absolute right-2 top-2 z-10 w-72 max-w-[80%]">
            <SignalDrilldownPanel
              markers={drilldownMarkers}
              resonance={resonance}
              onClose={() => setDrilldownMarkers(null)}
            />
          </div>
        )}
```

- [ ] **Step 4: 运行确认通过 + 既有图表测试回归**

Run: `npx vitest run src/components/kline/__tests__/KLineChartPanel.signals.test.tsx src/components/kline/__tests__/KLineChartPanel.test.tsx`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/dsa-web/src/components/kline/KLineChartPanel.tsx apps/dsa-web/src/components/kline/__tests__/KLineChartPanel.signals.test.tsx
git commit -m "feat: K线面板加日/周/月周期切换(信号仅日视图)并透传共振到钻取面板"
```

---

### Task 10: 文档（CHANGELOG + 专题）

**Files:**
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平追加）
- Create: `docs/multi-period-resonance.md`（多周期 + 共振专题）
- Test: 无（docs only）

- [ ] **Step 1: CHANGELOG `[Unreleased]` 扁平追加**（每条独立一行，`- [类型] 描述`，**禁止新增 `###` 标题**）

```markdown
- [新功能] 个股 K 线支持日/周/月周期切换（周/月线由日线本地聚合，市场无关）
- [新功能] 日线信号新增多周期共振标记（高周期趋势与信号方向同向：周线共振/周月双共振），看板行与钻取面板可见
- [改进] /stocks/{code}/history 放宽 days 上限至 1825 以支撑周/月线窗口（纯加宽，向后兼容）
```

- [ ] **Step 2: 写专题文档** `docs/multi-period-resonance.md`

覆盖：周/月线本地聚合规则（OHLCV 聚合口径、date=该周期最后交易日、pct_chg 重算）、**日历分组近似**（pandas W/M，非交易所官方 K 线，存在边界差异）、共振判定（均线排列+收盘确认、周线门控月线加强、三档）、共振按方向门控的独立深抓（仅 buy/sell、失败降级 none、M3 信号路径零改动）、信号标记仅日视图、**v1 已知局限**（月线在 days 上限内根数有限、共振只在最新 bar、周/月线无自身回测/胜率）。

- [ ] **Step 3: 核对命令/文件名/字段名与实仓一致**（无需跑测试）

- [ ] **Step 4: 提交**

```bash
git add docs/CHANGELOG.md docs/multi-period-resonance.md
git commit -m "docs: 多周期+共振 CHANGELOG 与专题文档(含日历分组近似/v1 局限)"
```

---

## 全量门禁（全部任务完成后，交付前亲自跑）

- 后端：`./scripts/ci_gate.sh`（flake8 + `pytest -m "not network"`）
- 前端（`/root/dsa-m4a/apps/dsa-web`）：`npm ci && npm run lint && npm run build` + `npx vitest run`
- 交付说明：改了什么 / 为什么 / 验证情况 / 未验证项 / 风险点 / 回滚方式

## Self-Review（计划自审结论）

- **Spec 覆盖**：周/月聚合(T1/T3)、共振判定(T2)、按方向门控深抓+透传(T5)、days 放宽(T4)、前端切换(T9)+徽标(T7/T8)+契约(T6)、文档+局限(T10)、防未来函数（partial bar/日历近似/历史不足→neutral，体现在 T1/T2 实现与测试）——逐条有对应任务。
- **类型一致**：共振枚举全程 `'none'|'weekly'|'weekly_monthly'`；趋势 `'bullish'|'bearish'|'neutral'`；`resonance_from_daily`/`period_trend`/`resonance_level` 签名跨任务一致；前端 `ResonanceLevel` 与后端 Literal 对齐。
- **占位符**：无 TBD；每个改码步骤含真实代码与真实命令。
- **稳定性**：daily 与 M3 信号抓取路径零改动（T3/T5 显式回归断言）；共振失败/历史不足/抓取失败一律降级 `none`、不抛错。
