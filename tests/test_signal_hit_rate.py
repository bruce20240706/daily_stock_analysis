# -*- coding: utf-8 -*-
"""Tests for signal hit-rate backfill (M2c)."""

import os
import tempfile
import unittest
from datetime import date

from src.config import Config
from src.repositories.backtest_repo import BacktestRepository
from src.services.signal_hit_rate import (
    HitRate,
    backfill_signal_hit_rate,
    resolve_marker_hit_fields,
)
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


class BackfillSignalHitRateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_backfill.db")
        os.environ["DATABASE_PATH"] = self._db_path
        os.environ["BACKTEST_EVAL_WINDOW_DAYS"] = "4"
        os.environ.pop("SIGNAL_HIT_VERIFIED_MIN_SAMPLE", None)

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config._instance = None
        os.environ.pop("BACKTEST_EVAL_WINDOW_DAYS", None)
        self._temp_dir.cleanup()

    def _add(self, *, code, analysis_date, direction_correct, eval_status="completed"):
        # 先落 AnalysisHistory 父行满足 BacktestResult.analysis_history_id（nullable=False FK）。
        with self.db.get_session() as session:
            history = AnalysisHistory(code=code, name=code, report_type="single")
            session.add(history)
            session.flush()
            session.add(
                BacktestResult(
                    analysis_history_id=history.id,
                    code=code,
                    analysis_date=analysis_date,
                    eval_status=eval_status,
                    direction_correct=direction_correct,
                    eval_window_days=10,
                    engine_version="v1",
                )
            )
            session.commit()

    def test_aggregates_direction_hit_rate(self) -> None:
        # 3 correct, 1 incorrect -> 0.75 over 4 samples
        self._add(code="600519", analysis_date=date(2024, 1, 1), direction_correct=True)
        self._add(code="600519", analysis_date=date(2024, 1, 2), direction_correct=True)
        self._add(code="600519", analysis_date=date(2024, 1, 3), direction_correct=True)
        self._add(code="600519", analysis_date=date(2024, 1, 4), direction_correct=False)

        result = backfill_signal_hit_rate("rule_score", "600519")

        self.assertIsInstance(result, HitRate)
        self.assertEqual(result.hit_sample, 4)
        self.assertAlmostEqual(result.hit_rate, 0.75)

    def test_direction_correct_none_excluded_from_sample(self) -> None:
        # completed but direction_correct None must not enter denominator
        self._add(code="600519", analysis_date=date(2024, 1, 1), direction_correct=True)
        self._add(code="600519", analysis_date=date(2024, 1, 2), direction_correct=None)

        result = backfill_signal_hit_rate("rule_score", "600519")

        self.assertEqual(result.hit_sample, 1)
        self.assertAlmostEqual(result.hit_rate, 1.0)

    def test_no_sample_returns_null_hit_rate(self) -> None:
        result = backfill_signal_hit_rate("rule_score", "000002")

        self.assertIsNone(result.hit_rate)
        self.assertEqual(result.hit_sample, 0)


class ResolveMarkerHitFieldsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_resolve.db")
        os.environ["DATABASE_PATH"] = self._db_path
        os.environ["SIGNAL_HIT_VERIFIED_MIN_SAMPLE"] = "3"

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config._instance = None
        os.environ.pop("SIGNAL_HIT_VERIFIED_MIN_SAMPLE", None)
        self._temp_dir.cleanup()

    def _add(self, *, code, analysis_date, direction_correct):
        # 先落 AnalysisHistory 父行满足 BacktestResult.analysis_history_id（nullable=False FK）。
        with self.db.get_session() as session:
            history = AnalysisHistory(code=code, name=code, report_type="single")
            session.add(history)
            session.flush()
            session.add(
                BacktestResult(
                    analysis_history_id=history.id,
                    code=code,
                    analysis_date=analysis_date,
                    eval_status="completed",
                    direction_correct=direction_correct,
                    eval_window_days=10,
                    engine_version="v1",
                )
            )
            session.commit()

    def test_sample_at_threshold_sets_verified_true(self) -> None:
        self._add(code="600519", analysis_date=date(2024, 1, 1), direction_correct=True)
        self._add(code="600519", analysis_date=date(2024, 1, 2), direction_correct=True)
        self._add(code="600519", analysis_date=date(2024, 1, 3), direction_correct=False)

        fields = resolve_marker_hit_fields("rule_score", "600519")

        self.assertEqual(fields["hit_sample"], 3)
        self.assertTrue(fields["verified"])
        self.assertAlmostEqual(fields["hit_rate"], round(2 / 3, 4))

    def test_sample_below_threshold_not_verified(self) -> None:
        self._add(code="600519", analysis_date=date(2024, 1, 1), direction_correct=True)
        self._add(code="600519", analysis_date=date(2024, 1, 2), direction_correct=True)

        fields = resolve_marker_hit_fields("rule_score", "600519")

        self.assertEqual(fields["hit_sample"], 2)
        self.assertFalse(fields["verified"])

    def test_no_sample_returns_null_fields_not_verified(self) -> None:
        fields = resolve_marker_hit_fields("rule_score", "000002")

        self.assertIsNone(fields["hit_rate"])
        self.assertIsNone(fields["hit_sample"])
        self.assertFalse(fields["verified"])


