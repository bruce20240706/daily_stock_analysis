# 信号引擎走查向量化(O(n²)→O(n log k)) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `signal_backtest.py::_eval` 的逐窗 O(n²) 信号走查重构为单遍 O(n log k),使链路B 分钟回测可用,同时图表/看板几何字节级不变。

**Architecture:** 方案 C 分层。图表路径(`compute_volume_price_signals` 全 df 单调用)及其全部传递被调函数**零改动**;回测路径新增 `compute_signals_for_all_bars`(因果修正语义,单遍)+ `derive_price_levels_series`,`_eval` 改为 O(1) 命中预计算。所有因果改写一律**新增并列函数**,绝不就地改原检测器。golden 双轨:图表既有套件 + 近边界 viz 回归;回测独立因果 oracle(绕过 `_limit_b_class`)。

**Tech Stack:** Python 3 / pandas / numpy / pytest;venv = `/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin/python`(cwd 须在工作树)。

## Global Constraints

- **不得修改** `compute_volume_price_signals` 及其全部传递被调函数(`_detect_obv_divergence`/`_detect_breakouts`/`_detect_shrink_pullback`/`_anchored_vwap_signals`/`_detect_vsa_bars`/`_detect_upthrust_spring`/`_detect_latest_vfx`/`_limit_b_class`/`find_swing_pivots`/`atr`/`_compute_primitives`/`derive_price_levels`/`_classify_vfx`)的**输出**;`normalize_ohlcv` 仅允许追加式可选参 `keep_original_index`(默认 False,行为不变)。因果/快速变体全部新增并列函数。
- **因果性**:回测 bar t 只能用 ≤t 数据;pivot 仅当 `center + swing_k ≤ t` 可用。
- **零新增业务配置项**(`keep_original_index`/`now` 是函数级参,非环境配置)。
- commit message 用英文 type 前缀 + 中文正文,**不加 `Co-Authored-By`**,不加工具/agent 前缀。
- 未经明确确认不执行 `git commit`/`push`/`tag`(本 plan 的 commit 步骤须在用户确认提交策略后执行;默认每任务一 commit 到 feature 分支)。
- B 类 signal_type 集合(用于 oracle 过滤与 block 判定):`{"vsa_no_demand","vsa_no_supply","vsa_stopping","vsa_effort_vs_result","upthrust","spring"}`。
- D4 排序键(已证与 `_limit_b_class` 等价):`(-abs(observed_value), block, bar)`,block: VSA=0 / upthrust&spring=1。
- 退化三态:`_normalize` 失败→全空;窗口不足→**per-bar warmup gate 落 norm 行号**(`(j+1) < max(vol_ma_window,atr_period,breakout_window)+1` 的 norm 行 j 不归位);`rel_vol` 全 NaN→仍产 marker。

---

## File Structure

- `src/services/alert_indicators.py` — **Modify**:`normalize_ohlcv` 加 `keep_original_index` 可选参。
- `src/services/volume_price_signals.py` — **新增并列函数**(文件尾部):`_scalar_or_none`、`_price_levels_scalar_core`、`derive_price_levels_series`、`_detect_vsa_bars_rows`、`_detect_upthrust_spring_causal_rows`、`_detect_shrink_pullback_causal_rows`、`_detect_vfx_all_bars_rows`、`_detect_breakouts_rows`、`_anchored_vwap_signals_rows`、`_detect_obv_divergence_rows`、`_streaming_topk_kept`、`compute_signals_for_all_bars`。仅 `derive_price_levels` 内部改为委托 `_price_levels_scalar_core`(输出不变,既有测试守护)。
- `src/services/signal_backtest.py` — **Modify**:`_eval` 改用预计算;import 新函数。
- `tests/test_signal_eval_vectorization.py` — **Create**:因果 oracle + F1–F12 + 多市场 golden + 性能。
- `tests/test_volume_price_signals.py` — **Modify**:追加近边界 viz 回归(路径 1 不变)。
- `tests/test_alert_indicators*.py` 或新增 — **Modify/Create**:`keep_original_index` 回归。
- `docs/signal-credibility.md` / `docs/CHANGELOG.md` — **Modify**:§5.2 行为变更。

约定:所有 `_*_rows` 变体返回 `list[tuple[int, VPSignal]]`(norm 行号, marker),供 `compute_signals_for_all_bars` 按行号归位(规避 B2 时间戳歧义)。

---

## Task 1: `normalize_ohlcv` 追加 `keep_original_index`(解 norm2raw)

**Files:**
- Modify: `src/services/alert_indicators.py:178-210`
- Test: `tests/test_normalize_keep_index.py`(Create)

**Interfaces:**
- Produces: `normalize_ohlcv(df, *, required_columns, now=None, keep_original_index=False) -> pd.DataFrame`;当 `keep_original_index=True` 时返回的 df 多一列 `_orig_idx`(int,= 归一化前原始行号,经 dropna/partial/sort 后随行保留)。默认 False 时行为与现状**字节一致**。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_normalize_keep_index.py
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
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_normalize_keep_index.py -v`
Expected: FAIL（`keep_original_index` 不接受 / `_orig_idx` 缺失）

- [ ] **Step 3: 实现(追加可选参)**

把 `src/services/alert_indicators.py:178` 的签名与第 209 行改为:

```python
def normalize_ohlcv(
    df: Any,
    *,
    required_columns: tuple[str, ...],
    now: Optional[datetime] = None,
    keep_original_index: bool = False,
) -> pd.DataFrame:
    if df is None or getattr(df, "empty", True):
        return pd.DataFrame()
    if not isinstance(df, pd.DataFrame):
        return pd.DataFrame()

    output = pd.DataFrame(index=df.index.copy())
    output["date"] = _date_series(df)
    if keep_original_index:
        # 归一化前的原始行号(_eval 中 df 已 reset_index → RangeIndex 0..n-1 = raw bar 位)
        output["_orig_idx"] = pd.RangeIndex(len(df))

    missing = []
    for canonical in required_columns:
        source = _find_column(df, canonical)
        if source is None:
            missing.append(canonical)
            continue
        output[canonical] = pd.to_numeric(df[source], errors="coerce")
    if missing:
        missing_text = ", ".join(missing)
        raise ValueError(f"daily data missing {missing_text} column")

    output = output.dropna(subset=list(required_columns)).copy()
    if output.empty:
        return output
    output = _drop_partial_today(output, now=now)
    if output.empty:
        return output.reset_index(drop=True)
    output = output.sort_values(by="date", kind="stable", na_position="first").reset_index(drop=True)
    return output
```

> 注:`_orig_idx` 用 `pd.RangeIndex(len(df))` 显式落成数据列,随 dropna/partial/sort 迁移、不被最终 `reset_index(drop=True)` 销毁。dropna 的 `subset` 仍只列 OHLCV,`_orig_idx` 不会被当作必填列。

- [ ] **Step 4: 运行确认通过 + 既有 alert 回归**

Run: `.venv/bin/python -m pytest tests/test_normalize_keep_index.py -v`
Expected: PASS
Run: `.venv/bin/python -m pytest tests/ -k "alert or normalize" -q`
Expected: PASS（默认 `keep_original_index=False`,既有消费方零影响）

- [ ] **Step 5: Commit**

```bash
git add src/services/alert_indicators.py tests/test_normalize_keep_index.py
git commit -m "feat(signals): normalize_ohlcv 追加可选 keep_original_index 以支持 norm2raw 行号映射(默认关、行为不变)"
```

---

## Task 2: 价位序列 `derive_price_levels_series`(raw 空间,scalar core)

**Files:**
- Modify: `src/services/volume_price_signals.py`(新增 `_scalar_or_none`/`_price_levels_scalar_core`/`derive_price_levels_series`;`derive_price_levels` 改委托)
- Test: `tests/test_signal_eval_vectorization.py`(Create,本任务先放价位部分)

**Interfaces:**
- Produces:
  - `derive_price_levels_series(df, *, atr_mult=1.5, rr_target=2.0) -> list[PriceLevels]`(长度 == len(df),raw 行对齐)
  - `_price_levels_scalar_core(ma20, swing_low, current_price, last_atr, *, atr_mult, rr_target) -> PriceLevels`
  - `_scalar_or_none(value) -> float | None`

- [ ] **Step 1: 写失败测试(序列版逐 bar == 逐窗 derive_price_levels)**

```python
# tests/test_signal_eval_vectorization.py
import numpy as np
import pandas as pd
import pytest
from src.services.volume_price_signals import derive_price_levels, derive_price_levels_series

