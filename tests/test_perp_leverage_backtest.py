# -*- coding: utf-8 -*-
"""perp 杠杆情景回测：引擎强平/放大分支、repo perp_only 粗滤、service 标签隔离、API 参数面。
L=1 与现货回归见锁套件（test_backtest_engine / test_crypto_backtest / test_backtest_summary / test_perp_backtest_engine，零改动）。"""
import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from src.config import Config
from src.core.backtest_engine import BacktestEngine, EvaluationConfig
from src.repositories.backtest_repo import BacktestRepository
from src.storage import AnalysisHistory, DatabaseManager

PERP_CODE = "BTC/USDT:PERP"


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


def _eval(advice="买入", *, bars, leverage=None, stop_loss=None, take_profit=None,
          funding=0.0, is_perp=True, start_price=100.0):
    kwargs = dict(
        operation_advice=advice, analysis_date=date(2024, 1, 1), start_price=start_price,
        forward_bars=bars, stop_loss=stop_loss, take_profit=take_profit,
        config=EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0),
        is_perp=is_perp, funding_cost_pct=funding,
    )
    if leverage is not None:
        kwargs["leverage"] = leverage
    return BacktestEngine.evaluate_single(**kwargs)


class LeverageEngineTestCase(unittest.TestCase):
    def test_l1_explicit_equals_default_per_field(self):
        # L=1 不变量（最高优先级）：显式 leverage=1 与不传 leverage 输出逐字段相等（long/short/cash 三态）
        bars = _bars(date(2024, 1, 1), [98, 96, 95], highs=[99, 97, 96], lows=[97, 95, 94])
        cases = [
            ("买入", dict(stop_loss=95.0, take_profit=110.0, funding=0.5)),
            ("卖出", dict(funding=0.3)),
            ("观望", dict(funding=0.9)),
        ]
        for advice, extra in cases:
            with self.subTest(advice=advice):
                self.assertEqual(_eval(advice, bars=bars, leverage=1, **extra),
                                 _eval(advice, bars=bars, **extra))

    def test_l1_short_does_not_liquidate_at_double_entry(self):
        # L=1 门控判别：1x 做空理论爆仓点 +100%（high≥2×entry）不模拟强平（与 E 一致）；
        # 若门控误写 leverage >= 1，本用例即挂（liq_short=200，high 210 会被误判强平）
        bars = _bars(date(2024, 1, 1), [95, 90, 85], highs=[210, 95, 90], lows=[94, 88, 84])
        res = _eval("卖出", bars=bars, leverage=1, funding=0.3)
        self.assertEqual(res["simulated_exit_reason"], "window_end_short")
        self.assertAlmostEqual(res["simulated_return_pct"], (100 - 85) / 100 * 100 + 0.3)

    def test_long_liquidation_overrides_later_tp(self):
        # L=4 → liq=75；bar1 low 74 触强平，bar2 才触 TP=110 → 强平（已出场，后续 TP 不改结果）
        bars = _bars(date(2024, 1, 1), [80, 85, 90], highs=[95, 110, 112], lows=[74, 80, 85])
        res = _eval("买入", bars=bars, leverage=4, take_profit=110.0, funding=0.5)
        self.assertEqual(res["simulated_exit_reason"], "liquidated")
        self.assertEqual(res["simulated_return_pct"], -100.0)
        self.assertAlmostEqual(res["simulated_exit_price"], 75.0)
        self.assertAlmostEqual(res["simulated_entry_price"], 100.0)
        # 预测质量字段维持既有逻辑（不感知强平）：TP 在 bar2 命中照旧记录
        self.assertTrue(res["hit_take_profit"])
        self.assertEqual(res["first_hit"], "take_profit")
        self.assertEqual(res["first_hit_trading_days"], 2)

    def test_long_exit_before_liq_touch_is_not_liquidated(self):
        # 判别用例（整窗 min/max 误实现的唯一可挂点）：bar1 触 TP 出场，bar2 才跌穿强平线 75
        # → 止盈放大 4×10% − 4×0.5 = 38.0，非 liquidated
        bars = _bars(date(2024, 1, 1), [105, 80, 78], highs=[111, 95, 90], lows=[90, 70, 72])
        res = _eval("买入", bars=bars, leverage=4, take_profit=110.0, funding=0.5)
        self.assertEqual(res["simulated_exit_reason"], "take_profit")
        self.assertAlmostEqual(res["simulated_exit_price"], 110.0)
        self.assertAlmostEqual(res["simulated_return_pct"], 38.0)

    def test_same_bar_liq_and_tp_prefers_liquidation(self):
        # 同 bar 双触（bar1 high 111≥TP 且 low 74≤liq 75）→ 保守强平优先
        bars = _bars(date(2024, 1, 1), [100, 100, 100], highs=[111, 100, 100], lows=[74, 99, 99])
        res = _eval("买入", bars=bars, leverage=4, take_profit=110.0)
        self.assertEqual(res["simulated_exit_reason"], "liquidated")
        self.assertEqual(res["simulated_return_pct"], -100.0)

    def test_same_bar_liq_and_sl_prefers_liquidation(self):
        # SL=80 > liq=75：触强平的 bar 必同触 SL（杠杆强平最常见形态）→ 强平优先，非 stop_loss
        bars = _bars(date(2024, 1, 1), [85, 90, 95], highs=[100, 95, 96], lows=[74, 85, 90])
        res = _eval("买入", bars=bars, leverage=4, stop_loss=80.0)
        self.assertEqual(res["simulated_exit_reason"], "liquidated")
        self.assertEqual(res["simulated_return_pct"], -100.0)
        self.assertAlmostEqual(res["simulated_exit_price"], 75.0)
        # 预测字段照旧：SL 命中记录不变
        self.assertTrue(res["hit_stop_loss"])
        self.assertEqual(res["first_hit"], "stop_loss")

    def test_short_liquidation_on_high_touch(self):
        # L=4 → liq_short=125；bar1 high 126 → 强平
        bars = _bars(date(2024, 1, 1), [120, 110, 105], highs=[126, 115, 108], lows=[110, 105, 100])
        res = _eval("卖出", bars=bars, leverage=4, funding=0.3)
        self.assertEqual(res["simulated_exit_reason"], "liquidated")
        self.assertEqual(res["simulated_return_pct"], -100.0)
        self.assertAlmostEqual(res["simulated_exit_price"], 125.0)

    def test_short_window_end_amplification(self):
        # 未触强平（liq_short=133.33，high≤99）：L×(entry−end)/entry×100 + L×funding = 3×10 + 3×0.6 = 31.8
        bars = _bars(date(2024, 1, 1), [95, 92, 90], highs=[99, 96, 93], lows=[94, 91, 89])
        res = _eval("卖出", bars=bars, leverage=3, funding=0.6)
        self.assertEqual(res["simulated_exit_reason"], "window_end_short")
        self.assertAlmostEqual(res["simulated_return_pct"], 31.8)

    def test_clamp_at_minus_100_without_liq_touch(self):
        # 近似声明④判别：价格未触强平线（liq_short=133.33，high≤132）但负资金费拖穿
        # 3×(−30) + 3×(−5) = −105 → 钳制输出恰 −100.0
        bars = _bars(date(2024, 1, 1), [120, 125, 130], highs=[125, 128, 132], lows=[115, 120, 126])
        res = _eval("卖出", bars=bars, leverage=3, funding=-5.0)
        self.assertEqual(res["simulated_exit_reason"], "window_end_short")
        self.assertEqual(res["simulated_return_pct"], -100.0)

    def test_non_perp_ignores_leverage(self):
        # is_perp=False + leverage=5 → 与不传 leverage 完全一致（bar1 low 74 若误进杠杆分支会被判强平）
        bars = _bars(date(2024, 1, 1), [105, 106, 107], highs=[111, 107, 108], lows=[74, 105, 106])
        self.assertEqual(
            _eval("买入", bars=bars, leverage=5, take_profit=110.0, is_perp=False),
            _eval("买入", bars=bars, take_profit=110.0, is_perp=False),
        )


