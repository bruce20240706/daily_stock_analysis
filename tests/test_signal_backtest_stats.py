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
    # independent Wilson pin: ci_low must match wilson_ci(6,8) directly
    lo, _ = wilson_ci(6, 8)
    assert abs(s.ci_low - lo) < 1e-9
    # excess is rounded: round(ci_low - baseline, 4)
    assert abs(s.excess - round(lo - 0.5, 4)) < 1e-9
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
    # fixture: 3 wins + 3 losses → win_rate=0.5; baseline 4w/10n → baseline_win_rate=0.4
    outs = [SignalOutcome("macd_cross", "us", "win")] * 3 + \
           [SignalOutcome("macd_cross", "us", "loss")] * 3
    base = [SignalOutcome("__baseline__", "us", "win")] * 4 + \
           [SignalOutcome("__baseline__", "us", "loss")] * 6
    stats = aggregate_signal_stats(outs, base, horizon=20, interval="1w")
    s = next(x for x in stats if x.signal_type == "macd_cross")
    assert isinstance(s, SignalStat)
    assert s.interval == "1w" and s.horizon == 20
    assert s.sample == 6
    # numeric pins
    assert abs(s.win_rate - 0.5) < 1e-9
    assert abs(s.baseline_win_rate - 0.4) < 1e-9
    lo, hi = wilson_ci(3, 6)
    assert abs(s.ci_low - lo) < 1e-9
    assert abs(s.ci_high - hi) < 1e-9
    assert s.excess is not None
    assert abs(s.excess - round(lo - 0.4, 4)) < 1e-9


# =====================================================================
# Inc 1c: family-wise Bonferroni-CI 多重检验校正(spec §4.2/§4.3/§7)
# =====================================================================
from src.services.signal_backtest import bonferroni_z


def _cells(*spec_triples):
    """(signal_type, win, loss) 列表 → cn 市场 SignalOutcome 列表。"""
    outs = []
    for st, w, l in spec_triples:
        outs += [SignalOutcome(st, "cn", "win")] * w + [SignalOutcome(st, "cn", "loss")] * l
    return outs


_BASE_CN_50 = [SignalOutcome("__baseline__", "cn", "win")] * 5 + \
              [SignalOutcome("__baseline__", "cn", "loss")] * 5   # baseline=0.50


def test_bonferroni_z_values():
    # N<=1 → 字面量 1.96(byte-identical 承重;禁 inv_cdf(0.975)=1.95996…)
    assert bonferroni_z(0) == 1.96
    assert bonferroni_z(1) == 1.96
    # N=2/20 双尾均摊 pin(手算 NormalDist().inv_cdf)
    assert abs(bonferroni_z(2) - 2.2414027276049464) < 1e-12
    assert abs(bonferroni_z(20) - 3.0233414397391534) < 1e-12


def test_corrected_equals_raw_for_single_cell_family():
    """§7.1 退化 N=1 byte-identical:win=6/sample=8(0<win<sample)钉死判别式。

    wilson_ci(6,8,1.96)[0]=0.40926987… 与 wilson_ci(6,8,inv_cdf(0.975))[0]=0.40927543…
    第 5 位差开 → 精确 == 真能证伪"N<=1 误用 inv_cdf(0.975)"。win=0 clamp 到 0 恒等,禁用。
    """
    stats = aggregate_signal_stats(
        _cells(("volume_breakout", 6, 2)), _BASE_CN_50, horizon=10, min_sample=5)
    s = stats[0]
    assert s.family_size == 1
    assert s.ci_low_corrected == s.ci_low                       # 精确 ==,非 approx
    assert abs(s.ci_low_corrected - 0.40926987910258916) < 1e-12  # pin 1.96 字面量


def test_family_zero_no_crash_and_degenerates():
    """§7.5 N=0:全格 sample<min_sample → 不崩、family_size=0、校正值==raw。"""
    stats = aggregate_signal_stats(
        _cells(("a", 2, 2), ("b", 3, 1)), _BASE_CN_50, horizon=10,
        min_sample=10, fwer_alpha=0.0001)
    assert all(s.family_size == 0 for s in stats)
    for s in stats:
        assert s.ci_low_corrected == s.ci_low       # N=0 → z=1.96 → 恒等
        assert s.ci_low_corrected is not None


def test_correction_bites_at_n20():
    """§7.2 咬合:N=20、边界格子 raw>baseline 但校正后≤baseline;强格子仍>。"""
    fillers = [(f"filler_{i}", 5, 5) for i in range(18)]        # 18×sample=10
    outs = _cells(*fillers, ("edge", 15, 5), ("strong", 90, 10))  # N=20
    stats = aggregate_signal_stats(outs, _BASE_CN_50, horizon=10, min_sample=10)
    by = {s.signal_type: s for s in stats}
    assert by["edge"].family_size == 20
    z20 = bonferroni_z(20)
    # 边界格子:raw 0.5313 > 0.5 但 corr 0.4167 ≤ 0.5(手算 pin)
    assert by["edge"].ci_low > 0.5
    assert by["edge"].ci_low_corrected <= 0.5
    assert abs(by["edge"].ci_low_corrected - wilson_ci(15, 20, z20)[0]) < 1e-12
    # 强格子:校正后仍 > baseline(0.7734)
    assert by["strong"].ci_low_corrected > 0.5


def test_correction_bites_at_n2():
    """§7.3 N=2 首次生效点:z=2.2414,同一边界格子 corr 0.4994 ≤ 0.5。"""
    outs = _cells(("filler_0", 5, 5), ("edge", 15, 5))
    stats = aggregate_signal_stats(outs, _BASE_CN_50, horizon=10, min_sample=10)
    edge = next(s for s in stats if s.signal_type == "edge")
    assert edge.family_size == 2
    assert edge.ci_low > 0.5 and edge.ci_low_corrected <= 0.5
    assert abs(edge.ci_low_corrected - wilson_ci(15, 20, bonferroni_z(2))[0]) < 1e-12


def test_alpha_cap_only_tightens_incl_out_of_domain():
    """§7.4 "只收紧"不变式:域内遍历 + 域外 0.5 被纯函数内钳制(不可绕过)。"""
    for n in (2, 5, 20):
        for alpha in (0.0001, 0.01, 0.05, 0.5):     # 0.5 为域外,应钳到 0.05
            z = bonferroni_z(n, alpha)
            assert z >= 1.96, f"反转陷阱: n={n} alpha={alpha} z={z}"
    assert bonferroni_z(2, 0.5) == bonferroni_z(2, 0.05)     # 域外钳制到上限
    assert bonferroni_z(2, 0.0) == bonferroni_z(2, 0.0001)   # 0 钳到下限,不进 inv_cdf(1.0)
    # aggregate 级:每格 ci_low_corrected ≤ ci_low
    outs = _cells(*[(f"t{i}", 6, 4) for i in range(5)])
    for s in aggregate_signal_stats(outs, _BASE_CN_50, horizon=10, min_sample=10):
        assert s.ci_low_corrected <= s.ci_low


def test_alpha_monotone_stricter_when_smaller():
    """§7.10 域内单调:alpha 越小 → z 越大 → 校正下界越低。"""
    outs = _cells(*[(f"t{i}", 6, 4) for i in range(9)], ("probe", 15, 5))
    lows = []
    for alpha in (0.05, 0.01, 0.0001):
        stats = aggregate_signal_stats(outs, _BASE_CN_50, horizon=10,
                                       min_sample=10, fwer_alpha=alpha)
        lows.append(next(s for s in stats if s.signal_type == "probe").ci_low_corrected)
    assert lows[0] > lows[1] > lows[2]
