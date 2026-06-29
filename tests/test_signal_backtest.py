# -*- coding: utf-8 -*-
"""tests/test_signal_backtest.py — 三重门回测评估器单元测试（TDD Step 1）"""
import numpy as np
import pandas as pd
import pytest

from src.services.signal_backtest import (
    classify_triple_barrier,
    evaluate_baseline_outcomes,
    evaluate_signal_outcomes,
    SignalOutcome,
)


def _bar(h, l, c=None):
    return {"high": h, "low": l, "close": c if c is not None else (h + l) / 2}


# ---------------------------------------------------------------------------
# classify_triple_barrier
# ---------------------------------------------------------------------------

def test_triple_barrier_win_when_target_hit_first():
    """第2根先触及 target=110 → win"""
    fwd = [_bar(105, 99), _bar(112, 104)]  # 第2根 high=112 >= target=110
    assert classify_triple_barrier(fwd, stop=95.0, target=110.0) == "win"


def test_triple_barrier_loss_when_stop_hit_first():
    """第1根先破 stop=95 → loss"""
    fwd = [_bar(104, 94), _bar(111, 100)]  # 第1根 low=94 <= stop=95
    assert classify_triple_barrier(fwd, stop=95.0, target=110.0) == "loss"


def test_triple_barrier_same_bar_both_is_conservative_loss():
    """同根既触 target 又破 stop → 保守判 loss"""
    fwd = [_bar(111, 94)]  # high=111 >= 110 且 low=94 <= 95
    assert classify_triple_barrier(fwd, stop=95.0, target=110.0) == "loss"


def test_triple_barrier_expired_when_neither_touched():
    """到期未触任何门 → expired"""
    fwd = [_bar(106, 99), _bar(108, 101)]
    assert classify_triple_barrier(fwd, stop=95.0, target=110.0) == "expired"


def test_triple_barrier_empty_forward_bars_is_expired():
    """无前瞻 bar → expired（不抛异常）"""
    assert classify_triple_barrier([], stop=95.0, target=110.0) == "expired"


def test_triple_barrier_exact_boundary_target():
    """high 恰好等于 target（边界包含）→ win"""
    fwd = [_bar(110.0, 99.0)]
    assert classify_triple_barrier(fwd, stop=95.0, target=110.0) == "win"


def test_triple_barrier_exact_boundary_stop():
    """low 恰好等于 stop（边界包含）→ loss"""
    fwd = [_bar(105.0, 95.0)]
    assert classify_triple_barrier(fwd, stop=95.0, target=110.0) == "loss"


# ---------------------------------------------------------------------------
# evaluate_signal_outcomes
# ---------------------------------------------------------------------------

def test_evaluate_signal_outcomes_tags_market_and_signal_type():
    """产出的 SignalOutcome 均携带正确 market 和合法 outcome 值（fixture 保证 ≥1 bullish 触发）"""
    df = _make_history_with_signals()
    outs = evaluate_signal_outcomes(df, market="cn", horizon=10)
    assert len(outs) > 0, "fixture 应至少触发 1 条 bullish 信号；请检查 _make_history_with_signals"
    assert all(isinstance(o, SignalOutcome) and o.market == "cn" for o in outs)
    assert all(o.outcome in {"win", "loss", "expired"} for o in outs)


def test_evaluate_signal_outcomes_exact_dedup_count():
    """去重计数守卫：fixture 在 bar 60 唯一触发 1 条 volume_breakout。

    向量化 _eval 经 compute_signals_for_all_bars 单遍预计算每根 bar 的因果信号集合，每根 bar
    的同类信号至多计一次；此测试用精确计数(==1 而非 >0)守护该去重语义：若预计算把相邻 bar 的
    volume_breakout 重复计入，Counter 将 > 1。bar 60 落在 C1 新边界 range(40, 70) 内，截尾修复
    不影响本断言。
    """
    from collections import Counter
    df = _make_history_with_signals()
    outcomes = evaluate_signal_outcomes(df, market="cn", horizon=10)
    vb_count = Counter(o.signal_type for o in outcomes)["volume_breakout"]
    # fixture 的 bar 60 设计为唯一触发点，period 内恰好 1 条 volume_breakout
    assert vb_count == 1, (
        f"期望 volume_breakout 恰好 1 条，实际得到 {vb_count}；"
        "若向量化 _eval 经 compute_signals_for_all_bars 预计算把相邻 bar 的 volume_breakout 重复计入则此处失败。"
    )