def _synthetic_df(n, seed):
    rng = np.random.default_rng(seed)
    close = 50 + np.cumsum(rng.normal(0, 0.5, n))
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    open_ = close + rng.normal(0, 0.3, n)
    vol = rng.uniform(1e3, 5e3, n)
    dates = pd.date_range("2020-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
    return pd.DataFrame({"date": dates, "open": open_, "high": high, "low": low,
                         "close": close, "volume": vol})

def test_price_levels_series_matches_per_window():
    df = _synthetic_df(80, seed=1)
    series = derive_price_levels_series(df)
    assert len(series) == len(df)
    for t in range(len(df)):
        ref = derive_price_levels(df.iloc[: t + 1])
        got = series[t]
        assert got.entry == pytest.approx(ref.entry) if ref.entry is not None else got.entry == ref.entry
        assert got.stop == pytest.approx(ref.stop) if ref.stop is not None else got.stop == ref.stop
        assert got.target == pytest.approx(ref.target) if ref.target is not None else got.target == ref.target
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py::test_price_levels_series_matches_per_window -v`
Expected: FAIL（`derive_price_levels_series` 未定义）

- [ ] **Step 3: 实现 scalar core + 序列版,并把 derive_price_levels 改委托**

在 `volume_price_signals.py` 文件尾部新增:

```python
def _scalar_or_none(value) -> float | None:
    """单值取 float;NaN/None/不可转 → None(镜像 _last_finite 的标量语义)。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def _price_levels_scalar_core(
    ma20: float | None,
    swing_low: float | None,
    current_price: float | None,
    last_atr: float | None,
    *,
    atr_mult: float,
    rr_target: float,
) -> PriceLevels:
    """derive_price_levels 的标量内核(逐 bar 复用);entry/回退分支与原实现字节一致。"""
    entry_candidates = [c for c in (ma20, swing_low) if c is not None]
    if current_price is not None:
        below = [c for c in entry_candidates if c <= current_price]
        entry = max(below) if below else (min(entry_candidates) if entry_candidates else None)
    else:
        entry = max(entry_candidates) if entry_candidates else None

    if entry is None or last_atr is None or last_atr <= 0:
        return PriceLevels(entry=entry, stop=None, target=None, risk_reward=None)
    stop = entry - atr_mult * last_atr
    risk = entry - stop
    if risk <= 0:
        return PriceLevels(entry=entry, stop=None, target=None, risk_reward=None)
    target = entry + rr_target * risk
    risk_reward = (target - entry) / risk
    candidate = PriceLevels(entry=entry, stop=stop, target=target, risk_reward=risk_reward)
    if is_invalid_price_level(entry=candidate.entry, stop=candidate.stop,
                              target=candidate.target, current_price=current_price):
        return _fallback_atr_levels(current_price, last_atr, atr_mult=atr_mult, rr_target=rr_target)
    return candidate


def derive_price_levels_series(
    df,
    *,
    atr_mult: float = _DEFAULT_ATR_MULT,
    rr_target: float = _DEFAULT_RR_TARGET,
) -> list[PriceLevels]:
    """derive_price_levels 的全 df 序列版(raw 空间,逐 bar O(1) 取值)。

    与 derive_price_levels(df.iloc[:t+1]) 逐 bar 等价:rolling/atr 因果且位置稳定,
    series.iloc[t] == _last_finite(window[:t+1] 对应序列)。硬编码 20/14,不接 config。
    """
    n = len(df) if df is not None else 0
    if df is None or getattr(df, "empty", True) or "close" not in df.columns:
        return [PriceLevels(entry=None, stop=None, target=None, risk_reward=None) for _ in range(n)]
    close = df["close"].astype(float).reset_index(drop=True)
    ma20_series = close.rolling(_PRICE_LEVEL_WINDOW).mean()
    swing_series = (
        df["low"].astype(float).reset_index(drop=True).rolling(_PRICE_LEVEL_WINDOW).min()
        if "low" in df.columns else None
    )
    atr_arr = atr(df).reset_index(drop=True)   # 默认 period=14,与 config 解耦
    out: list[PriceLevels] = []
    for t in range(n):
        current_price = _scalar_or_none(close.iloc[t])
        ma20 = _scalar_or_none(ma20_series.iloc[t])
        swing_low = None
        if swing_series is not None and (t + 1) >= _PRICE_LEVEL_WINDOW:
            swing_low = _scalar_or_none(swing_series.iloc[t])
        last_atr = _scalar_or_none(atr_arr.iloc[t])
        out.append(_price_levels_scalar_core(
            ma20, swing_low, current_price, last_atr, atr_mult=atr_mult, rr_target=rr_target))
    return out
```

然后把 `derive_price_levels`(`volume_price_signals.py:386-407`)的 entry 之后逻辑改为委托 core(保持输出不变):将 `if entry is None or last_atr is None ...` 到 `return candidate` 整段替换为:

```python
    return _price_levels_scalar_core(
        ma20, swing_low, current_price, last_atr, atr_mult=atr_mult, rr_target=rr_target
    )
```

(其中 `derive_price_levels` 上文已算出 `ma20`/`swing_low`/`current_price`/`last_atr`;`last_atr = _last_finite(atr(df))` 保留。)

- [ ] **Step 4: 运行确认通过 + 既有价位测试不回归**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py::test_price_levels_series_matches_per_window tests/test_volume_price_levels.py -v`
Expected: PASS（既有 8 个 price-level 测试证明 derive_price_levels 委托后字节不变）

- [ ] **Step 5: Commit**

```bash
git add src/services/volume_price_signals.py tests/test_signal_eval_vectorization.py
git commit -m "feat(signals): 新增 derive_price_levels_series 序列版价位 + 抽 scalar core(derive_price_levels 委托、输出不变)"
```

---

## Task 3: `_detect_vsa_bars_rows`(hoist astype,行号标注,输出等价)

**Files:**
- Modify: `src/services/volume_price_signals.py`(新增)
- Test: `tests/test_signal_eval_vectorization.py`

**Interfaces:**
- Produces: `_detect_vsa_bars_rows(prim) -> list[tuple[int, VPSignal]]`,marker 与 `_detect_vsa_bars(prim, config)` **逐条等价**(同序),tuple[0] = norm 行号 i。
- Consumes: 无(纯函数,prim 由 `_compute_primitives` 产出)。

- [ ] **Step 1: 写失败测试(与原 _detect_vsa_bars 等价)**

```python
def test_vsa_rows_equiv_original():
    from src.services.volume_price_signals import (
        VPSConfig, _compute_primitives, _normalize, _detect_vsa_bars, _detect_vsa_bars_rows)
    df = _synthetic_df(120, seed=7)
    cfg = VPSConfig()
    norm, _ = _normalize(df, cfg)
    prim = _compute_primitives(norm, cfg)
    ref = _detect_vsa_bars(prim, cfg)
    got = _detect_vsa_bars_rows(prim, cfg)
    assert [s for _, s in got] == ref
    # 行号正确:marker.timestamp 来自该行 date
    for i, s in got:
        from src.services.volume_price_signals import _to_epoch_ms_shanghai
        assert s.timestamp == _to_epoch_ms_shanghai(prim["date"].iloc[i])
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py::test_vsa_rows_equiv_original -v`
Expected: FAIL（`_detect_vsa_bars_rows` 未定义）

- [ ] **Step 3: 实现(hoist `astype(float)` 出循环 + 行号)**

```python
def _detect_vsa_bars_rows(prim: pd.DataFrame, config: VPSConfig) -> list[tuple[int, VPSignal]]:
    """_detect_vsa_bars 的快速变体:hoist close.astype 出循环(消除 O(n²)),返回 (行号, marker)。
    与 _detect_vsa_bars 逐条等价。"""
    close = prim["close"].astype(float).reset_index(drop=True)
    rv_s = prim["rel_vol"].reset_index(drop=True)
    rp_s = prim["range_pos"].reset_index(drop=True)
    body_s = prim["body"].reset_index(drop=True)
    spread_s = prim["spread"].reset_index(drop=True)
    limit_s = prim["is_limit_bar"].reset_index(drop=True)
    date_s = prim["date"].reset_index(drop=True)
    out: list[tuple[int, VPSignal]] = []
    for i in range(len(prim)):
        rv = rv_s.iloc[i]; rp = rp_s.iloc[i]; body = body_s.iloc[i]
        if pd.isna(rv) or bool(limit_s.iloc[i]):
            continue
        ts = _to_epoch_ms_shanghai(date_s.iloc[i])
        price = float(close.iloc[i])
        if rv < config.vol_shrink and body > 0 and not pd.isna(rp) and rp < 0.5:
            out.append((i, _vsa_signal(ts, price, "vsa_no_demand", "bearish", rv)))
        elif rv < config.vol_shrink and body < 0 and not pd.isna(rp) and rp > 0.5:
            out.append((i, _vsa_signal(ts, price, "vsa_no_supply", "bullish", rv)))
        elif rv >= config.vol_high and not pd.isna(rp) and 0.3 <= rp <= 0.7:
            out.append((i, _vsa_signal(ts, price, "vsa_stopping", "neutral", rv)))
        elif rv >= config.vol_high and abs(body) < (spread_s.iloc[i] * 0.2):
            out.append((i, _vsa_signal(ts, price, "vsa_effort_vs_result", "neutral", rv)))
    return out
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py::test_vsa_rows_equiv_original -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/volume_price_signals.py tests/test_signal_eval_vectorization.py
git commit -m "feat(signals): 新增 _detect_vsa_bars_rows(hoist astype 消 O(n²)、行号标注,与原输出等价)"
```

---

## Task 4: `_detect_upthrust_spring_causal_rows`(center+k≤i 门 + 单调指针)

**Files:**
- Modify: `src/services/volume_price_signals.py`(新增)
- Test: `tests/test_signal_eval_vectorization.py`

**Interfaces:**
- Produces: `_detect_upthrust_spring_causal_rows(prim, config) -> list[tuple[int, VPSignal]]`,每根 i 用确认索引 `center+swing_k ≤ i` 的最近 pivot;= 原 `_detect_upthrust_spring(prim[:i+1])` 末根 marker。返回 upthrust(bearish)与 spring(bullish)两方向 RAW marker。

- [ ] **Step 1: 写失败测试(== 原检测器逐窗末根 + 近边界 F1)**

```python
def test_upthrust_spring_causal_equiv_per_window_lastbar():
    from src.services.volume_price_signals import (
        VPSConfig, _compute_primitives, _normalize, _detect_upthrust_spring,
        _detect_upthrust_spring_causal_rows, _to_epoch_ms_shanghai)
    df = _synthetic_df(150, seed=3)
    cfg = VPSConfig()
    norm, _ = _normalize(df, cfg)
    prim = _compute_primitives(norm, cfg)
    causal = _detect_upthrust_spring_causal_rows(prim, cfg)
    # 参照:逐窗 [0:i+1] 末根
    ref: list[tuple[int, str]] = []
    for i in range(len(prim)):
        sub = prim.iloc[: i + 1]
        last_ts = _to_epoch_ms_shanghai(sub["date"].iloc[-1])
        for m in _detect_upthrust_spring(sub, cfg):
            if m.timestamp == last_ts:
                ref.append((i, m.signal_type))
    assert [(i, s.signal_type) for i, s in causal] == ref

def test_upthrust_spring_causal_excludes_unconfirmed_pivot():
    # 构造一个 center 落在 (i-k, i] 的 pivot:因果变体不得在 bar i 用它(F1)
    from src.services.volume_price_signals import (
        VPSConfig, _compute_primitives, _normalize, _detect_upthrust_spring_causal_rows)
    cfg = VPSConfig(swing_k=2)
    # 24 根:制造一个低点 pivot center=20(confirm@22),并在 bar 21 试图触发 spring
    close = [10.0] * 24
    low = [10.0] * 24
    low[20] = 5.0; close[20] = 9.0          # 低点
    low[21] = 4.0; close[21] = 9.5          # bar21 跌破前低收回 → 若用未确认 pivot20 会误产 spring
    df = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=24, freq="D").strftime("%Y-%m-%d"),
                       "open": close, "high": [c + 0.5 for c in close], "low": low,
                       "close": close, "volume": [100.0] * 24})
    norm, _ = _normalize(df, cfg)
    prim = _compute_primitives(norm, cfg)
    causal = _detect_upthrust_spring_causal_rows(prim, cfg)
    # bar21: pivot20 的确认索引 = 20+2 = 22 > 21 → 不可用 → bar21 无 spring
    assert not any(i == 21 and s.signal_type == "spring" for i, s in causal)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py -k upthrust_spring_causal -v`
Expected: FAIL（未定义）

- [ ] **Step 3: 实现(单调指针 + center+k≤i)**

```python
def _detect_upthrust_spring_causal_rows(
    prim: pd.DataFrame, config: VPSConfig
) -> list[tuple[int, VPSignal]]:
    """upthrust/spring 因果变体:对每根 i,仅用确认索引 center+swing_k ≤ i 的最近 pivot。
    单调指针沿 pivot(已按 center 升序)推进,O(n+p)。等价于原 _detect_upthrust_spring(prim[:i+1]) 末根。"""
    high = prim["high"].astype(float).reset_index(drop=True)
    low = prim["low"].astype(float).reset_index(drop=True)
    close = prim["close"].astype(float).reset_index(drop=True)
    date_s = prim["date"].reset_index(drop=True)
    k = config.swing_k
    pivots = _attach_pivot_timestamps(find_swing_pivots(close, k), prim)
    highs = [p for p in pivots if p.kind == "high"]   # center 升序
    lows = [p for p in pivots if p.kind == "low"]
    out: list[tuple[int, VPSignal]] = []
    hi_ptr = 0; last_high = None
    lo_ptr = 0; last_low = None
    for i in range(len(prim)):
        while hi_ptr < len(highs) and highs[hi_ptr].index + k <= i:
            last_high = highs[hi_ptr]; hi_ptr += 1
        while lo_ptr < len(lows) and lows[lo_ptr].index + k <= i:
            last_low = lows[lo_ptr]; lo_ptr += 1
        ts = _to_epoch_ms_shanghai(date_s.iloc[i])
        if last_high is not None and high.iloc[i] > last_high.price and close.iloc[i] < last_high.price:
            out.append((i, VPSignal(
                timestamp=ts, price=float(close.iloc[i]), anchor="high", direction="bearish",
                signal_type="upthrust", confidence="low", is_daily_approx=True, is_anomalous=False,
                reason="假突破顶（Upthrust，日线近似）", threshold=float(last_high.price),
                observed_value=float(high.iloc[i]))))
        if last_low is not None and low.iloc[i] < last_low.price and close.iloc[i] > last_low.price:
            out.append((i, VPSignal(
                timestamp=ts, price=float(close.iloc[i]), anchor="low", direction="bullish",
                signal_type="spring", confidence="low", is_daily_approx=True, is_anomalous=False,
                reason="假跌破底（Spring，日线近似）", threshold=float(last_low.price),
                observed_value=float(low.iloc[i]))))
    return out
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py -k upthrust_spring_causal -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/volume_price_signals.py tests/test_signal_eval_vectorization.py
git commit -m "feat(signals): 新增 _detect_upthrust_spring_causal_rows(center+k≤i 门+单调指针,因果且 O(n+p))"
```

---

## Task 5: `_detect_shrink_pullback_causal_rows`(last-bar→per-bar + O(1) 段增量)

**Files:**
- Modify: `src/services/volume_price_signals.py`(新增)
- Test: `tests/test_signal_eval_vectorization.py`

**Interfaces:**
- Produces: `_detect_shrink_pullback_causal_rows(prim, config) -> list[tuple[int, VPSignal]]`,每根 i = 原 `_detect_shrink_pullback(prim[:i+1])` 末根。

- [ ] **Step 1: 写失败测试(== 原检测器逐窗末根)**

```python
def test_shrink_pullback_causal_equiv_per_window_lastbar():
    from src.services.volume_price_signals import (
        VPSConfig, _compute_primitives, _normalize, _detect_shrink_pullback,
        _detect_shrink_pullback_causal_rows, _to_epoch_ms_shanghai)
    # 上升趋势 + 缩量回调,确保有 shrink_pullback 触发
    n = 120
    close = list(50 + np.linspace(0, 20, n))
    for j in range(60, 70):  # 一段缩量回调
        close[j] = close[59] - (j - 59) * 0.2
    df = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=n, freq="D").strftime("%Y-%m-%d"),
                       "open": close, "high": [c + 0.3 for c in close], "low": [c - 0.3 for c in close],
                       "close": close, "volume": [3000.0] * 60 + [500.0] * 10 + [3000.0] * (n - 70)})
    cfg = VPSConfig()
    norm, _ = _normalize(df, cfg)
    prim = _compute_primitives(norm, cfg)
    causal = _detect_shrink_pullback_causal_rows(prim, cfg)
    ref: list[tuple[int, float]] = []
    for i in range(len(prim)):
        sub = prim.iloc[: i + 1]
        last_ts = _to_epoch_ms_shanghai(sub["date"].iloc[-1])
        for m in _detect_shrink_pullback(sub, cfg):
            if m.timestamp == last_ts:
                ref.append((i, round(m.observed_value, 6)))
    assert [(i, round(s.observed_value, 6)) for i, s in causal] == ref
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py::test_shrink_pullback_causal_equiv_per_window_lastbar -v`
Expected: FAIL

- [ ] **Step 3: 实现(逐根:确认 high 锚 + 段全低于阈值,复刻原逐窗末根语义)**

> 实现策略:对每根 i 复刻原 `_detect_shrink_pullback` 在窗 [0:i+1] 末根的判定。原逻辑用 `highs[-1]`(窗内最近确认 high = center+k≤i 的最近 high,用 Task 4 同款指针)+ `seg_rel = rel_vol[last_high.index+1 : i+1].dropna()` 全 `< pullback_rel_vol`。段检查用前缀增量 O(1):维护 `last_violation_idx`(最近一个 `rel_vol >= pullback_rel_vol` 且非 NaN 的行号)与 `nonnan_count`(累计非 NaN 个数),段非空且无违例 ⇔ `(last_violation_idx < anchor+1) and (该段非 NaN 数 > 0)`。

```python
def _detect_shrink_pullback_causal_rows(
    prim: pd.DataFrame, config: VPSConfig
) -> list[tuple[int, VPSignal]]:
    close = prim["close"].astype(float).reset_index(drop=True)
    ma5 = prim["ma5"].reset_index(drop=True)
    ma20 = prim["ma20"].reset_index(drop=True)
    rel_vol = prim["rel_vol"].reset_index(drop=True)
    pct = prim["pct_chg"].reset_index(drop=True)
    date_s = prim["date"].reset_index(drop=True)
    atr_series = atr(prim, config.atr_period).reset_index(drop=True)
    k = config.swing_k
    pivots = _attach_pivot_timestamps(find_swing_pivots(close, k), prim)
    highs = [p for p in pivots if p.kind == "high"]
    out: list[tuple[int, VPSignal]] = []
    hi_ptr = 0; last_high = None
    # 前缀:nonnan 累计计数 + 最近违例行号
    nonnan_prefix = [0] * (len(prim) + 1)
    last_violation_idx = -1
    last_violation_prefix = [-1] * len(prim)
    for j in range(len(prim)):
        rv = rel_vol.iloc[j]
        nonnan_prefix[j + 1] = nonnan_prefix[j] + (0 if pd.isna(rv) else 1)
        if (not pd.isna(rv)) and rv >= config.pullback_rel_vol:
            last_violation_idx = j
        last_violation_prefix[j] = last_violation_idx
    for i in range(len(prim)):
        while hi_ptr < len(highs) and highs[hi_ptr].index + k <= i:
            last_high = highs[hi_ptr]; hi_ptr += 1
        if last_high is None:
            continue
        if pd.isna(ma5.iloc[i]) or pd.isna(ma20.iloc[i]) or ma5.iloc[i] <= ma20.iloc[i]:
            continue
        drawdown = last_high.price - float(close.iloc[i])
        anchor = last_high.index + 1          # 段 [anchor, i]
        if anchor > i:
            continue
        seg_nonnan = nonnan_prefix[i + 1] - nonnan_prefix[anchor]
        seg_ok = (seg_nonnan > 0) and (last_violation_prefix[i] < anchor)
        atr_now = float(atr_series.iloc[i])
        if (drawdown > 0 and seg_ok and not np.isnan(atr_now)
                and drawdown < config.pullback_atr_mult * atr_now):
            curr_rv = float(rel_vol.iloc[i]) if not pd.isna(rel_vol.iloc[i]) else float("nan")
            curr_pct = float(pct.iloc[i]) if not pd.isna(pct.iloc[i]) else 0.0
            close_now = float(close.iloc[i])
            atr_norm_now = (atr_now / close_now) if close_now > 0 else 0.0
            if not math.isnan(curr_rv):
                vp = classify_volume_pattern(curr_rv, curr_pct, atr_norm_now, config)
                reason_str = f"上升趋势缩量回调（回撤<{config.pullback_atr_mult}*ATR）[量能形态:{vp}]"
            else:
                reason_str = f"上升趋势缩量回调（回撤<{config.pullback_atr_mult}*ATR）"
            out.append((i, VPSignal(
                timestamp=_to_epoch_ms_shanghai(date_s.iloc[i]), price=float(close.iloc[i]),
                anchor="close", direction="bullish", signal_type="shrink_pullback",
                confidence="medium", is_daily_approx=True, is_anomalous=False, reason=reason_str,
                threshold=config.pullback_atr_mult * atr_now, observed_value=drawdown)))
    return out
```

> 注:原 `_detect_shrink_pullback` 用 `highs[-1]`(无 `index<i` 过滤),逐窗下 `highs[-1].index ≤ (i+1)-1-k = i-k`,故 `center+k ≤ i`——与本指针一致。`seg_rel.dropna()` 全 `<阈值` ⇔ 段内非 NaN 数 >0 且无 `≥阈值` 违例。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py::test_shrink_pullback_causal_equiv_per_window_lastbar -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/volume_price_signals.py tests/test_signal_eval_vectorization.py
git commit -m "feat(signals): 新增 _detect_shrink_pullback_causal_rows(last-bar→per-bar + O(1) 段增量)"
```

---

## Task 6: `_detect_vfx_all_bars_rows`(全 bar vfx,复刻字段覆写)

**Files:**
- Modify: `src/services/volume_price_signals.py`(新增)
- Test: `tests/test_signal_eval_vectorization.py`

**Interfaces:**
- Produces: `_detect_vfx_all_bars_rows(prim, config) -> list[tuple[int, VPSignal]]`,每根 i = 原 `_detect_latest_vfx(prim[:i+1])`(末根)的 marker;字段覆写(confidence='low'/is_daily_approx/is_anomalous=False/observed_value/price=close)与原一致;neutral/anomalous 跳过。

- [ ] **Step 1: 写失败测试**

```python
def test_vfx_all_bars_equiv_per_window_lastbar():
    from src.services.volume_price_signals import (
        VPSConfig, _compute_primitives, _normalize, _detect_latest_vfx,
        _detect_vfx_all_bars_rows)
    df = _synthetic_df(100, seed=11)
    cfg = VPSConfig()
    norm, _ = _normalize(df, cfg)
    prim = _compute_primitives(norm, cfg)
    got = _detect_vfx_all_bars_rows(prim, cfg)
    ref: list[tuple[int, str, str]] = []
    for i in range(len(prim)):
        for m in _detect_latest_vfx(prim.iloc[: i + 1], cfg):
            ref.append((i, m.signal_type, m.confidence))
    assert [(i, s.signal_type, s.confidence) for i, s in got] == ref
    assert all(s.confidence == "low" for _, s in got)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py::test_vfx_all_bars_equiv_per_window_lastbar -v`
Expected: FAIL

- [ ] **Step 3: 实现(逐根跑 _classify_vfx + 复刻 _detect_latest_vfx 覆写)**

```python
def _detect_vfx_all_bars_rows(prim: pd.DataFrame, config: VPSConfig) -> list[tuple[int, VPSignal]]:
    """vfx 全 bar 变体:每根用当根 causal primitive 跑 _classify_vfx,复刻 _detect_latest_vfx 字段覆写。
    与逐窗 _detect_latest_vfx(prim[:i+1]) 逐根等价。neutral/anomalous 跳过。"""
    if prim.empty:
        return []
    close = prim["close"].astype(float).reset_index(drop=True)
    rv_s = prim["rel_vol"].reset_index(drop=True)
    pct_s = prim["pct_chg"].reset_index(drop=True)
    body_s = prim["body"].reset_index(drop=True)
    rp_s = prim["range_pos"].reset_index(drop=True)
    date_s = prim["date"].reset_index(drop=True)
    out: list[tuple[int, VPSignal]] = []
    for i in range(len(prim)):
        classified = _classify_vfx(
            rel_vol=rv_s.iloc[i], pct_chg=pct_s.iloc[i],
            body=body_s.iloc[i], range_pos=rp_s.iloc[i], config=config)
        if classified.direction == "neutral" or classified.is_anomalous:
            continue
        out.append((i, VPSignal(
            timestamp=_to_epoch_ms_shanghai(date_s.iloc[i]), price=float(close.iloc[i]),
            anchor="close", direction=classified.direction, signal_type=classified.signal_type,
            confidence="low", is_daily_approx=True, is_anomalous=False,
            reason=classified.reason, threshold=None, observed_value=classified.observed_value)))
    return out
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py::test_vfx_all_bars_equiv_per_window_lastbar -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/volume_price_signals.py tests/test_signal_eval_vectorization.py
git commit -m "feat(signals): 新增 _detect_vfx_all_bars_rows(全 bar vfx,复刻 _detect_latest_vfx 字段覆写)"
```

---

## Task 7: A 类行号变体(`_detect_breakouts_rows` / `_anchored_vwap_signals_rows` / `_detect_obv_divergence_rows`)

**Files:**
- Modify: `src/services/volume_price_signals.py`(新增 3 个)
- Test: `tests/test_signal_eval_vectorization.py`

**Interfaces:**
- Produces:
  - `_detect_breakouts_rows(prim, config) -> list[tuple[int, VPSignal]]`(`atr_series.iloc[i]` 取代切片;signal_type/threshold/observed_value 与原一致)
  - `_anchored_vwap_signals_rows(prim, config) -> list[tuple[int, VPSignal]]`(锚点用 bar 行号,非 ts.index;无重复时间戳时与原选同锚)
  - `_detect_obv_divergence_rows(prim, config) -> list[tuple[int, VPSignal]]`(marker 行号 = conf_idx)

- [ ] **Step 1: 写失败测试(三者 signal_type 级等价于原函数,clean fixture)**

```python
def test_a_class_rows_equiv_original():
    from src.services.volume_price_signals import (
        VPSConfig, _compute_primitives, _normalize,
        _detect_breakouts, _detect_breakouts_rows,
        _anchored_vwap_signals, _anchored_vwap_signals_rows,
        _detect_obv_divergence, _detect_obv_divergence_rows)
    df = _synthetic_df(200, seed=5)
    cfg = VPSConfig()
    norm, _ = _normalize(df, cfg)
    prim = _compute_primitives(norm, cfg)
    # 无重复时间戳 → 行号变体与原逐条等价(signal_type/timestamp/observed_value)
    def _key(ms): return [(m.timestamp, m.signal_type, round(m.observed_value, 6)) for m in ms]
    assert _key([s for _, s in _detect_breakouts_rows(prim, cfg)]) == _key(_detect_breakouts(prim, cfg))
    assert _key([s for _, s in _anchored_vwap_signals_rows(prim, cfg)]) == _key(_anchored_vwap_signals(prim, cfg))
    assert _key([s for _, s in _detect_obv_divergence_rows(prim, cfg)]) == _key(_detect_obv_divergence(prim, cfg))
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py::test_a_class_rows_equiv_original -v`
Expected: FAIL

- [ ] **Step 3: 实现三个行号变体**

```python
def _detect_breakouts_rows(prim: pd.DataFrame, config: VPSConfig) -> list[tuple[int, VPSignal]]:
    """_detect_breakouts 行号变体:atr_series.iloc[i] 取代 _last_finite(atr_series.iloc[:i+1])(O(1))。
    对 signal_type/threshold/observed_value 等价(reason 仅在 ATR seed 前极端 config 下可能不同,回测不用 reason)。"""
    high = prim["high"].astype(float).reset_index(drop=True)
    close = prim["close"].astype(float).reset_index(drop=True)
    prior_max = high.rolling(config.breakout_window).max().shift(1)
    rel_vol = prim["rel_vol"].reset_index(drop=True)
    pct_s = prim["pct_chg"].reset_index(drop=True)
    date_s = prim["date"].reset_index(drop=True)
    atr_series = atr(prim, config.atr_period).reset_index(drop=True)
    out: list[tuple[int, VPSignal]] = []
    for i in range(len(prim)):
        pm = prior_max.iloc[i]; rv = rel_vol.iloc[i]
        if pd.isna(pm) or pd.isna(rv):
            continue
        if close.iloc[i] >= pm and rv >= config.breakout_rel_vol:
            pct = float(pct_s.iloc[i]) if not pd.isna(pct_s.iloc[i]) else 0.0
            av = atr_series.iloc[i]
            atr_val = None if pd.isna(av) else float(av)
            atr_norm = (atr_val / float(close.iloc[i])) if (atr_val is not None and float(close.iloc[i]) > 0) else 0.0
            vp = classify_volume_pattern(float(rv), pct, atr_norm, config)
            out.append((i, VPSignal(
                timestamp=_to_epoch_ms_shanghai(date_s.iloc[i]), price=float(close.iloc[i]),
                anchor="close", direction="bullish", signal_type="volume_breakout", confidence="high",
                is_daily_approx=True, is_anomalous=False,
                reason=f"放量突破近{config.breakout_window}日高点（不含当日）[量能形态:{vp}]",
                threshold=float(pm), observed_value=float(rv))))
    return out


def _anchored_vwap_signals_rows(prim: pd.DataFrame, config: VPSConfig) -> list[tuple[int, VPSignal]]:
    """_anchored_vwap_signals 行号变体:突破锚点用 bar 行号(消除 ts.index 线扫与重复时间戳误锚)。"""
    breakout_rows = _detect_breakouts_rows(prim, config)
    if not breakout_rows:
        return []
    high = prim["high"].astype(float).reset_index(drop=True)
    low = prim["low"].astype(float).reset_index(drop=True)
    close = prim["close"].astype(float).reset_index(drop=True)
    volume = prim["volume"].astype(float).reset_index(drop=True)
    typical = (high + low + close) / 3.0
    out: list[tuple[int, VPSignal]] = []
    for start, _bsig in breakout_rows:
        cum_pv = 0.0; cum_v = 0.0; vwap: list[float] = []
        for j in range(start, len(prim)):
            cum_pv += float(typical.iloc[j]) * float(volume.iloc[j])
            cum_v += float(volume.iloc[j])
            vwap.append(cum_pv / cum_v if cum_v > 0 else float("nan"))
        for off in range(1, len(vwap)):
            j = start + off
            prev_delta = float(close.iloc[j - 1]) - vwap[off - 1]
            curr_delta = float(close.iloc[j]) - vwap[off]
            if prev_delta < 0 <= curr_delta:
                out.append((j, _avwap_signal(prim, j, "anchored_vwap_reclaim", "bullish", vwap[off])))
            elif prev_delta >= 0 > curr_delta:
                out.append((j, _avwap_signal(prim, j, "anchored_vwap_loss", "bearish", vwap[off])))
    return out


def _detect_obv_divergence_rows(prim: pd.DataFrame, config: VPSConfig) -> list[tuple[int, VPSignal]]:
    """_detect_obv_divergence 行号变体:行号 = conf_idx(curr.index+swing_k);逻辑逐字复刻。"""
    rows: list[tuple[int, VPSignal]] = []
    for sig in _detect_obv_divergence(prim, config):
        # conf 行号由 timestamp 反查在无重复时间戳下唯一;为稳健直接复算见下
        rows.append(sig)  # 占位,Step3b 替换
    raise NotImplementedError
```

> Step 3b:`_detect_obv_divergence_rows` 必须给出行号,不能靠 timestamp 反查。把 `_detect_obv_divergence`(`volume_price_signals.py:757-845`)整体复制为 `_detect_obv_divergence_rows`,把最后 `out.append(VPSignal(...))` 改为 `out.append((conf_idx, VPSignal(...)))`(其余逐字不变)。conf_idx 已在原函数 line 831 算出。

```python
def _detect_obv_divergence_rows(prim: pd.DataFrame, config: VPSConfig) -> list[tuple[int, VPSignal]]:
    close = prim["close"].astype(float).reset_index(drop=True)
    vol = prim["volume"].astype(float).reset_index(drop=True)
    high = prim["high"].astype(float).reset_index(drop=True)
    low = prim["low"].astype(float).reset_index(drop=True)
    obv_series = _obv(close, vol)
    cmf_series = _cmf(high, low, close, vol, _DIV_CMF_WINDOW)
    mfi_series = _mfi(high, low, close, vol, _DIV_MFI_WINDOW)
    pivots = _attach_pivot_timestamps(find_swing_pivots(close, config.swing_k), prim)
    out: list[tuple[int, VPSignal]] = []
    for kind, sig_type, cmp_price, cmp_ind, direction in (
        ("high", "obv_top_divergence",    lambda a, b: a > b, lambda a, b: a <= b, "bearish"),
        ("low",  "obv_bottom_divergence", lambda a, b: a < b, lambda a, b: a >= b, "bullish"),
    ):
        same = [p for p in pivots if p.kind == kind]
        for prev, curr in zip(same, same[1:]):
            if not cmp_price(curr.price, prev.price):
                continue
            obv_prev = float(obv_series.iloc[prev.index]); obv_curr = float(obv_series.iloc[curr.index])
            cmf_prev = float(cmf_series.iloc[prev.index]); cmf_curr = float(cmf_series.iloc[curr.index])
            mfi_prev = float(mfi_series.iloc[prev.index]); mfi_curr = float(mfi_series.iloc[curr.index])
            obv_div = cmp_ind(obv_curr, obv_prev) and not math.isnan(obv_curr) and not math.isnan(obv_prev)
            cmf_div = cmp_ind(cmf_curr, cmf_prev) and not math.isnan(cmf_curr) and not math.isnan(cmf_prev)
            mfi_div = cmp_ind(mfi_curr, mfi_prev) and not math.isnan(mfi_curr) and not math.isnan(mfi_prev)
            k = sum([obv_div, cmf_div, mfi_div])
            if k == 0:
                continue
            confidence = "high" if k >= 3 else ("medium" if k >= 2 else "low")
            rel_divs: list[float] = []
            if obv_div:
                denom_obv = max(abs(obv_curr), abs(obv_prev), 1.0)
                rel_divs.append(min(abs(obv_curr - obv_prev) / denom_obv, 1.0))
            if cmf_div:
                rel_divs.append(min(abs(cmf_curr - cmf_prev) / 2.0, 1.0))
            if mfi_div:
                rel_divs.append(min(abs(mfi_curr - mfi_prev) / 100.0, 1.0))
            grade = _divergence_strength_grade(k, rel_divs)
            sources_desc = "+".join(s for s, d in [("OBV", obv_div), ("CMF", cmf_div), ("MFI", mfi_div)] if d)
            conf_idx = curr.index + config.swing_k
            out.append((conf_idx, VPSignal(
                timestamp=_to_epoch_ms_shanghai(prim["date"].iloc[conf_idx]), price=curr.price,
                anchor=kind, direction=direction, signal_type=sig_type, confidence=confidence,
                is_daily_approx=True, is_anomalous=False,
                reason=f"价格创新极值但量能指标未同步（{sources_desc} 背离，强度:{grade}）",
                threshold=float(obv_prev), observed_value=float(obv_curr))))
    return out
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py::test_a_class_rows_equiv_original -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/volume_price_signals.py tests/test_signal_eval_vectorization.py
git commit -m "feat(signals): 新增 A 类行号变体(breakouts/anchored_vwap/obv_divergence;atr.iloc[i]+bar-index 锚定)"
```

---

## Task 8: 因果流式 top-k `_streaming_topk_kept`

**Files:**
- Modify: `src/services/volume_price_signals.py`(新增)
- Test: `tests/test_signal_eval_vectorization.py`

**Interfaces:**
- Produces: `_streaming_topk_kept(b_items: list[tuple[int, int, VPSignal]], k: int) -> set[int]` —— 入参 `(bar, block, sig)`,返回被 top-k 保留的 marker 的 `id()` 集合。bar t 的 marker 保留 ⇔ 在冻结池 [0:t] 的 top-k(键 `(-abs(observed_value), block, bar)`,全方向竞争)。

- [ ] **Step 1: 写失败测试(跨块平局 F2 / 同 bar 多 marker F3 / 池<k / 冻结)**

```python
def _mk(ov, direction="bullish", stype="spring"):
    from src.services.volume_price_signals import VPSignal
    return VPSignal(timestamp=0, price=0.0, anchor="low", direction=direction,
                    signal_type=stype, confidence="low", is_daily_approx=True,
                    is_anomalous=False, reason="", threshold=None, observed_value=ov)

def test_streaming_topk_cross_block_tie_prefers_vsa_then_bar():
    # F2: abs 相等(=85),k=1。block0(VSA)优先于 block1(spring),即使 VSA 的 bar 更大
    from src.services.volume_price_signals import _streaming_topk_kept
    spring = _mk(85.0, "bullish", "spring")        # bar30, block1
    vsa = _mk(85.0, "bullish", "vsa_no_supply")    # bar50, block0
    items = [(30, 1, spring), (50, 0, vsa)]
    kept = _streaming_topk_kept(items, k=1)
    # 在 bar50 的池[0:50]={spring@30, vsa@50},键 (-85,0,50)<(-85,1,30) → vsa 胜
    assert id(vsa) in kept
    # spring@30 在它自己的 bar30 池[0:30]={spring@30} 是 top-1 → 也保留(冻结)
    assert id(spring) in kept

def test_streaming_topk_same_bar_multi():
    # F3: 同 bar 两 marker,k=1,只保留 abs 大的那个
    from src.services.volume_price_signals import _streaming_topk_kept
    big = _mk(10.0, "bullish", "spring")           # bar5
    small = _mk(3.0, "bearish", "upthrust")        # bar5
    items = [(5, 1, big), (5, 1, small)]
    kept = _streaming_topk_kept(items, k=1)
    assert id(big) in kept and id(small) not in kept

def test_streaming_topk_pool_smaller_than_k():
    from src.services.volume_price_signals import _streaming_topk_kept
    a = _mk(1.0); b = _mk(2.0)
    kept = _streaming_topk_kept([(1, 0, a), (2, 0, b)], k=5)
    assert id(a) in kept and id(b) in kept

def test_streaming_topk_freeze_earlier_bar_evicted_later_still_kept():
    from src.services.volume_price_signals import _streaming_topk_kept
    early = _mk(5.0)    # bar1
    big1 = _mk(100.0)   # bar3
    big2 = _mk(101.0)   # bar4
    # k=1: bar1 池={early} → early 是 top1 → 保留;后续被 big 挤出,仍算保留
    kept = _streaming_topk_kept([(1, 0, early), (3, 0, big1), (4, 0, big2)], k=1)
    assert id(early) in kept
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py -k streaming_topk -v`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
def _streaming_topk_kept(b_items: list[tuple[int, int, VPSignal]], k: int) -> set[int]:
    """因果流式 top-k:bar t 的 marker 保留 ⇔ 在冻结池 [0:t] 的 top-k。
    键 (-abs(observed_value), block, bar) 为严格全序(同 bar+block 至多一 marker)。
    全方向竞争;调用方负责 top-k 之后再过滤 bullish。"""
    def keyf(bar: int, block: int, sig: VPSignal):
        ov = abs(sig.observed_value) if sig.observed_value is not None else 0.0
        return (-ov, block, bar)

    by_bar: dict[int, list[tuple[int, int, VPSignal]]] = {}
    for bar, block, sig in b_items:
        by_bar.setdefault(bar, []).append((bar, block, sig))

    kept_ids: set[int] = set()
    best: list[tuple[tuple, VPSignal]] = []   # 升序键、容量 k 的当前 top-k
    for bar in sorted(by_bar):
        for (b, blk, sig) in by_bar[bar]:
            kx = keyf(b, blk, sig)
            if len(best) < k:
                best.append((kx, sig)); best.sort(key=lambda e: e[0])
            elif kx < best[-1][0]:
                best[-1] = (kx, sig); best.sort(key=lambda e: e[0])
        bar_ids = {id(s) for (_, _, s) in by_bar[bar]}
        for (_, s) in best:
            if id(s) in bar_ids:
                kept_ids.add(id(s))
    return kept_ids
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py -k streaming_topk -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/volume_price_signals.py tests/test_signal_eval_vectorization.py
git commit -m "feat(signals): 新增 _streaming_topk_kept(因果流式 top-k,全序键+冻结语义+全方向竞争)"
```

---

## Task 9: `compute_signals_for_all_bars`(整合 + 退化三态 + norm 空间 warmup gate)

**Files:**
- Modify: `src/services/volume_price_signals.py`(新增)
- Test: `tests/test_signal_eval_vectorization.py`(F4/F5/F6 部分)

**Interfaces:**
- Produces: `compute_signals_for_all_bars(df, *, config, now=None) -> dict[int, list[str]]` —— key = **原始 bar 行号**,value = 该 bar bullish signal_type 列表(装配序 obv→breakout→shrink→vwap→B(top-k)→vfx,去重)。退化→空映射 / 跳过对应 bar。

- [ ] **Step 1: 写失败测试(degraded 三态 F5 + warmup norm 空间 F4 + 行号 F6)**

```python
def test_csfab_zero_volume_still_emits_obv_bottom(F5=None):
    # F5: volume 全 0(rel_vol 全 NaN),仍应有 obv_bottom_divergence(不依赖 rel_vol)
    from src.services.volume_price_signals import VPSConfig, compute_signals_for_all_bars
    n = 60
    # 下降双底制造 bottom divergence
    low = list(40 - np.linspace(0, 10, n)); 
    for j in range(40, n): low[j] = low[39] + (j - 39) * 0.1
    close = [v + 1 for v in low]
    df = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=n, freq="D").strftime("%Y-%m-%d"),
                       "open": close, "high": [c + 0.5 for c in close], "low": low,
                       "close": close, "volume": [0.0] * n})
    sig = compute_signals_for_all_bars(df, config=VPSConfig())
    flat = {st for v in sig.values() for st in v}
    assert "obv_bottom_divergence" in flat

def test_csfab_warmup_gate_norm_space():
    # F4: VPS_VOL_MA_WINDOW=50(min_bars=51) + bar5 NaN volume;raw t=50(norm 行49)应被 gate
    import os
    from src.services.volume_price_signals import VPSConfig, compute_signals_for_all_bars
    n = 60
    close = list(40 - np.linspace(0, 10, 46)) + list(30 + np.linspace(0.1, 1.4, n - 46))
    low = [c - 1 for c in close]
    vol = [1000.0] * n; vol[5] = float("nan")
    df = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=n, freq="D").strftime("%Y-%m-%d"),
                       "open": close, "high": [c + 0.5 for c in close], "low": low,
                       "close": close, "volume": vol})
    cfg = VPSConfig(vol_ma_window=50)
    sig = compute_signals_for_all_bars(df, config=cfg)
    # 与逐窗真值对齐:对原始 raw df 跑 compute_volume_price_signals(df.iloc[:51]) 取末根
    from src.services.volume_price_signals import compute_volume_price_signals, _to_epoch_ms_shanghai
    res = compute_volume_price_signals(df.iloc[:51], config=cfg)  # norm 50 行 < 51 → degraded 空
    assert res.markers == []
    assert sig.get(50, []) == []   # 行号 50 被 norm 空间 warmup gate 拦下
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py -k csfab -v`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
def compute_signals_for_all_bars(df, *, config: VPSConfig | None = None, now=None) -> dict[int, list[str]]:
    """对全 df 单遍算出每根 bar 因果触发的 bullish signal_type(回测专用,因果修正语义)。
    返回 {原始 bar 行号: [bullish signal_type, ...]}(装配序去重)。图表路径不调用本函数。"""
    cfg = config or VPSConfig()
    try:
        norm = normalize_ohlcv(df, required_columns=_REQUIRED_COLUMNS,
                               now=now, keep_original_index=True)
    except ValueError:
        return {}
    if norm.empty:
        return {}
    prim = _compute_primitives(norm, cfg)
    orig_idx = norm["_orig_idx"].astype(int).tolist()   # norm 行 → raw 行
    min_bars = max(cfg.vol_ma_window, cfg.atr_period, cfg.breakout_window) + 1

    # norm 行 → 装配序 bullish signal_type 列表
    by_norm: dict[int, list[str]] = {}

    def _add(norm_row: int, sig: VPSignal):
        if sig.direction != "bullish":
            return
        if (norm_row + 1) < min_bars:        # per-bar warmup gate(norm 行号空间)
            return
        lst = by_norm.setdefault(norm_row, [])
        if sig.signal_type not in lst:
            lst.append(sig.signal_type)

    # 1) A 类(装配序:obv → breakout → shrink → vwap)
    for r, s in _detect_obv_divergence_rows(prim, cfg):
        _add(r, s)
    for r, s in _detect_breakouts_rows(prim, cfg):
        _add(r, s)
    for r, s in _detect_shrink_pullback_causal_rows(prim, cfg):
        _add(r, s)
    for r, s in _anchored_vwap_signals_rows(prim, cfg):
        _add(r, s)

    # 2) B 类:RAW 因果 marker(VSA block0 + upthrust/spring block1)→ 流式 top-k
    b_items: list[tuple[int, int, VPSignal]] = []
    for r, s in _detect_vsa_bars_rows(prim, cfg):
        b_items.append((r, 0, s))
    for r, s in _detect_upthrust_spring_causal_rows(prim, cfg):
        b_items.append((r, 1, s))
    kept = _streaming_topk_kept(b_items, cfg.b_class_top_k)
    for r, blk, s in b_items:
        if id(s) in kept:
            _add(r, s)

    # 3) vfx(旁路 top-k)
    for r, s in _detect_vfx_all_bars_rows(prim, cfg):
        _add(r, s)

    # 4) norm 行 → raw 行
    return {orig_idx[nr]: lst for nr, lst in by_norm.items()}
```

> 注:`_add` 的装配序通过调用顺序保证;同 bar 内若多检测器命中,list 顺序 = obv→breakout→shrink→vwap→B→vfx。B 类是否进 `_add` 由 `kept` 决定,vfx 不进 b_items(旁路)。退化(c):rel_vol 全 NaN 时 normalize 不失败、prim 仍算,obv 等照常产 marker → 仍归位(与逐窗一致)。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py -k csfab -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/volume_price_signals.py tests/test_signal_eval_vectorization.py
git commit -m "feat(signals): 新增 compute_signals_for_all_bars(单遍因果信号+norm空间warmup gate+退化三态)"
```

---

## Task 10: `_eval` 改写为预计算命中

**Files:**
- Modify: `src/services/signal_backtest.py:24-29`(import)、`97-171`(_eval)
- Test: `tests/test_signal_backtest*.py`(既有,作回归)

**Interfaces:**
- Consumes: `derive_price_levels_series`、`compute_signals_for_all_bars`(Task 2/9)。
- Produces: `_eval` 行为对外不变(签名不变),内部 O(n log k)。

- [ ] **Step 1: 写失败测试(分钟路径性能 smoke:2000 根应秒级完成)**

```python
# tests/test_signal_eval_vectorization.py
import time
def test_eval_minute_scale_is_subquadratic():
    from src.services.signal_backtest import evaluate_signal_outcomes
    df = _synthetic_df(2000, seed=9)
    t0 = time.time()
    out = evaluate_signal_outcomes(df, market="crypto", horizon=10)
    elapsed = time.time() - t0
    assert elapsed < 20.0, f"2000 bars took {elapsed:.1f}s (expected subquadratic)"
    assert isinstance(out, list)
```

- [ ] **Step 2: 运行确认失败(当前 O(n²),2000 根远超 20s)**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py::test_eval_minute_scale_is_subquadratic -v`
Expected: FAIL（超时/远超 20s）

- [ ] **Step 3: 改写 _eval**

`signal_backtest.py:24-29` import 改为:

```python
from src.services.volume_price_signals import (
    VPSConfig,
    compute_signals_for_all_bars,
    derive_price_levels_series,
)
```

`signal_backtest.py:97-171` 的 `_eval` 函数体改为:

```python
def _eval(
    df: pd.DataFrame,
    *,
    market: str,
    horizon: int,
    config: Optional[VPSConfig],
    all_bars: bool,
    min_history: int,
) -> List[SignalOutcome]:
    cfg = config or VPSConfig.from_env()
    df = df.reset_index(drop=True)
    out: List[SignalOutcome] = []
    n = len(df)

    levels = derive_price_levels_series(df)                       # raw 空间, O(n)
    sig_by_bar = {} if all_bars else compute_signals_for_all_bars(df, config=cfg)  # O(n log k)

    for t in range(min_history, n - 1):                          # 至少留 1 根前瞻
        lv = levels[t]
        if lv.stop is None or lv.target is None:
            continue
        fwd = _bars_as_dicts(df.iloc[t + 1 : t + 1 + horizon])
        if not fwd:
            continue
        if all_bars:
            out.append(SignalOutcome(
                signal_type=BASELINE_SIGNAL_TYPE, market=market,
                outcome=classify_triple_barrier(fwd, stop=lv.stop, target=lv.target)))
        else:
            for sig_type in sig_by_bar.get(t, ()):              # 已过滤 bullish, 装配序
                out.append(SignalOutcome(
                    signal_type=sig_type, market=market,
                    outcome=classify_triple_barrier(fwd, stop=lv.stop, target=lv.target)))
    return out
```

> 注:`_last_ts`/`_to_epoch_ms_shanghai` 不再被 `_eval` 使用;`_last_ts` 若无其它引用可保留(供测试)或删除(确认无引用后)。`compute_volume_price_signals`/`derive_price_levels` import 移除(已不在 _eval 用)。

- [ ] **Step 4: 运行确认通过 + 既有回测回归**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py::test_eval_minute_scale_is_subquadratic tests/test_signal_backtest.py tests/test_signal_backtest_service.py -v`
Expected: PASS（性能 smoke 通过;既有回测测试需结合 Task 11 golden 一并校验语义——若既有测试断言旧逐窗精确值,可能因因果修正语义而需更新,见 Task 11）

- [ ] **Step 5: Commit**

```bash
git add src/services/signal_backtest.py tests/test_signal_eval_vectorization.py
git commit -m "refactor(signals): _eval 改用 derive_price_levels_series + compute_signals_for_all_bars(O(n²)→O(n log k))"
```

---

## Task 11: 核心 golden —— 独立因果 oracle + F1–F12 + 多市场

**Files:**
- Modify: `tests/test_signal_eval_vectorization.py`(新增 oracle + golden)

**Interfaces:**
- Consumes: `evaluate_signal_outcomes`/`evaluate_baseline_outcomes`(新)+ 原始检测器(oracle 用)。

- [ ] **Step 1: 写 oracle + pin + golden 测试**

```python
# tests/test_signal_eval_vectorization.py —— 测试内独立因果参照(绕过 _limit_b_class)
from collections import Counter
from src.services.volume_price_signals import (
    VPSConfig, _compute_primitives, _normalize, _detect_vsa_bars, _detect_upthrust_spring,
    _limit_b_class, compute_volume_price_signals, _to_epoch_ms_shanghai, derive_price_levels)
from src.services.signal_backtest import (
    evaluate_signal_outcomes, evaluate_baseline_outcomes, classify_triple_barrier, _bars_as_dicts)

_B_TYPES = {"vsa_no_demand", "vsa_no_supply", "vsa_stopping", "vsa_effort_vs_result", "upthrust", "spring"}

def _oracle_bullish_by_bar(df, cfg):
    """独立因果参照:B 类绕过 _limit_b_class(直接调原始检测器取逐窗末根 RAW),A/vfx/shrink 经
    compute_volume_price_signals 末根。仅用于无重复时间戳 + 历史日期 fixture(等价适用域)。"""
    df = df.reset_index(drop=True)
    n = len(df)
    raw_b_pool = []        # (bar, block, sig) RAW 因果 B marker
    a_vfx_by_bar = {}      # bar -> set(bullish 非 B 类 signal_type)
    for i in range(n):
        sub = df.iloc[: i + 1]
        norm, reason = _normalize(sub, cfg)
        if reason is not None:
            continue
        from src.services.volume_price_signals import _check_sufficient_window
        if _check_sufficient_window(norm, cfg) is not None:
            continue
        prim = _compute_primitives(norm, cfg)
        last_ts = _to_epoch_ms_shanghai(sub["date"].iloc[-1])
        # A/vfx/shrink:从编排器末根取(非 B 类),bullish
        res = compute_volume_price_signals(sub, config=cfg)
        a_vfx_by_bar[i] = {
            m.signal_type for m in res.markers
            if m.timestamp == last_ts and m.direction == "bullish" and m.signal_type not in _B_TYPES}
        # B 类:绕过 _limit_b_class,取末根 RAW(VSA block0 + upthrust/spring block1)
        for m in _detect_vsa_bars(prim, cfg):
            if m.timestamp == last_ts:
                raw_b_pool.append((i, 0, m))
        for m in _detect_upthrust_spring(prim, cfg):
            if m.timestamp == last_ts:
                raw_b_pool.append((i, 1, m))
    # 每个 bar t:对 pool[0:t] 朴素 top-k(键 -abs,block,bar),取末根==t 的 bullish B
    result = {}
    for t in range(n):
        pool = [(b, blk, s) for (b, blk, s) in raw_b_pool if b <= t]
        pool_sorted = sorted(pool, key=lambda e: (-(abs(e[2].observed_value) if e[2].observed_value is not None else 0.0), e[1], e[0]))
        kept = pool_sorted[: cfg.b_class_top_k]
        b_at_t = {s.signal_type for (b, blk, s) in kept if b == t and s.direction == "bullish"}
        bull = set(a_vfx_by_bar.get(t, set())) | b_at_t
        if bull:
            result[t] = bull
    return result

def _oracle_signal_outcomes(df, *, market, horizon, cfg, min_history=40):
    df = df.reset_index(drop=True)
    n = len(df)
    bull = _oracle_bullish_by_bar(df, cfg)
    out = []
    for t in range(min_history, n - 1):
        ref = derive_price_levels(df.iloc[: t + 1])
        if ref.stop is None or ref.target is None:
            continue
        fwd = _bars_as_dicts(df.iloc[t + 1 : t + 1 + horizon])
        if not fwd:
            continue
        for st in bull.get(t, set()):
            out.append((st, market, classify_triple_barrier(fwd, stop=ref.stop, target=ref.target)))
    return out

def _counter(outcomes):
    return Counter((o.signal_type, o.market, o.outcome) for o in outcomes)

@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_golden_signal_equiv_oracle_multimarket(seed):
    df = _synthetic_df(160, seed=seed)
    cfg = VPSConfig.from_env()
    got = evaluate_signal_outcomes(df, market="cn", horizon=10, config=cfg)
    ref = _oracle_signal_outcomes(df, market="cn", horizon=10, cfg=cfg)
    got_c = _counter(got)
    ref_c = Counter((st, mk, oc) for (st, mk, oc) in ref)
    assert got_c == ref_c

def test_golden_baseline_unchanged():
    df = _synthetic_df(160, seed=2)
    base = evaluate_baseline_outcomes(df, market="cn", horizon=10)
    # baseline 不跑信号规则,只依赖 derive_price_levels_series == 逐窗(Task 2 已证)
    # 逐窗参照
    df2 = df.reset_index(drop=True); n = len(df2); ref = []
    for t in range(40, n - 1):
        lv = derive_price_levels(df2.iloc[: t + 1])
        if lv.stop is None or lv.target is None: continue
        fwd = _bars_as_dicts(df2.iloc[t + 1 : t + 1 + 10])
        if not fwd: continue
        ref.append(("__baseline__", "cn", classify_triple_barrier(fwd, stop=lv.stop, target=lv.target)))
    assert _counter(base) == Counter(ref)
```

> 集合/计数比较(非哈希序逐条),对齐下游 `aggregate_signal_stats` 顺序无关语义。F1(pivot 跨界)、F2(平局)、F3(同 bar 多)、F5(零量)等已在 Task 4/8/9 单测覆盖;此处用多 seed 合成数据做整体 oracle 等价。

- [ ] **Step 2: 运行确认(先红后绿)**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py -k "golden" -v`
Expected: 若 Task 1-10 正确,应 PASS;任何 FAIL 即定位到对应 Task 的因果/等价缺陷,回到该 Task 修复。

- [ ] **Step 3: F12(Q3 今日 partial,单侧生产断言)**

```python
from datetime import datetime
def test_q3_today_closed_minute_bars_emit(monkeypatch):
    # 注入 now<16:00 + 今日多根分钟 bar;t<n-1 的今日已收盘 bar 应正常产信号(单侧)
    from src.services.volume_price_signals import compute_signals_for_all_bars, VPSConfig
    n = 80
    close = list(50 + np.linspace(0, 8, n))
    base = pd.Timestamp("2020-06-25 09:30:00")
    dates = [(base + pd.Timedelta(minutes=5 * j)).strftime("%Y-%m-%d %H:%M:%S") for j in range(n)]
    df = pd.DataFrame({"date": dates, "open": close, "high": [c + 0.3 for c in close],
                       "low": [c - 0.3 for c in close], "close": close, "volume": [1000.0] * n})
    now = datetime(2020, 6, 25, 11, 0, 0)   # <16:00,今日
    sig = compute_signals_for_all_bars(df, config=VPSConfig(), now=now)
    # 仅全局末根受 partial 影响;中间今日已收盘 bar 可正常归位(至少某个 bar 有信号)
    assert any(k < n - 1 for k in sig.keys())
```

- [ ] **Step 4: 运行 + 既有回测语义回归核对**

Run: `.venv/bin/python -m pytest tests/test_signal_eval_vectorization.py -v`
Run: `.venv/bin/python -m pytest tests/test_signal_backtest.py tests/test_signal_backtest_service.py tests/test_signal_board_service.py -v`
Expected: PASS;若既有测试断言了旧逐窗精确命中值(因 Q1–Q4 因果修正而变),更新这些断言并在 commit message 说明属 §5.2 预期行为变更。

- [ ] **Step 5: Commit**

```bash
git add tests/test_signal_eval_vectorization.py
git commit -m "test(signals): 加因果 oracle(绕过 _limit_b_class)+ 多市场 golden + F12 Q3 单侧断言"
```

---

## Task 12: 近边界 viz 回归 + 性能复验 + 文档

**Files:**
- Modify: `tests/test_volume_price_signals.py`(近边界 viz 回归)
- Modify: `docs/signal-credibility.md`、`docs/CHANGELOG.md`

**Interfaces:** 无新增。

- [ ] **Step 1: 写近边界 viz 回归(路径 1 = compute_volume_price_signals 全 df 仍渲染非因果 upthrust/spring)**

```python
# tests/test_volume_price_signals.py 追加
def test_chart_path_renders_near_boundary_upthrust():
    """路径1(图表)用全 df 调用,应保留近边界非因果 upthrust/spring viz marker。
    锁死'因果变体是并列新函数、未就地改共享 _detect_upthrust_spring'。"""
    from src.services.volume_price_signals import compute_volume_price_signals, VPSConfig
    cfg = VPSConfig(swing_k=2, b_class_top_k=10)
    # 制造一个高点 pivot center=c,在 c+1 处假突破(c+1 时 pivot 未确认,但全 df viz 会渲染)
    n = 30
    high = [10.0] * n; close = [9.5] * n; low = [9.0] * n
    high[20] = 12.0; close[20] = 11.5            # 高点 center=20(confirm@22)
    high[21] = 12.5; close[21] = 11.0            # bar21 假突破前高、收回 → upthrust(non-causal at 21)
    df = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=n, freq="D").strftime("%Y-%m-%d"),
                       "open": close, "high": high, "low": low, "close": close, "volume": [100.0] * n})
    res = compute_volume_price_signals(df, config=cfg)
    types = {m.signal_type for m in res.markers}
    assert "upthrust" in types   # 路径1 全 df 仍渲染(若被就地改成 center+k<=i 则消失 → 本测试守护)
```

- [ ] **Step 2: 运行确认通过(实现期路径 1 未被触碰)**

Run: `.venv/bin/python -m pytest tests/test_volume_price_signals.py::test_chart_path_renders_near_boundary_upthrust tests/test_volume_price_signals.py -q`
Expected: PASS（全 30+ 既有用例 + 新 viz 回归)

- [ ] **Step 3: 性能复验脚本(200/500/2000/10000 近线性)**

Run:
```bash
.venv/bin/python - <<'PY'
import time, numpy as np, pandas as pd
from src.services.signal_backtest import evaluate_signal_outcomes
def mk(n):
    rng=np.random.default_rng(0); c=50+np.cumsum(rng.normal(0,.5,n))
    return pd.DataFrame({"date":pd.date_range("2020-01-01",periods=n,freq="D").strftime("%Y-%m-%d"),
        "open":c,"high":c+1,"low":c-1,"close":c,"volume":rng.uniform(1e3,5e3,n)})
for n in (200,500,2000,10000):
    df=mk(n); t=time.time(); evaluate_signal_outcomes(df,market="crypto",horizon=10); print(n, round(time.time()-t,3),"s")
PY
```
Expected: 耗时随 n 近线性(非 ~平方);10000 根应在数秒内。记录数据填入 PR 说明。

- [ ] **Step 4: 文档(§5.2 行为变更 + CHANGELOG)**

更新 `docs/signal-credibility.md`:新增小节说明回测改因果修正语义、命中率 7 字段(hit_rate/hit_sample/verified/ci_low/ci_high/baseline_excess/horizon)在重跑 `--signal-backtest` 后会变、注解可能跨 min_sample 阈值出现/消失、今日已收盘 bar 现正常产信号。

`docs/CHANGELOG.md` `[Unreleased]` 追加(扁平格式,各一行):
```
- [改进] 信号回测走查向量化(O(n²)→O(n log k)),链路B 分钟路径可用;回测改因果修正语义,命中率统计重跑后更新(图表 marker 几何不变)
- [测试] 新增信号引擎向量化因果 oracle golden(绕过 _limit_b_class)与 F1–F12 区分性反例 fixture
```

- [ ] **Step 5: 全门禁 + Commit**

Run: `./scripts/ci_gate.sh`
Expected: flake8 + `pytest -m "not network"` 全绿。

```bash
git add tests/test_volume_price_signals.py docs/signal-credibility.md docs/CHANGELOG.md
git commit -m "test+docs(signals): 近边界 viz 回归(路径1不变)+ 性能复验 + §5.2 行为变更/CHANGELOG"
```

---

## Self-Review

**1. Spec 覆盖:**
- §1.1/§1.2 方案 C 因果修正 → Task 4/5/8/9(Q1/Q2/Q3/Q4)✓
- §4.1 compute_signals_for_all_bars → Task 9 ✓;§4.2 derive_price_levels_series → Task 2 ✓;§4.3 anchored_vwap bar-index → Task 7 ✓
- §4.4 _eval 改写 → Task 10 ✓
- §6.1(a) 图表既有套件 + 近边界 viz → Task 12 ✓;§6.1(b) 独立因果 oracle(绕过 _limit_b_class) → Task 11 ✓
- §6.3 F1–F12 → F1(T4)/F2(T8)/F3(T8)/F4(T9)/F5(T9)/F6a-c(T1/T9)/F7(T2 既有 price-level 测试)/F8(T2)/F9(T5)/F10(T6)/F11(T7)/F12(T11)✓
- §5.2 行为变更 + 7 字段 → Task 12 ✓;D5 norm2raw(keep_original_index)→ Task 1 ✓;D6 三态/warmup norm 空间 → Task 9 ✓
- §1 硬约束(全传递树不动 + 并列新函数)→ 所有 Task 新增函数,Task 12 viz 回归守护 ✓

**2. Placeholder 扫描:** Task 7 Step 3 的占位 `raise NotImplementedError` 已由 Step 3b 完整实现替换;无其它 TBD/TODO。

**3. 类型一致性:** `_*_rows` 统一返回 `list[tuple[int, VPSignal]]`;`_streaming_topk_kept` 入参 `(bar, block, sig)`、返回 `set[int]`;`compute_signals_for_all_bars` 返回 `dict[int, list[str]]`;`derive_price_levels_series` 返回 `list[PriceLevels]`。Task 9 调用与各 Task 定义签名一致。

**遗留判断点(执行时确认):** 既有 `tests/test_signal_backtest*.py` 若存在断言旧逐窗精确命中值的用例,会因 Q1–Q4 因果修正语义而需更新(Task 10/11 Step 4 已标注)——这是 §5.2 预期行为变更,非缺陷;更新时在 commit 注明,不得为"凑测试"反向改实现。

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-26-signal-eval-vectorization.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - 每任务派一个 fresh subagent,任务间 review,快速迭代。

**2. Inline Execution** - 本会话内按 executing-plans 批量执行 + 检查点。

**Which approach?**
