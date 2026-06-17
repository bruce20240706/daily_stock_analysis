# -*- coding: utf-8 -*-
"""Task A2: 统计聚合 + Wilson CI + 基准超额 测试。"""
from src.services.signal_backtest import wilson_ci, aggregate_signal_stats, SignalOutcome, SignalStat


def test_wilson_ci_bounds_within_0_1_and_low_below_high():
    lo, hi = wilson_ci(7, 10)
    assert 0.0 <= lo < hi <= 1.0


def test_wilson_ci_small_sample_is_wide():
    lo_small, hi_small = wilson_ci(1, 2)
    lo_big, hi_big = wilson_ci(50, 100)
    assert (hi_small - lo_small) > (hi_big - lo_big)


def test_aggregate_groups_by_type_and_market_excludes_expired_from_sample():
    outs = [SignalOutcome("volume_breakout", "cn", "win")] * 6 + \
           [SignalOutcome("volume_breakout", "cn", "loss")] * 2 + \
           [SignalOutcome("volume_breakout", "cn", "expired")] * 5
    base = [SignalOutcome("__baseline__", "cn", "win")] * 5 + [SignalOutcome("__baseline__", "cn", "loss")] * 5
    stats = aggregate_signal_stats(outs, base, horizon=10)
    s = next(x for x in stats if x.signal_type == "volume_breakout" and x.market == "cn")
    assert s.win == 6 and s.loss == 2 and s.sample == 8        # expired 不计入
    assert abs(s.win_rate - 0.75) < 1e-9
    assert abs(s.baseline_win_rate - 0.5) < 1e-9
    assert abs(s.excess - (s.ci_low - 0.5)) < 1e-9
    assert s.interval == "1d" and s.horizon == 10


def test_aggregate_baseline_per_market():
    outs = [SignalOutcome("x", "crypto", "win")]
    base = [SignalOutcome("__baseline__", "crypto", "win"), SignalOutcome("__baseline__", "cn", "loss")]
    stats = aggregate_signal_stats(outs, base, horizon=5)
    s = next(x for x in stats if x.signal_type == "x")
    assert s.market == "crypto" and abs(s.baseline_win_rate - 1.0) < 1e-9  # 只用同 market 的 baseline


def test_wilson_ci_n_zero_returns_zero_tuple():
    assert wilson_ci(0, 0) == (0.0, 0.0)


def test_wilson_ci_all_wins():
    lo, hi = wilson_ci(10, 10)
    assert 0.0 <= lo < hi <= 1.0


def test_aggregate_returns_signal_stat_instances():
    outs = [SignalOutcome("macd_cross", "us", "win")] * 3 + \
           [SignalOutcome("macd_cross", "us", "loss")] * 3
    base = [SignalOutcome("__baseline__", "us", "win")] * 4 + \
           [SignalOutcome("__baseline__", "us", "loss")] * 6
    stats = aggregate_signal_stats(outs, base, horizon=20, interval="1w")
    s = next(x for x in stats if x.signal_type == "macd_cross")
    assert isinstance(s, SignalStat)
    assert s.interval == "1w" and s.horizon == 20
    assert s.sample == 6
