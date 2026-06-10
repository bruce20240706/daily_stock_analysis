# -*- coding: utf-8 -*-
"""perp 回测引擎：infer_perp_position + evaluate_single 的 is_perp/funding 分支。
现货默认路径回归见既有 lock 套件（tests/test_backtest_engine.py 等，不在此重复）。"""
import unittest
from dataclasses import dataclass
from datetime import date, timedelta

from src.core.backtest_engine import BacktestEngine, EvaluationConfig


@dataclass
class Bar:
    date: date
    high: float
    low: float
    close: float


def _bars(start: date, closes, highs=None, lows=None):
    highs = highs or closes
    lows = lows or closes
    return [Bar(date=start + timedelta(days=i + 1), high=highs[i], low=lows[i], close=closes[i])
            for i in range(len(closes))]


class InferPerpPositionTestCase(unittest.TestCase):
    def test_bearish_maps_to_short(self):
        self.assertEqual(BacktestEngine.infer_perp_position("卖出"), "short")
        self.assertEqual(BacktestEngine.infer_perp_position("strong sell"), "short")

    def test_bullish_and_hold_map_to_long(self):
        self.assertEqual(BacktestEngine.infer_perp_position("买入"), "long")
        self.assertEqual(BacktestEngine.infer_perp_position("持有"), "long")

    def test_wait_and_default_map_to_cash(self):
        self.assertEqual(BacktestEngine.infer_perp_position("观望"), "cash")
        self.assertEqual(BacktestEngine.infer_perp_position("some gibberish"), "cash")
        for advice in [None, "", "   "]:
            self.assertEqual(BacktestEngine.infer_perp_position(advice), "cash")

    def test_only_bearish_diverges_from_long_only_inference(self):
        # 除 bearish 外，与 infer_position_recommendation 完全一致
        for advice in ["买入", "持有", "观望", "震荡观望", "先观望再买入", "some gibberish", "", None]:
            perp = BacktestEngine.infer_perp_position(advice)
            spot = BacktestEngine.infer_position_recommendation(advice)
            self.assertEqual(perp, spot, f"non-bearish advice should match: {advice!r}")


class EvaluateSinglePerpTestCase(unittest.TestCase):
    def _cfg(self):
        return EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0)

    def test_perp_short_profits_on_drop_with_funding(self):
        # 看空、价格下跌：做空盈利 = (start-end)/start*100 + funding；持有至窗口末
        bars = _bars(date(2024, 1, 1), [98, 96, 95], highs=[99, 97, 96], lows=[97, 95, 94])
        res = BacktestEngine.evaluate_single(
            operation_advice="卖出", analysis_date=date(2024, 1, 1), start_price=100,
            forward_bars=bars, stop_loss=110, take_profit=90, config=self._cfg(),
            is_perp=True, funding_cost_pct=0.3,
        )
        self.assertEqual(res["position_recommendation"], "short")
        self.assertEqual(res["direction_expected"], "down")
        self.assertEqual(res["outcome"], "win")  # 跌 5% > band
        self.assertAlmostEqual(res["simulated_entry_price"], 100)
        self.assertAlmostEqual(res["simulated_exit_price"], 95)  # 窗口末收盘
        self.assertEqual(res["simulated_exit_reason"], "window_end_short")
        self.assertAlmostEqual(res["simulated_return_pct"], (100 - 95) / 100 * 100 + 0.3)  # 5.3
        self.assertIsNone(res["hit_stop_loss"])
        self.assertIsNone(res["hit_take_profit"])
        self.assertEqual(res["first_hit"], "not_applicable")

    def test_perp_long_subtracts_funding_and_matches_spot_when_zero(self):
        bars = _bars(date(2024, 1, 1), [105, 106, 107], highs=[111, 107, 108], lows=[103, 105, 106])
        kwargs = dict(operation_advice="买入", analysis_date=date(2024, 1, 1), start_price=100,
                      forward_bars=bars, stop_loss=95, take_profit=110, config=self._cfg())
        perp = BacktestEngine.evaluate_single(is_perp=True, funding_cost_pct=0.5, **kwargs)
        spot = BacktestEngine.evaluate_single(**kwargs)  # is_perp=False 默认
        # 多头 TP 命中路径与现货一致；perp 仅多扣 funding
        self.assertEqual(perp["position_recommendation"], "long")
        self.assertEqual(perp["simulated_exit_reason"], "take_profit")
        self.assertAlmostEqual(perp["simulated_return_pct"], spot["simulated_return_pct"] - 0.5)
        # funding=0 时数值等同现货
        perp0 = BacktestEngine.evaluate_single(is_perp=True, funding_cost_pct=0.0, **kwargs)
        self.assertAlmostEqual(perp0["simulated_return_pct"], spot["simulated_return_pct"])

    def test_perp_wait_is_cash_zero_return(self):
        bars = _bars(date(2024, 1, 1), [101, 102, 103], highs=[102, 103, 104], lows=[100, 101, 102])
        res = BacktestEngine.evaluate_single(
            operation_advice="观望", analysis_date=date(2024, 1, 1), start_price=100,
            forward_bars=bars, stop_loss=95, take_profit=110, config=self._cfg(),
            is_perp=True, funding_cost_pct=0.9,
        )
        self.assertEqual(res["position_recommendation"], "cash")
        self.assertEqual(res["simulated_return_pct"], 0.0)
        self.assertIsNone(res["simulated_entry_price"])

    def test_perp_error_and_insufficient_keep_short_label(self):
        cfg = self._cfg()
        # error：start_price<=0
        err = BacktestEngine.evaluate_single(
            operation_advice="卖出", analysis_date=date(2024, 1, 1), start_price=0,
            forward_bars=[], stop_loss=None, take_profit=None, config=cfg, is_perp=True,
        )
        self.assertEqual(err["eval_status"], "error")
        self.assertEqual(err["position_recommendation"], "short")
        # insufficient：bars 不足
        short_bars = _bars(date(2024, 1, 1), [98])
        insf = BacktestEngine.evaluate_single(
            operation_advice="卖出", analysis_date=date(2024, 1, 1), start_price=100,
            forward_bars=short_bars, stop_loss=None, take_profit=None, config=cfg, is_perp=True,
        )
        self.assertEqual(insf["eval_status"], "insufficient_data")
        self.assertEqual(insf["position_recommendation"], "short")

    def test_spot_default_is_unchanged_for_bearish(self):
        # is_perp=False（默认）：看空仍 cash、return 0.0（逐字不变）
        bars = _bars(date(2024, 1, 1), [98, 96, 95], highs=[99, 97, 96], lows=[97, 95, 94])
        res = BacktestEngine.evaluate_single(
            operation_advice="卖出", analysis_date=date(2024, 1, 1), start_price=100,
            forward_bars=bars, stop_loss=95, take_profit=110, config=self._cfg(),
        )
        self.assertEqual(res["position_recommendation"], "cash")
        self.assertEqual(res["simulated_return_pct"], 0.0)
        self.assertEqual(res["first_hit"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
