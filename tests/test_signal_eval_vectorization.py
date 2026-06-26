# tests/test_signal_eval_vectorization.py
import time
from collections import Counter
from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from src.services.signal_backtest import (
    _bars_as_dicts, classify_triple_barrier, evaluate_baseline_outcomes, evaluate_signal_outcomes)
from src.services.volume_price_signals import (
    VPSConfig, _check_sufficient_window, _compute_primitives, _detect_upthrust_spring,
    _detect_vsa_bars, _normalize, _to_epoch_ms_shanghai, compute_volume_price_signals,
    derive_price_levels, derive_price_levels_series)


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
        assert s.timestamp == _to_epoch_ms_shanghai(prim["date"].iloc[i])


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


# ─── Task 8: _streaming_topk_kept ─────────────────────────────────────────────

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
    a = _mk(1.0)
    b = _mk(2.0)
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


def test_csfab_zero_volume_still_emits_obv_bottom():
    # F5: volume 全 0(rel_vol 全 NaN),仍应有 obv_bottom_divergence(不依赖 rel_vol)
    # W 形下降双底:第一底 bar16≈35.3,第二底 bar38≈33.0(价格更低,OBV 全 0 不随动 → bullish 背离)
    from src.services.volume_price_signals import VPSConfig, compute_signals_for_all_bars
    n = 60
    close = (
        [41.0 - i * 0.375 for i in range(16)]          # bars 0-15:下行第一段
        + [35.0 + i * 0.333 for i in range(1, 10)]     # bars 16-24:反弹
        + [38.0 - i * 0.357 for i in range(1, 15)]     # bars 25-38:下行至更低底
        + [33.0 + i * 0.143 for i in range(1, 22)]     # bars 39-59:最终回升
    )
    low = [c - 0.5 for c in close]
    df = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=n, freq="D").strftime("%Y-%m-%d"),
                       "open": close, "high": [c + 0.5 for c in close], "low": low,
                       "close": close, "volume": [0.0] * n})
    sig = compute_signals_for_all_bars(df, config=VPSConfig())
    flat = {st for v in sig.values() for st in v}
    assert "obv_bottom_divergence" in flat


def test_csfab_warmup_gate_norm_space():
    # F4: VPS_VOL_MA_WINDOW=50(min_bars=51) + bar5 NaN volume;raw t=50(norm 行49)应被 gate
    from src.services.volume_price_signals import VPSConfig, compute_signals_for_all_bars
    n = 60
    close = list(40 - np.linspace(0, 10, 46)) + list(30 + np.linspace(0.1, 1.4, n - 46))
    low = [c - 1 for c in close]
    vol = [1000.0] * n
    vol[5] = float("nan")
    df = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=n, freq="D").strftime("%Y-%m-%d"),
                       "open": close, "high": [c + 0.5 for c in close], "low": low,
                       "close": close, "volume": vol})
    cfg = VPSConfig(vol_ma_window=50)
    sig = compute_signals_for_all_bars(df, config=cfg)
    # 与逐窗真值对齐:对原始 raw df 跑 compute_volume_price_signals(df.iloc[:51]) 取末根
    from src.services.volume_price_signals import compute_volume_price_signals
    res = compute_volume_price_signals(df.iloc[:51], config=cfg)  # norm 50 行 < 51 → degraded 空
    assert res.markers == []
    assert sig.get(50, []) == []   # 行号 50 被 norm 空间 warmup gate 拦下


# ─── Task 10: 性能 smoke ───────────────────────────────────────────────────────

def test_eval_minute_scale_is_subquadratic():
    from src.services.signal_backtest import evaluate_signal_outcomes
    df = _synthetic_df(2000, seed=9)
    t0 = time.time()
    out = evaluate_signal_outcomes(df, market="crypto", horizon=10)
    elapsed = time.time() - t0
    assert elapsed < 20.0, f"2000 bars took {elapsed:.1f}s (expected subquadratic)"
    assert isinstance(out, list)


# ─── Task 11: 独立因果 oracle + 多市场 golden + F12 Q3 ─────────────────────────

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
        pool_sorted = sorted(
            pool,
            key=lambda e: (-(abs(e[2].observed_value) if e[2].observed_value is not None else 0.0), e[1], e[0]))
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
    df2 = df.reset_index(drop=True)
    n = len(df2)
    ref = []
    for t in range(40, n - 1):
        lv = derive_price_levels(df2.iloc[: t + 1])
        if lv.stop is None or lv.target is None:
            continue
        fwd = _bars_as_dicts(df2.iloc[t + 1 : t + 1 + 10])
        if not fwd:
            continue
        ref.append(("__baseline__", "cn", classify_triple_barrier(fwd, stop=lv.stop, target=lv.target)))
    assert _counter(base) == Counter(ref)


def test_q3_today_closed_minute_bars_emit():
    # 注入 now<16:00 + 今日多根分钟 bar;t<n-1 的今日已收盘 bar 应正常产信号(单侧)。
    # 注:用随机游走分钟序列(同 _synthetic_df 口径)以确保确有信号触发;brief 原平直斜坡
    # (恒定量 + pct_chg<eps)不产任何信号,无法体现“今日已收盘 bar 仍出点”的被测语义。
    from src.services.volume_price_signals import compute_signals_for_all_bars, VPSConfig
    n = 160
    rng = np.random.default_rng(7)
    close = 50 + np.cumsum(rng.normal(0, 0.5, n))
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    open_ = close + rng.normal(0, 0.3, n)
    vol = rng.uniform(1e3, 5e3, n)
    base = pd.Timestamp("2020-06-25 09:30:00")
    dates = [(base + pd.Timedelta(minutes=5 * j)).strftime("%Y-%m-%d %H:%M:%S") for j in range(n)]
    df = pd.DataFrame({"date": dates, "open": open_, "high": high, "low": low,
                       "close": close, "volume": vol})
    now = datetime(2020, 6, 25, 11, 0, 0)   # <16:00,今日(全部 bar 均为今日)
    sig = compute_signals_for_all_bars(df, config=VPSConfig(), now=now)
    # 仅全局末根(今日 partial)受影响被丢弃;中间今日已收盘 bar 可正常归位(至少某个 bar 有信号)
    assert any(k < n - 1 for k in sig.keys())
