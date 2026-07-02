# -*- coding: utf-8 -*-
"""BacktestEngine._compute_risk_metrics 单元测试(不年化风险指标 + 事件净值 maxDD)。"""
from datetime import date

import pytest

from src.core.backtest_engine import BacktestEngine


class _R:
    """轻量 duck-typed result stub(仅带风险指标需要的字段)。"""
    def __init__(self, ret, pos="long", d=None, code="X"):
        self.eval_status = "completed"
        self.position_recommendation = pos
        self.simulated_return_pct = ret
        self.analysis_date = d
        self.code = code


def _rm(rows):
    return BacktestEngine._compute_risk_metrics(rows)


def test_exact_values():
    d = [date(2024, 1, i) for i in (1, 2, 3, 4)]
    m = _rm([_R(2, d=d[0]), _R(-1, d=d[1]), _R(3, d=d[2]), _R(-2, d=d[3])])
    assert m["sample"] == 4
    assert m["mean_return_pct"] == 0.5
    assert m["worst_single_return_pct"] == -2.0
    assert m["return_std_pct"] == pytest.approx(2.3805, abs=1e-4)
    assert m["sharpe"] == pytest.approx(0.2100, abs=1e-4)
    assert m["sortino"] == pytest.approx(0.4472, abs=1e-4)
    assert m["max_drawdown_pct"] == pytest.approx(2.0, abs=1e-4)
    assert m["equity_final_pct"] == pytest.approx(1.9292, abs=1e-4)


def test_sharpe_guard_uses_raw_std_not_rounded():
    # 原始 std 微小非零(round_std=0.0000)时 sharpe 仍非 None(证守卫用原始 std)
    m = _rm([_R(1.00001), _R(1.0)])
    assert m["return_std_pct"] == 0.0
    assert m["sharpe"] is not None and m["sharpe"] != 0


def test_cash_excluded_perp_short_included():
    rows = [_R(2, pos="long"), _R(0.0, pos="cash"), _R(-3, pos="short"), _R(0.0, pos="cash")]
    m = _rm(rows)
    assert m["sample"] == 2                       # 仅 long + short,cash 不计
    assert m["mean_return_pct"] == pytest.approx(-0.5, abs=1e-9)  # (2 + -3)/2
    assert m["worst_single_return_pct"] == -3.0


def test_maxdd_ordering_determinism_and_reject_oracle():
    # 反例 oracle:选路径敏感的收益集,输入原序与日期序的 maxDD 明确不同,证实现确实按日期排序。
    # [+100,-60,-60](日期序)maxDD=84;[-60,+100,-60](输入乱序)maxDD=68。
    d1, d2, d3 = date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)
    date_order = [_R(100, d=d1), _R(-60, d=d2), _R(-60, d=d3)]
    input_order = [_R(-60, d=d2), _R(100, d=d1), _R(-60, d=d3)]   # 打乱输入,日期不变
    maxdd_by_input_naive = _naive_input_order_maxdd([-60, 100, -60])   # 68.0
    maxdd_by_date = _naive_input_order_maxdd([100, -60, -60])          # 84.0
    assert maxdd_by_input_naive != pytest.approx(maxdd_by_date, abs=1e-6)  # 乱序确会变(非 tautology)
    assert _rm(input_order)["max_drawdown_pct"] == pytest.approx(maxdd_by_date, abs=1e-4)
    assert _rm(date_order)["max_drawdown_pct"] == pytest.approx(maxdd_by_date, abs=1e-4)
    # overall 同日并列:同 date 不同 code 须按 code 排序(非输入序/非 index 回退)才得正确 maxDD。
    # 3 行同日:code 升序 A,B,C=[+100,-50,-50]→maxDD 75;输入/index 序 C,A,B=[-50,+100,-50]→50。
    same_date_input = [_R(-50, d=d1, code="C"), _R(100, d=d1, code="A"), _R(-50, d=d1, code="B")]
    maxdd_code_order = _naive_input_order_maxdd([100, -50, -50])    # 75.0(按 code 升序 A,B,C)
    maxdd_index_order = _naive_input_order_maxdd([-50, 100, -50])   # 50.0(按输入/index 序 C,A,B)
    assert maxdd_code_order != pytest.approx(maxdd_index_order, abs=1e-6)  # 两序确不同(非 tautology)
    assert _rm(same_date_input)["max_drawdown_pct"] == pytest.approx(maxdd_code_order, abs=1e-4)
    # 打乱输入,同一 (date,code) 集合 → 结果不变(determinism)
    same_date_shuffled = [_R(100, d=d1, code="A"), _R(-50, d=d1, code="C"), _R(-50, d=d1, code="B")]
    assert _rm(same_date_input)["max_drawdown_pct"] == _rm(same_date_shuffled)["max_drawdown_pct"]


def _naive_input_order_maxdd(returns):
    equity, peak, maxdd = 1.0, 1.0, 0.0
    for ri in returns:
        equity *= (1 + max(ri, -100.0) / 100.0)
        peak = max(peak, equity)
        maxdd = max(maxdd, (peak - equity) / peak)
    return round(maxdd * 100, 4)


def test_guards():
    assert _rm([])["sample"] == 0
    assert _rm([])["sharpe"] is None and _rm([])["max_drawdown_pct"] is None
    one = _rm([_R(3, d=date(2024, 1, 1))])
    assert one["return_std_pct"] is None and one["sharpe"] is None and one["sortino"] is None
    assert one["mean_return_pct"] == 3.0 and one["max_drawdown_pct"] == 0.0
    flat = _rm([_R(2), _R(2), _R(2)])
    assert flat["sharpe"] is None                 # std==0
    assert flat["sortino"] is None                # 无负收益 downside_dev==0
    allpos = _rm([_R(1), _R(2), _R(3)])
    assert allpos["sortino"] is None