def test_evaluate_signal_outcomes_returns_list():
    """函数签名返回 list，即使无触发也不抛异常"""
    df = _make_history_with_signals()
    result = evaluate_signal_outcomes(df, market="us", horizon=5)
    assert isinstance(result, list)


def test_evaluate_signal_outcomes_causal_no_future_leak():
    """因果约束：截断未来 bar 不改变已决定的早期结果。

    若实现存在前瞻泄露（如用了 df.iloc[t:] 而非 df.iloc[:t+1]），则
    在 full_df 下产出的早期信号可能与 df[:k] 的产出不一致，此测试会捕捉到。

    策略：
    - 用 df[:k] 和 full_df 分别运行 evaluate_signal_outcomes。
    - k 取 full_df 长度的前 2/3，并留足 horizon 前瞻（=10）之外的空间。
    - 对 df[:k] 中的所有 outcome，在 full_df 的产出中也必须存在
      （因果下早期窗口等价于完整窗口的前缀，信号触发时机不会随未来数据改变）。
    实际上此处验证"截断不多生成信号"：df[:k] 不应有在 full_df 中消失的记录。
    """
    full_df = _make_history_with_signals()
    n = len(full_df)
    horizon = 10
    k = n * 2 // 3  # 取前 2/3，后 1/3 为"未来"

    outs_truncated = evaluate_signal_outcomes(full_df.iloc[:k].copy(), market="cn", horizon=horizon)
    outs_full = evaluate_signal_outcomes(full_df, market="cn", horizon=horizon)

    # 早期触发的信号（在 df[:k] 中决定的）应是 full_df 产出的子集
    # 若实现有前瞻泄露，早期信号的 outcome 会受后续 bar 影响，两边对不上。
    from collections import Counter
    trunc_counts = Counter((o.signal_type, o.outcome) for o in outs_truncated)
    full_counts = Counter((o.signal_type, o.outcome) for o in outs_full)
    for key, cnt in trunc_counts.items():
        assert full_counts[key] >= cnt, (
            f"因果泄露：df[:k] 产出 {key}×{cnt} 条，但 full_df 只有 {full_counts[key]} 条；"
            "截断未来不应增加早期已触发信号数量。"
        )


# ---------------------------------------------------------------------------
# evaluate_baseline_outcomes
# ---------------------------------------------------------------------------

def test_baseline_uses_all_bars_not_only_triggers():
    """baseline 用全体有效 bar 入场，数量 >= 信号触发点（fixture 保证 sig ≥1）"""
    df = _make_history_with_signals()
    base = evaluate_baseline_outcomes(df, market="cn", horizon=10)
    sig = evaluate_signal_outcomes(df, market="cn", horizon=10)
    assert all(o.signal_type == "__baseline__" for o in base)
    assert len(sig) > 0, "fixture 应至少触发 1 条 bullish 信号；请检查 _make_history_with_signals"
    assert len(base) >= len(sig)  # 全体 bar 入场点 ≥ 信号触发点


def test_baseline_outcome_values_are_valid():
    """baseline 产出的 outcome 只含合法值"""
    df = _make_history_with_signals()
    base = evaluate_baseline_outcomes(df, market="cn", horizon=10)
    assert all(o.outcome in {"win", "loss", "expired"} for o in base)


def test_baseline_signal_type_is_always_baseline():
    """baseline 的 signal_type 固定为 __baseline__"""
    df = _make_history_with_signals()
    base = evaluate_baseline_outcomes(df, market="hk", horizon=5)
    assert all(o.signal_type == "__baseline__" for o in base)


def test_baseline_market_propagated():
    """baseline 产出的 market 与入参一致"""
    df = _make_history_with_signals()
    base = evaluate_baseline_outcomes(df, market="us", horizon=10)
    assert all(o.market == "us" for o in base)


# ---------------------------------------------------------------------------
# SignalOutcome dataclass
# ---------------------------------------------------------------------------

def test_signal_outcome_is_frozen():
    """SignalOutcome 是 frozen dataclass，不可变"""
    o = SignalOutcome(signal_type="volume_breakout", market="cn", outcome="win")
    with pytest.raises((AttributeError, TypeError)):
        o.outcome = "loss"  # type: ignore[misc]


