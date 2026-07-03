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


# =====================================================================
# 链路B 风险画像:per-cell risk_metrics 聚合(spec §4.3/§7.5/§7.6)
# =====================================================================


def _o(sig, mkt, outcome, ret=None, date=None):
    return SignalOutcome(sig, mkt, outcome, return_pct=ret, date=date)


def test_cell_risk_metrics_includes_expired_and_counts_excluded():
    outs = [
        _o("volume_breakout", "cn", "win", 10.0, "2026-01-02"),
        _o("volume_breakout", "cn", "loss", -5.0, "2026-01-03"),
        _o("volume_breakout", "cn", "expired", 2.0, "2026-01-04"),   # D8:expired 计入
        _o("volume_breakout", "cn", "win", None, "2026-01-05"),      # 失真 win(excluded)
        _o("volume_breakout", "cn", "loss", None, "2026-01-06"),     # 失真 loss(excluded)
    ]
    base = [SignalOutcome("__baseline__", "cn", "win")] * 5 + [SignalOutcome("__baseline__", "cn", "loss")] * 5
    stats = aggregate_signal_stats(outs, base, horizon=10, interval="5m", min_sample=3)
    s = stats[0]
    rm = s.risk_metrics
    assert rm is not None
    assert rm["sample"] == 3                     # 10,-5,2(expired 计入;两 None 剔除)
    assert rm["excluded"] == 2                   # 失真 win + 失真 loss 都计
    assert rm["interval"] == "5m" and rm["horizon"] == 10
    assert "note" not in rm                      # D5:note 不入 dict
    # 胜率分母不变式:sample(win+loss)=4,与 risk sample=3 不定序共存
    assert s.sample == 4


def test_all_excluded_cell_still_gets_full_dict_not_none():
    """NEW-4 回归锚:100% 剔除时落全键 dict(sample=0/excluded=N),None 仅 legacy 一义。"""
    outs = [_o("x", "cn", "win", None, "2026-01-02"), _o("x", "cn", "loss", None, "2026-01-03")]
    base = [SignalOutcome("__baseline__", "cn", "win")]
    stats = aggregate_signal_stats(outs, base, horizon=10)
    rm = stats[0].risk_metrics
    assert rm is not None
    assert rm["sample"] == 0 and rm["excluded"] == 2
    assert rm["sharpe"] is None and rm["max_drawdown_pct"] is None


def test_cell_maxdd_uses_date_order_not_input_order():
    """maxDD 排序判别式(3 元非对称):date 序 ≠ 输入序时 maxDD 不同;mean 不随序变。"""
    # date 序:-50, +100, -50 → equity 0.5→1.0→0.5,maxDD=50%
    # 输入序:+100, -50, -50 → equity 2.0→1.0→0.5,maxDD=75%
    outs = [
        _o("x", "cn", "win", 100.0, "2026-01-02"),
        _o("x", "cn", "loss", -50.0, "2026-01-01"),
        _o("x", "cn", "loss", -50.0, "2026-01-03"),
    ]
    base = [SignalOutcome("__baseline__", "cn", "win")]
    rm = aggregate_signal_stats(outs, base, horizon=10)[0].risk_metrics
    assert abs(rm["max_drawdown_pct"] - 50.0) < 1e-4      # date 序生效(输入序会是 75%)
    assert abs(rm["mean_return_pct"] - 0.0) < 1e-4        # moment 不随序变


def test_dates_all_none_falls_back_to_input_order_no_typeerror():
    outs = [_o("x", "cn", "win", 100.0), _o("x", "cn", "loss", -50.0), _o("x", "cn", "loss", -50.0)]
    base = [SignalOutcome("__baseline__", "cn", "win")]
    rm = aggregate_signal_stats(outs, base, horizon=10)[0].risk_metrics
    assert abs(rm["max_drawdown_pct"] - 75.0) < 1e-4      # 输入序


def test_legacy_signaloutcome_construction_still_works():
    o = SignalOutcome("x", "cn", "win")                    # 既有三参构造零破坏
    assert o.return_pct is None and o.date is None


