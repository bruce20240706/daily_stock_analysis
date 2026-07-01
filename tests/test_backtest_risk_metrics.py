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
