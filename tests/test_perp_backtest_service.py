# -*- coding: utf-8 -*-
"""perp 回测 service：资金费窗口换算 spy、perp e2e（short 落库/funding 调整）、门控/足量守卫。"""
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from src.config import Config
from src.services.backtest_service import BacktestService
from src.storage import AnalysisHistory, BacktestResult, DatabaseManager, StockDaily

PERP_CODE = "BTC/USDT:PERP"


class PerpBacktestServiceTestCase(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_PATH"] = os.path.join(self._temp_dir.name, "perp_bt.db")
        os.environ.pop("CRYPTO_DERIVATIVES_ENABLED", None)
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self):
        os.environ.pop("CRYPTO_DERIVATIVES_ENABLED", None)
        Config._instance = None
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def _seed(self, advice="卖出"):
        with self.db.get_session() as session:
            session.add(AnalysisHistory(
                query_id="pq1", code=PERP_CODE, name="BTC perp", report_type="simple",
                sentiment_score=40, operation_advice=advice, trend_prediction="看空",
                analysis_summary="perp short test", stop_loss=110000.0, take_profit=90000.0,
                created_at=datetime(2024, 1, 1),
                context_snapshot=json.dumps({"enhanced_context": {"date": "2024-01-01"}}),
            ))
            # 起始 bar + 3 前向 bar，价格下跌（做空盈利）
            session.add(StockDaily(code=PERP_CODE, date=date(2024, 1, 1), open=100000.0, high=100500.0, low=99500.0, close=100000.0))
            session.add_all([
                StockDaily(code=PERP_CODE, date=date(2024, 1, 2), open=100000.0, high=100000.0, low=97000.0, close=98000.0),
                StockDaily(code=PERP_CODE, date=date(2024, 1, 3), open=98000.0, high=98000.0, low=95000.0, close=96000.0),
                StockDaily(code=PERP_CODE, date=date(2024, 1, 4), open=96000.0, high=96000.0, low=94000.0, close=95000.0),
            ])
            session.commit()

    def _result(self):
        with self.db.get_session() as session:
            return session.query(BacktestResult).filter(BacktestResult.code == PERP_CODE).one()

    def test_funding_window_args_and_pct(self):
        self._seed()
        captured = {}

        def spy(base, quote, start_ms, end_ms):
            captured.update(base=base, quote=quote, start_ms=start_ms, end_ms=end_ms)
            return [0.0001, 0.0002]  # sum=0.0003 → *100 = 0.03

        with patch("data_provider.crypto_derivatives.fetch_funding_rate_history", side_effect=spy):
            BacktestService(self.db).run_backtest(code=PERP_CODE, eval_window_days=3, min_age_days=0, limit=10)

        # 入场 = start_date+1 00:00 UTC；出场 = start_date+(N+1) 00:00 UTC
        entry = int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp() * 1000)
        exit_ = int(datetime(2024, 1, 5, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertEqual(captured["base"], "BTC")
        self.assertEqual(captured["quote"], "USDT")
        self.assertEqual(captured["start_ms"], entry)
        self.assertEqual(captured["end_ms"], exit_)

    def test_perp_short_persisted_with_funding(self):
        self._seed()
        with patch("data_provider.crypto_derivatives.fetch_funding_rate_history", return_value=[0.0003]):
            BacktestService(self.db).run_backtest(code=PERP_CODE, eval_window_days=3, min_age_days=0, limit=10)
        r = self._result()
        self.assertEqual(r.eval_status, "completed")
        self.assertEqual(r.position_recommendation, "short")
        self.assertEqual(r.simulated_exit_reason, "window_end_short")
        # (100000-95000)/100000*100 + 0.0003*100 = 5.03
        self.assertAlmostEqual(r.simulated_return_pct, 5.03, places=4)
        self.assertEqual(r.outcome, "win")

    def test_gating_off_skips_fetch_but_keeps_short_pnl(self):
        os.environ["CRYPTO_DERIVATIVES_ENABLED"] = "false"
        Config._instance = None
        self._seed()
        with patch("data_provider.crypto_derivatives.fetch_funding_rate_history") as m:
            BacktestService(self.db).run_backtest(code=PERP_CODE, eval_window_days=3, min_age_days=0, limit=10)
        m.assert_not_called()
        r = self._result()
        self.assertEqual(r.position_recommendation, "short")
        self.assertAlmostEqual(r.simulated_return_pct, 5.0, places=4)  # 无 funding

    def test_insufficient_bars_skips_fetch(self):
        self._seed()
        # 要求 5 bar 但只有 3 前向 bar → insufficient，且不应发起 funding 抓取
        with patch("data_provider.crypto_derivatives.fetch_funding_rate_history") as m, \
             patch("data_provider.base.DataFetcherManager.get_daily_data", return_value=(__import__("pandas").DataFrame(), "")):
            BacktestService(self.db).run_backtest(code=PERP_CODE, eval_window_days=5, min_age_days=0, limit=10)
        m.assert_not_called()
        self.assertEqual(self._result().eval_status, "insufficient_data")


if __name__ == "__main__":
    unittest.main()