class _TempDbTestCase(unittest.TestCase):
    """临时 DB + 配置隔离（形态同 tests/test_perp_backtest_service.py）。"""

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_PATH"] = os.path.join(self._temp_dir.name, "leverage_bt.db")
        for key in ("CRYPTO_BACKTEST_LEVERAGE", "BACKTEST_ENGINE_VERSION", "CRYPTO_DERIVATIVES_ENABLED"):
            os.environ.pop(key, None)
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self):
        for key in ("CRYPTO_BACKTEST_LEVERAGE", "BACKTEST_ENGINE_VERSION", "CRYPTO_DERIVATIVES_ENABLED"):
            os.environ.pop(key, None)
        Config._instance = None
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def _add_analysis(self, *, query_id, code, created_at, advice="买入"):
        with self.db.get_session() as session:
            session.add(AnalysisHistory(
                query_id=query_id, code=code, name=code, report_type="simple",
                sentiment_score=40, operation_advice=advice, trend_prediction="测试",
                analysis_summary="leverage test", created_at=created_at,
                context_snapshot=json.dumps({"enhanced_context": {"date": created_at.strftime("%Y-%m-%d")}}),
            ))
            session.commit()


class GetCandidatesPerpOnlyTestCase(_TempDbTestCase):
    def test_sql_filter_prevents_starvation(self):
        # 饥饿判别：5 条更新的股票分析 + 1 条更老的 perp，limit=3 < 股票行数。
        # 若只靠循环内跳过（无 SQL 粗滤），配额会被 3 条最新股票占满、永远选不到 perp。
        for i in range(5):
            self._add_analysis(query_id=f"s{i}", code="600519", created_at=datetime(2024, 1, 10 + i))
        self._add_analysis(query_id="p1", code=PERP_CODE, created_at=datetime(2024, 1, 1), advice="卖出")

        rows = BacktestRepository(self.db).get_candidates(
            code=None, min_age_days=0, limit=3, eval_window_days=3,
            engine_version="v1-x3", force=False, perp_only=True,
        )
        self.assertEqual([r.code for r in rows], [PERP_CODE])

    def test_default_perp_only_false_keeps_existing_behavior(self):
        self._add_analysis(query_id="s0", code="600519", created_at=datetime(2024, 1, 2))
        self._add_analysis(query_id="p1", code=PERP_CODE, created_at=datetime(2024, 1, 1), advice="卖出")
        rows = BacktestRepository(self.db).get_candidates(
            code=None, min_age_days=0, limit=10, eval_window_days=3,
            engine_version="v1", force=False,
        )
        self.assertEqual({r.code for r in rows}, {"600519", PERP_CODE})


if __name__ == "__main__":
    unittest.main()
