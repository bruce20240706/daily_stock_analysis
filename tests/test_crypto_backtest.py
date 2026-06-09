# -*- coding: utf-8 -*-
"""crypto 回测兼容 characterization / lock 测试（离线，无网络、无 LLM）。

证明并锁定：crypto symbol（如 BTC/USDT）走与股票相同的现有回测流程
（AnalysisHistory → 前向日线 → evaluate_single）产出正确 BacktestResult。
这些测试验证既有行为，应首次即 PASS；若 FAIL 说明暴露真 bug，按 spec §7 修复。
"""
import json
import os
import tempfile
import unittest
from datetime import date, datetime
from unittest.mock import patch

import pandas as pd

from src.config import Config
from src.services.backtest_service import BacktestService
from src.storage import AnalysisHistory, BacktestResult, DatabaseManager, StockDaily

CRYPTO_CODE = "BTC/USDT"


class CryptoBacktestTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_crypto_backtest.db")
        os.environ["DATABASE_PATH"] = self._db_path
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def _seed_analysis(self, *, analysis_date: date = date(2024, 1, 1)) -> None:
        """Seed a crypto AnalysisHistory old enough to be a backtest candidate."""
        with self.db.get_session() as session:
            session.add(
                AnalysisHistory(
                    query_id="cq1",
                    code=CRYPTO_CODE,
                    name="BTC/USDT",
                    report_type="simple",
                    sentiment_score=70,
                    operation_advice="买入",
                    trend_prediction="看多",
                    analysis_summary="crypto backtest lock test",
                    stop_loss=57000.0,
                    take_profit=66000.0,
                    created_at=datetime(analysis_date.year, analysis_date.month, analysis_date.day),
                    context_snapshot=json.dumps(
                        {
                            "enhanced_context": {"date": analysis_date.isoformat()},
                            "market_phase_summary": {
                                "phase": "intraday",
                                "market": "crypto",
                                "trigger_source": "api",
                            },
                        }
                    ),
                )
            )
            session.commit()

    def _seed_daily(self) -> None:
        """Seed start bar + 3 forward bars; day2 high (67000) hits take_profit (66000)."""
        with self.db.get_session() as session:
            session.add(
                StockDaily(code=CRYPTO_CODE, date=date(2024, 1, 1),
                           open=60000.0, high=60500.0, low=59500.0, close=60000.0)
            )
            session.add_all([
                StockDaily(code=CRYPTO_CODE, date=date(2024, 1, 2), open=60000.0, high=67000.0, low=60000.0, close=65000.0),
                StockDaily(code=CRYPTO_CODE, date=date(2024, 1, 3), open=65000.0, high=66000.0, low=64000.0, close=65500.0),
                StockDaily(code=CRYPTO_CODE, date=date(2024, 1, 4), open=65500.0, high=66500.0, low=64500.0, close=66000.0),
            ])
            session.commit()

    def _result(self) -> BacktestResult:
        with self.db.get_session() as session:
            return session.query(BacktestResult).filter(BacktestResult.code == CRYPTO_CODE).one()

    def test_crypto_backtest_seeded_data_completed(self) -> None:
        self._seed_analysis()
        self._seed_daily()
        service = BacktestService(self.db)
        stats = service.run_backtest(code=CRYPTO_CODE, force=False, eval_window_days=3, min_age_days=0, limit=10)

        self.assertEqual(stats["completed"], 1)
        self.assertEqual(stats["saved"], 1)
        self.assertEqual(stats["processed"], 1)
        r = self._result()
        self.assertEqual(r.eval_status, "completed")
        self.assertEqual(r.code, CRYPTO_CODE)
        self.assertEqual(r.analysis_date, date(2024, 1, 1))
        self.assertAlmostEqual(r.start_price, 60000.0)
        self.assertAlmostEqual(r.end_close, 66000.0)
        self.assertAlmostEqual(r.stock_return_pct, 10.0)
        self.assertTrue(r.hit_take_profit)
        self.assertFalse(r.hit_stop_loss)
        self.assertEqual(r.first_hit, "take_profit")
        self.assertEqual(r.first_hit_date, date(2024, 1, 2))
        self.assertEqual(r.first_hit_trading_days, 1)
        self.assertEqual(r.outcome, "win")
        self.assertTrue(r.direction_correct)

        # 模拟执行：买入入场 start_price，止盈出场 take_profit
        self.assertAlmostEqual(r.simulated_entry_price, 60000.0)
        self.assertAlmostEqual(r.simulated_exit_price, 66000.0)
        self.assertEqual(r.simulated_exit_reason, "take_profit")
        self.assertAlmostEqual(r.simulated_return_pct, 10.0)


if __name__ == "__main__":
    unittest.main()
