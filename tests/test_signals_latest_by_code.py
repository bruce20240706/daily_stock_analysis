# -*- coding: utf-8 -*-
"""Regression tests for DatabaseManager.get_latest_analysis_by_code (M2a)."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from src.config import Config
from src.storage import DatabaseManager, AnalysisHistory


class LatestAnalysisByCodeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        # 与 tests/test_analysis_history.py:setUp 同范式：经 DATABASE_PATH + reset_instance
        # 拿独立临时库。DatabaseManager.get_instance() 不接 db_url 形参，
        # 库路径只能经 Config（DATABASE_PATH）注入。
        self._tmp = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._tmp.name, "latest_by_code.db")
        os.environ["DATABASE_PATH"] = db_path

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        self._tmp.cleanup()

    def _insert(self, code: str, advice: str, created_at: datetime) -> None:
        with self.db.session_scope() as session:
            session.add(
                AnalysisHistory(
                    code=code,
                    name=code,
                    report_type="single",
                    operation_advice=advice,
                    created_at=created_at,
                )
            )

    def test_returns_most_recent_record_for_code(self) -> None:
        base = datetime(2026, 6, 1, 9, 0, 0)
        self._insert("600519", "观望", base)
        self._insert("600519", "买入", base + timedelta(days=2))

        latest = self.db.get_latest_analysis_by_code("600519")

        self.assertIsNotNone(latest)
        self.assertEqual(latest.operation_advice, "买入")

    def test_returns_none_when_no_record(self) -> None:
        self.assertIsNone(self.db.get_latest_analysis_by_code("AAPL"))

    def test_isolated_by_code(self) -> None:
        base = datetime(2026, 6, 1, 9, 0, 0)
        self._insert("600519", "买入", base)
        self._insert("hk00700", "卖出", base + timedelta(days=1))

        latest = self.db.get_latest_analysis_by_code("600519")

        self.assertIsNotNone(latest)
        self.assertEqual(latest.operation_advice, "买入")


if __name__ == "__main__":
    unittest.main()