def test_perp_short_floor_and_maxdd_saturation():
    # L=1 perp short 原始 -200 → 下钳 -100;单笔 -100 使 maxDD 饱和 100%
    single = _rm([_R(-200, pos="short", d=date(2024, 1, 1))])
    assert single["worst_single_return_pct"] == -100.0
    assert single["max_drawdown_pct"] == pytest.approx(100.0, abs=1e-4)
    assert single["equity_final_pct"] == pytest.approx(-100.0, abs=1e-4)
    mid = _rm([_R(5, d=date(2024, 1, 1)), _R(-100, d=date(2024, 1, 2)), _R(5, d=date(2024, 1, 3))])
    assert mid["max_drawdown_pct"] == pytest.approx(100.0, abs=1e-4)
    assert mid["equity_final_pct"] == pytest.approx(-100.0, abs=1e-4)


# --- 链路B 风险画像:共享数学抽取的 golden 全等锁(spec §4.4/§7.4,永久回归) ---
import json as _json
import random as _random
from types import SimpleNamespace as _NS

from src.core.backtest_engine import risk_metrics_from_returns


def _golden_rows():
    rows = [_NS(position_recommendation="long", simulated_return_pct=r, analysis_date=d, code=c)
            for r, d, c in [(5.2, "2026-01-02", "A"), (-3.1, "2026-01-02", "B"),
                            (12.0, "2026-01-03", "A"), (-8.4, "2026-01-05", "C"),
                            (0.0, "2026-01-04", "B"), (2.5, None, "D")]]
    rng = _random.Random(42)   # seed 固定,离线确定性
    rows += [_NS(position_recommendation="long",
                 simulated_return_pct=round(rng.uniform(-30, 30), 4),
                 analysis_date=f"2026-02-{i+1:02d}", code=f"R{i%3}")
             for i in range(20)]
    return rows


# 由重构前实现生成(Step 1 脚本输出逐字粘贴)——重构后必须逐位/逐键序全等
_GOLDEN_JSON = '{"sample": 26, "mean_return_pct": -4.8157, "return_std_pct": 15.9153, "sharpe": -0.3026, "sortino": -0.3354, "max_drawdown_pct": 83.5306, "equity_final_pct": -80.7265, "worst_single_return_pct": -29.6101, "note": "信号收益序列风险画像(排除 cash;收益下钳≥-100;每信号独立、窗口可重叠、无仓位管理;非真实组合 maxDD;单笔=-100 会使 maxDD 饱和 100%)"}'


def test_compute_risk_metrics_golden_byte_equivalence():
    out = BacktestEngine._compute_risk_metrics(_golden_rows())
    assert _json.dumps(out, ensure_ascii=False) == _GOLDEN_JSON


def test_risk_metrics_from_returns_direct():
    # 空/单元素
    empty = risk_metrics_from_returns([])
    assert empty["sample"] == 0 and empty["sharpe"] is None
    one = risk_metrics_from_returns([5.0])
    assert one["sample"] == 1 and one["sharpe"] is None and one["worst_single_return_pct"] == 5.0
    # 已知序列 pin(手算):[10, -10] → mean=0, std=stdev=14.1421..., sharpe=0.0
    two = risk_metrics_from_returns([10.0, -10.0])
    assert two["mean_return_pct"] == 0.0
    assert abs(two["return_std_pct"] - 14.1421) < 1e-4
    assert two["sharpe"] == 0.0
    # maxDD:equity 1.1*0.9=0.99,peak 1.1 → dd=(1.1-0.99)/1.1=10%
    assert abs(two["max_drawdown_pct"] - 10.0) < 1e-4


def test_pairwise_nonfinite_filter_keeps_alignment():
    """NEW-3 回归锚:非有限值成对剔除后 returns 与 sort_keys 仍对齐(maxDD 序正确)。"""
    returns = [10.0, float("nan"), -10.0]
    keys = [(False, "2026-01-03", 0), (False, "2026-01-01", 1), (False, "2026-01-02", 2)]
    out = risk_metrics_from_returns(returns, sort_keys=keys)
    assert out["sample"] == 2                       # NaN 成对剔除
    # 排序后遍历序=[-10(01-02), +10(01-03)]:equity 0.9→0.99,peak 峰值 1.0 → maxDD=10%
    assert abs(out["max_drawdown_pct"] - 10.0) < 1e-4


def test_sort_keys_affect_only_maxdd_not_moments():
    # 注:brief 原始数据 [20,-15,5] 为单负值构造——无论正值先后,单次 -15% 跌幅前必为局部峰值,
    # maxDD 恒为 15%(与序无关),属 tautology,已用户选修换为与既有
    # test_maxdd_ordering_determinism_and_reject_oracle 同源的双负值非对称数据集
    # [100,-60,-60](复利耦合负值对序敏感,84 vs 68 已由既有测试证实非 tautology)。
    returns = [100.0, -60.0, -60.0]
    a = risk_metrics_from_returns(returns, sort_keys=[(False, "m", 0), (False, "a", 1), (False, "z", 2)])
    b = risk_metrics_from_returns(returns, sort_keys=None)
    assert a["mean_return_pct"] == b["mean_return_pct"]
    assert a["sharpe"] == b["sharpe"]
    assert a["max_drawdown_pct"] != b["max_drawdown_pct"]   # 序不同 maxDD 必不同(3 元非对称构造)