class DedupeSampleInflationTestCase(unittest.TestCase):
    """终审#1：同一 (analysis_history_id, eval_window_days) 下的 engine_version 变体
    （杠杆回测写 v1/v1-x3/v1-x5）方向命中是杠杆无关的同一底层观测，必须只计一次，
    否则 hit_sample / verified 在永续/杠杆 code 上虚增。不同 eval_window_days 是不同
    前向预测，仍各计一次。"""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_dedup.db")
        os.environ["DATABASE_PATH"] = self._db_path
        os.environ["SIGNAL_HIT_VERIFIED_MIN_SAMPLE"] = "3"

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config._instance = None
        os.environ.pop("SIGNAL_HIT_VERIFIED_MIN_SAMPLE", None)
        self._temp_dir.cleanup()

    def _add_rows(self, *, code, analysis_date, rows):
        """在同一个 AnalysisHistory（同一 analysis_history_id）下落多条 BacktestResult。

        rows: 形如 [(eval_window_days, engine_version, direction_correct), ...]。
        """
        with self.db.get_session() as session:
            history = AnalysisHistory(code=code, name=code, report_type="single")
            session.add(history)
            session.flush()  # 取得 history.id
            for eval_window_days, engine_version, direction_correct in rows:
                session.add(
                    BacktestResult(
                        analysis_history_id=history.id,
                        code=code,
                        analysis_date=analysis_date,
                        eval_status="completed",
                        direction_correct=direction_correct,
                        eval_window_days=eval_window_days,
                        engine_version=engine_version,
                    )
                )
            session.commit()

    def test_leverage_engine_version_variants_count_as_one_sample(self) -> None:
        # 同一 analysis_history_id + 同一 eval_window_days，仅 engine_version 不同
        # （v1 / v1-x3 / v1-x5），方向命中均 True。底层只有 1 个观测。
        self._add_rows(
            code="BTCUSDT",
            analysis_date=date(2024, 1, 1),
            rows=[
                (10, "v1", True),
                (10, "v1-x3", True),
                (10, "v1-x5", True),
            ],
        )

        result = backfill_signal_hit_rate("rule_score", "BTCUSDT")

        self.assertEqual(result.hit_sample, 1)  # NOT 3
        self.assertAlmostEqual(result.hit_rate, 1.0)

        fields = resolve_marker_hit_fields("rule_score", "BTCUSDT")
        # min_sample=3，去重后仅 1 个独立样本 → 不应判 verified
        self.assertEqual(fields["hit_sample"], 1)
        self.assertFalse(fields["verified"])

    def test_distinct_eval_windows_count_separately(self) -> None:
        # 同一 analysis_history_id，但 eval_window_days 不同（10 / 20）是不同前向预测，
        # 各算一个独立观测。
        self._add_rows(
            code="600519",
            analysis_date=date(2024, 1, 1),
            rows=[
                (10, "v1", True),
                (20, "v1", True),
            ],
        )

        result = backfill_signal_hit_rate("rule_score", "600519")

        self.assertEqual(result.hit_sample, 2)
        self.assertAlmostEqual(result.hit_rate, 1.0)

    def test_dedupe_prefers_completed_row_over_none(self) -> None:
        # 同一 (analysis_history_id, eval_window_days) 下，若杠杆变体里有一条
        # direction_correct=None，去重保留的代表行必须优先取 direction_correct
        # 非 None 的那条，避免完成的 base 行被 None 行遮蔽导致样本反而消失。
        self._add_rows(
            code="ETHUSDT",
            analysis_date=date(2024, 1, 1),
            rows=[
                (10, "v1-x3", None),
                (10, "v1", True),
            ],
        )

        result = backfill_signal_hit_rate("rule_score", "ETHUSDT")

        self.assertEqual(result.hit_sample, 1)
        self.assertAlmostEqual(result.hit_rate, 1.0)


if __name__ == "__main__":
    unittest.main()