# =====================================================================
# 链路B OOS holdout 切分(spec §3,Inc 1e)
# =====================================================================


def _b(mkt, date, end=None, outcome="win"):
    return SignalOutcome("__baseline__", mkt, outcome, date=date, window_end_date=end)


def _sig(mkt, date, end=None, outcome="win", st="volume_breakout"):
    return SignalOutcome(st, mkt, outcome, date=date, window_end_date=end)


def test_cutoff_quantile_kills_floor_variants_and_no_date_parsing():
    """审查 F2:两组算例各杀 len 基/round 变体;非日期 token 证纯字典序无解析。"""
    from src.services.signal_backtest import _derive_market_cutoffs
    base = [_b("cn", d) for d in ["d1", "d2", "d3", "d4", "d5"]]
    assert _derive_market_cutoffs(base, 0.25)["cn"] == "d4"   # floor(0.75*4)=3;len 基变体 floor(3.75)-1=2 → d3 被杀
    assert _derive_market_cutoffs(base, 0.3)["cn"] == "d3"    # floor(2.8)=2;round 变体 round(2.8)=3 → d4 被杀


def test_dual_market_heterogeneous_span_uses_own_cutoff():
    """审查 STAT-1 回归锚:异构跨度双市场各用自己的分位;全局池化会把短跨度市场 train 清空。"""
    from src.services.signal_backtest import _derive_market_cutoffs
    base = [_b("us", f"a{i:03d}", f"a{min(i + 2, 99):03d}") for i in range(100)]
    base += [_b("cn", f"b{i:03d}", f"b{min(i + 2, 19):03d}",
                "win" if i % 2 else "loss") for i in range(20)]
    cutoffs = _derive_market_cutoffs(base, 0.2)
    assert cutoffs["us"] == "a079" and cutoffs["cn"] == "b015"   # 各自 floor(0.8*(len-1));全局池化会落 a095

    outs = [_sig("cn", f"b{i:03d}", f"b{i + 2:03d}") for i in range(12)]
    stats = aggregate_signal_stats(outs, base, horizon=10, oos_fraction=0.2)
    rep = next(s for s in stats if s.market == "cn").oos
    assert rep["cutoff_date"] == "b015"
    assert rep["train"]["sample"] > 0            # 全局池化下 cn 全部日期 > a095 → train 必空,此断言即判别式


def test_split_conservation_embargo_and_expired_excluded():
    """审查 STAT-4:守恒恒等式承重;expired 不进任何计数;跨切点窗必 embargo;end 缺失保守归 embargo。"""
    base = []
    dates = [f"c{i:02d}" for i in range(10)]                     # c00..c09,f=0.3 → floor(0.7*9)=6 → cutoff=c06
    for i, d in enumerate(dates):
        end = dates[min(i + 2, 9)]
        base.append(_b("cn", d, end, "win" if i % 2 == 0 else "loss"))
    outs = [
        _sig("cn", "c01", "c03", "win"),      # train
        _sig("cn", "c02", "c04", "loss"),     # train
        _sig("cn", "c05", "c08", "win"),      # embargo:date≤c06<end(泄漏回归锚)
        _sig("cn", "c03", None, "loss"),      # embargo:end 缺失且 date≤cutoff 保守归类(#4)
        _sig("cn", "c07", "c09", "win"),      # OOS
        _sig("cn", "c08", "c09", "loss"),     # OOS
        _sig("cn", None, None, "win"),        # undated
        _sig("cn", "c01", "c03", "expired"),  # expired:不进任何 OOS 计数
    ]
    s = aggregate_signal_stats(outs, base, horizon=10, oos_fraction=0.3)[0]
    rep = s.oos
    assert rep["cutoff_date"] == "c06" and rep["fraction"] == 0.3
    assert rep["train"]["sample"] == 2 and rep["oos"]["sample"] == 2
    assert rep["embargoed"] == 2 and rep["undated"] == 1
    # 守恒恒等式(win/loss 宇宙的精确划分):
    assert rep["train"]["sample"] + rep["oos"]["sample"] + rep["embargoed"] + rep["undated"] == s.sample == 7
    # 两段子统计手算 pin:
    # baseline train=c00..c04(end≤c06)=3W2L→0.6;baseline OOS=c07,c08,c09=1W2L→0.3333
    assert abs(rep["train"]["win_rate"] - 0.5) < 1e-9
    assert abs(rep["train"]["baseline_win_rate"] - 0.6) < 1e-9
    assert abs(rep["train"]["excess"] - (-0.1)) < 1e-9
    assert abs(rep["oos"]["win_rate"] - 0.5) < 1e-9
    assert abs(rep["oos"]["baseline_win_rate"] - 0.3333) < 1e-9
    assert abs(rep["oos"]["excess"] - 0.1667) < 1e-9            # round(0.5-1/3, 4):raw 相减后 round4