def test_signal_outcome_equality():
    """相同字段的 SignalOutcome 相等"""
    a = SignalOutcome(signal_type="shrink_pullback", market="cn", outcome="expired")
    b = SignalOutcome(signal_type="shrink_pullback", market="cn", outcome="expired")
    assert a == b


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_history_with_signals():
    """构造含确定性 volume_breakout 触发点的历史数据。

    结构：
    - bar 0–59：平稳价格（100→101 缓涨），正常成交量 1_000_000，用于 VPS 预热窗口。
    - bar 60（突破 bar）：close 明确超过前 20 bar high 的最大值，
      volume = 2_500_000（≈2.5× 均值 1_000_000，超过 breakout_rel_vol=2.0 阈值）。
    - bar 61–79：平稳收尾（价格轻微回落），提供 evaluate_signal_outcomes 所需前瞻序列。

    VPSConfig 默认值：breakout_window=20，breakout_rel_vol=2.0，vol_ma_window=20。
    本 fixture 在 bar 60 必然满足触发条件（rel_vol ≈ 2.5 ≥ 2.0，且 close > prior_max）。
    调用方可用 compute_volume_price_signals 验证 ≥1 direction=='bullish' marker 存在。
    """
    n = 80
    base_vol = 1_000_000

    # 前 60 根：缓涨 100→101，单调递增便于 prior_max 稳定
    flat_close = list(np.linspace(100.0, 101.0, 60))
    # bar 60：突破价 = 前 20 bar high 最大值 * 1.05（绝对超过），成交量 2.5×
    flat_high_max = max(c * 1.01 for c in flat_close[-20:])  # 前 20 bar high 的最大值
    breakout_close = flat_high_max * 1.05  # 超过前 20 bar high 的最大值 5%
    # bar 61–79：轻微回落，不触及止损（用于前瞻 bar 分类）
    tail_close = list(np.linspace(breakout_close * 0.99, breakout_close * 0.98, 19))

    close = flat_close + [breakout_close] + tail_close
    assert len(close) == n

    volume = [base_vol] * 60 + [int(base_vol * 2.5)] + [base_vol] * 19

    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n).strftime("%Y-%m-%d"),
        "open": close,
        "high": [c * 1.01 for c in close],
        "low": [c * 0.99 for c in close],
        "close": close,
        "volume": volume,
    })


# ---------------------------------------------------------------------------
# C1: 右端截尾边界（绝对计数锁定 n-horizon）
# ---------------------------------------------------------------------------

def _smooth_uptrend_df(n):
    """全有效价位 fixture：平滑上升 + OHLC 振幅（ATR>0），价位从 t=19 起非 None。

    derive_price_levels 仅依赖 rolling MA20 / 20根 swing-low / ATR14（不依赖 swing pivot），
    平滑趋势即可让 t>=19 全部产出有效 stop/target；high>low 保证 ATR>0（避免退化为 None）。
    该「t>=19 非 None」由 test_eval_right_edge_absolute_count 的绝对计数断言自守：若 fixture
    退化致某 bar 价位 None，baseline 计数将低于期望、测试立即变红，故无需单独 assert。
    """
    close = np.linspace(50.0, 50.0 + 0.4 * n, n)
    high = close + 0.5
    low = close - 0.5
    open_ = close - 0.1
    vol = np.full(n, 1000.0)
    dates = pd.date_range("2024-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
    return pd.DataFrame({"date": dates, "open": open_, "high": high,
                         "low": low, "close": close, "volume": vol})


@pytest.mark.parametrize("n,h,expected", [(50, 10, 0), (51, 10, 1), (120, 10, 70), (120, 5, 75)])
def test_eval_right_edge_absolute_count(n, h, expected):
    """C1: 评估上界为 n-horizon，每个被评估 bar 都有完整 horizon 前瞻。

    全有效价位 fixture 下，baseline 计数 == max(0, (n-h)-min_history)（min_history=40）。
    绝对计数对上界 off-by-one 敏感：n-1（旧）与 n-h±1 都会被 (50,10)/(51,10) 边界例 + (120,*) 检出。
    (51,10)→1 顺带证明唯一被评估 bar t=40 的前瞻 df.iloc[41:51] 恰 10 根（末根满 horizon）；
    (50,10)→0 证明不足完整 horizon 的 bar 一律不评估。
    """
    df = _smooth_uptrend_df(n)
    base = evaluate_baseline_outcomes(df, market="cn", horizon=h)
    assert len(base) == expected
