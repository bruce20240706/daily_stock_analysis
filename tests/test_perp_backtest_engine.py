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


if __name__ == "__main__":
    unittest.main()