def test_zero_sample_segment_yields_none_not_crash():
    base = [_b("cn", f"e{i}", f"e{min(i + 1, 4)}") for i in range(5)]   # e0..e4,f=0.5→floor(0.5*4)=2→cutoff=e2
    outs = [_sig("cn", "e3", "e4", "win")]                               # 仅 OOS 一件,train 空
    rep = aggregate_signal_stats(outs, base, horizon=10, oos_fraction=0.5)[0].oos
    assert rep["train"]["sample"] == 0 and rep["train"]["win_rate"] is None
    assert rep["train"]["excess"] is None


def test_degenerate_market_coexists_with_normal_market():
    """§7.6:某市场 distinct 日期<2 → 退化 dict;同 run 其他市场正常。"""
    base = [_b("hk", "only-one-date", "only-one-date")]
    base += [_b("cn", f"g{i}", f"g{min(i + 1, 4)}") for i in range(5)]
    outs = [_sig("hk", "only-one-date", None), _sig("cn", "g3", "g4")]
    stats = aggregate_signal_stats(outs, base, horizon=10, oos_fraction=0.3)
    hk = next(s for s in stats if s.market == "hk").oos
    cn = next(s for s in stats if s.market == "cn").oos
    assert hk == {"cutoff_date": None, "fraction": 0.3, "degenerate": True}
    assert cn["cutoff_date"] is not None and "train" in cn


def test_headline_stats_invariant_and_inputs_not_mutated():
    """§7.7:f>0 不改任何既有字段;输入列表未被变异(id/顺序/元素同一)。"""
    base = [_b("cn", f"h{i:02d}", f"h{min(i + 2, 9):02d}", "win" if i % 2 else "loss") for i in range(10)]
    outs = [_sig("cn", f"h{i:02d}", f"h{min(i + 2, 9):02d}", "win" if i < 4 else "loss") for i in range(8)]
    snap_outs, snap_base = list(outs), list(base)
    s0 = aggregate_signal_stats(outs, base, horizon=10)[0]
    s1 = aggregate_signal_stats(outs, base, horizon=10, oos_fraction=0.3)[0]
    assert all(a is b for a, b in zip(outs, snap_outs)) and len(outs) == len(snap_outs)
    assert all(a is b for a, b in zip(base, snap_base)) and len(base) == len(snap_base)
    for f in ("signal_type", "market", "win", "loss", "sample", "win_rate",
              "ci_low", "ci_high", "baseline_win_rate", "excess",
              "ci_low_corrected", "family_size", "risk_metrics"):
        assert getattr(s0, f) == getattr(s1, f), f
    assert s0.oos is None and s1.oos is not None


def test_function_side_clamp_and_early_exit():
    """审查 F7:函数侧二次钳;fraction 键落钳后值;负值走 f=0 早退。"""
    base = [_b("cn", f"k{i}", f"k{min(i + 1, 5)}") for i in range(6)]
    outs = [_sig("cn", "k1", "k2")]
    rep09 = aggregate_signal_stats(outs, base, horizon=10, oos_fraction=0.9)[0].oos
    rep05 = aggregate_signal_stats(outs, base, horizon=10, oos_fraction=0.5)[0].oos
    assert rep09 == rep05 and rep09["fraction"] == 0.5
    assert aggregate_signal_stats(outs, base, horizon=10, oos_fraction=-0.1)[0].oos is None
