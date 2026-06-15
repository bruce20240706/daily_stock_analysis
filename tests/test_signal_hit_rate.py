# -*- coding: utf-8 -*-
"""Tests for signal hit-rate backfill (M2c)."""

import os
import tempfile
import unittest
from datetime import date

from src.config import Config
from src.repositories.backtest_repo import BacktestRepository
from src.storage import AnalysisHistory, BacktestResult, DatabaseManager


class SignalHitRateRepoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_signal_hit_rate.db")
        os.environ["DATABASE_PATH"] = self._db_path

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.repo = BacktestRepository(self.db)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config._instance = None
        self._temp_dir.cleanup()

    def _add(self, *, code, analysis_date, eval_status, direction_correct,
             eval_window_days=10, engine_version="v1"):
        # BacktestResult.analysis_history_id 是 nullable=False 的 FK
        # （ForeignKey('analysis_history.id')），必须先落一条 AnalysisHistory 父行。
        # code 同为 nullable=False。其余 nullable=False 列均有默认值。
        with self.db.get_session() as session:
            history = AnalysisHistory(code=code, name=code, report_type="single")
            session.add(history)
            session.flush()  # 取得 history.id
            session.add(
                BacktestResult(
                    analysis_history_id=history.id,
                    code=code,
                    analysis_date=analysis_date,
                    eval_status=eval_status,
                    direction_correct=direction_correct,
                    eval_window_days=eval_window_days,
                    engine_version=engine_version,
                )
            )
            session.commit()

    def test_returns_only_completed_for_code(self) -> None:
        self._add(code="600519", analysis_date=date(2024, 1, 1),
                  eval_status="completed", direction_correct=True)
        self._add(code="600519", analysis_date=date(2024, 1, 2),
                  eval_status="insufficient_data", direction_correct=None)
        self._add(code="000001", analysis_date=date(2024, 1, 3),
                  eval_status="completed", direction_correct=False)

        rows = self.repo.get_completed_results_for_code("600519")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].code, "600519")
        self.assertEqual(rows[0].eval_status, "completed")

    def test_window_and_version_filter(self) -> None:
        self._add(code="600519", analysis_date=date(2024, 1, 1),
                  eval_status="completed", direction_correct=True,
                  eval_window_days=10, engine_version="v1")
        self._add(code="600519", analysis_date=date(2024, 1, 2),
                  eval_status="completed", direction_correct=False,
                  eval_window_days=5, engine_version="v1")
        self._add(code="600519", analysis_date=date(2024, 1, 3),
                  eval_status="completed", direction_correct=True,
                  eval_window_days=10, engine_version="v2")

        rows = self.repo.get_completed_results_for_code(
            "600519", eval_window_days=10, engine_version="v1"
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].eval_window_days, 10)
        self.assertEqual(rows[0].engine_version, "v1")


class SignalHitVerifiedConfigTestCase(unittest.TestCase):
    def setUp(self) -> None:
        Config._instance = None

    def tearDown(self) -> None:
        os.environ.pop("SIGNAL_HIT_VERIFIED_MIN_SAMPLE", None)
        Config._instance = None

    def test_default_falls_back_to_eval_window_days(self) -> None:
        os.environ.pop("SIGNAL_HIT_VERIFIED_MIN_SAMPLE", None)
        # Config 经单例 + 惰性 _load_from_env 构造：reset 后 get_instance 才重读 env。
        Config.reset_instance()
        cfg = Config.get_instance()
        self.assertEqual(
            cfg.signal_hit_verified_min_sample,
            cfg.backtest_eval_window_days,
        )

    def test_env_override(self) -> None:
        os.environ["SIGNAL_HIT_VERIFIED_MIN_SAMPLE"] = "7"
        Config.reset_instance()
        cfg = Config.get_instance()
        self.assertEqual(cfg.signal_hit_verified_min_sample, 7)


if __name__ == "__main__":
    unittest.main()
