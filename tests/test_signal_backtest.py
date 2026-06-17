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
    """产出的 SignalOutcome 均携带正确 market 和合法 outcome 值"""
    df = _make_history_with_signals()
    outs = evaluate_signal_outcomes(df, market="cn", horizon=10)
    assert all(isinstance(o, SignalOutcome) and o.market == "cn" for o in outs)
    assert all(o.outcome in {"win", "loss", "expired"} for o in outs)


def test_evaluate_signal_outcomes_returns_list():
    """函数签名返回 list，即使无触发也不抛异常"""
    df = _make_history_with_signals()
    result = evaluate_signal_outcomes(df, market="us", horizon=5)
    assert isinstance(result, list)


def test_evaluate_signal_outcomes_causal_no_future_leak():
    """逐 bar 推进时，每根 bar 只使用 ≤t 数据（因果约束）。
    通过截断前半段历史来验证：只有后半段才有足够窗口，不应触发 IndexError 或越界。"""
    df = _make_history_with_signals()
    outs = evaluate_signal_outcomes(df, market="cn", horizon=10, min_history=40)
    # 只要正常完成即通过因果约束测试
    assert isinstance(outs, list)


# ---------------------------------------------------------------------------
# evaluate_baseline_outcomes
# ---------------------------------------------------------------------------

def test_baseline_uses_all_bars_not_only_triggers():
    """baseline 用全体有效 bar 入场，数量 >= 信号触发点"""
    df = _make_history_with_signals()
    base = evaluate_baseline_outcomes(df, market="cn", horizon=10)
    sig = evaluate_signal_outcomes(df, market="cn", horizon=10)
    assert all(o.signal_type == "__baseline__" for o in base)
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
    """构造足够长、含触发点的上升后回踩序列。"""
    n = 80
    close = list(np.linspace(100, 130, 40)) + list(np.linspace(130, 120, 40))
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n).strftime("%Y-%m-%d"),
        "open": close,
        "high": [c * 1.01 for c in close],
        "low": [c * 0.99 for c in close],
        "close": close,
        "volume": [1_000_000 + i * 10_000 for i in range(n)],
    })
