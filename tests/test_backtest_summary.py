# -*- coding: utf-8 -*-
"""Unit tests for BacktestEngine.compute_summary()."""

import unittest
from dataclasses import dataclass

from src.core.backtest_engine import BacktestEngine


@dataclass
class FakeRow:
    eval_status: str = "completed"
    position_recommendation: str = "long"
    outcome: str = "win"
    direction_correct: bool | None = True
    stock_return_pct: float | None = 1.0
    simulated_return_pct: float | None = 1.0
    hit_stop_loss: bool | None = False
    hit_take_profit: bool | None = False
    first_hit: str | None = "neither"
    first_hit_trading_days: int | None = None
    operation_advice: str | None = "买入"


class BacktestSummaryTestCase(unittest.TestCase):
    def test_trigger_rates_use_applicable_denominators(self) -> None:
        # One row has stop-loss configured, one row doesn't.
        rows = [
            FakeRow(hit_stop_loss=True, hit_take_profit=None, first_hit="stop_loss"),
            FakeRow(hit_stop_loss=None, hit_take_profit=True, first_hit="take_profit"),
        ]

        summary = BacktestEngine.compute_summary(
            results=rows,
            scope="stock",
            code="600519",
            eval_window_days=3,
            engine_version="v1",
        )

        # stop_loss_trigger_rate denominator should be 1 (only applicable row)
        self.assertEqual(summary["stop_loss_trigger_rate"], 100.0)

        # take_profit_trigger_rate denominator should be 1 (only applicable row)
        self.assertEqual(summary["take_profit_trigger_rate"], 100.0)

        # ambiguous_rate denominator should be 2 (any target applicable)
        self.assertEqual(summary["ambiguous_rate"], 0.0)


class RiskMetricsWiringTestCase(unittest.TestCase):
    def test_compute_summary_emits_risk_metrics_and_preserves_existing(self) -> None:
        rows = [
            FakeRow(simulated_return_pct=2.0, position_recommendation="long"),
            FakeRow(simulated_return_pct=-1.0, position_recommendation="long"),
            FakeRow(simulated_return_pct=0.0, position_recommendation="cash"),  # 应被风险指标排除
        ]
        summary = BacktestEngine.compute_summary(
            results=rows, scope="overall", code="__OVERALL__",
            eval_window_days=3, engine_version="v1",
        )
        rm = summary["diagnostics"]["risk_metrics"]
        self.assertEqual(rm["sample"], 2)                      # cash 不计
        self.assertEqual(rm["mean_return_pct"], 0.5)           # (2 + -1)/2
        self.assertIn("sharpe", rm)
        self.assertIn("max_drawdown_pct", rm)
        # 既有字段不回归:cash 仍计入 avg / 既有 diagnostics 键仍在
        self.assertIsNotNone(summary["avg_simulated_return_pct"])
        self.assertIn("eval_status", summary["diagnostics"])


if __name__ == "__main__":
    unittest.main()
