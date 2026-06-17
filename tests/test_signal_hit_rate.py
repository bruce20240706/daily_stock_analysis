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
    """M3-A6 改源后：resolve_marker_hit_fields 读 signal_stats by (signal_type, market)。
    旧 per-code BacktestResult 路径已移走，测试改为 mock signal_stats 源。"""

    def setUp(self) -> None:
        os.environ["SIGNAL_HIT_VERIFIED_MIN_SAMPLE"] = "3"
        Config._instance = None

    def tearDown(self) -> None:
        Config._instance = None
        os.environ.pop("SIGNAL_HIT_VERIFIED_MIN_SAMPLE", None)

    def _make_stat(self, *, sample, win_rate, ci_low, ci_high=0.80,
                   baseline_win_rate=0.50, excess=None):
        from unittest.mock import MagicMock
        m = MagicMock()
        m.sample = sample
        m.win_rate = win_rate
        m.ci_low = ci_low
        m.ci_high = ci_high
        m.baseline_win_rate = baseline_win_rate
        m.excess = excess if excess is not None else (ci_low - baseline_win_rate)
        return m

    def test_sample_at_threshold_sets_verified_true(self) -> None:
        # sample=3 >= min_sample=3, ci_low(0.55) > baseline(0.50) => verified=True
        stat = self._make_stat(sample=3, win_rate=round(2/3, 4), ci_low=0.55)
        with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
             patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
            Repo.return_value.get.return_value = stat
            fields = resolve_marker_hit_fields("rule_score", "600519")
            self.assertEqual(fields["hit_sample"], 3)
            self.assertTrue(fields["verified"])
            self.assertAlmostEqual(fields["hit_rate"], round(2/3, 4))

    def test_sample_below_threshold_not_verified(self) -> None:
        # sample=2 < min_sample=3 => verified=False
        stat = self._make_stat(sample=2, win_rate=1.0, ci_low=0.55)
        with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
             patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
            Repo.return_value.get.return_value = stat
            fields = resolve_marker_hit_fields("rule_score", "600519")
            self.assertEqual(fields["hit_sample"], 2)
            self.assertFalse(fields["verified"])

    def test_no_sample_returns_null_fields_not_verified(self) -> None:
        with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
             patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
            Repo.return_value.get.return_value = None
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

        # M3-A6：resolve_marker_hit_fields 现在读 signal_stats（not per-code BacktestResult）。
        # 用 mock signal_stats 验证 min_sample 门限 (sample=1 < min_sample=3 → not verified)。
        from unittest.mock import MagicMock, patch as _patch
        stat = MagicMock()
        stat.sample = 1
        stat.win_rate = 1.0
        stat.ci_low = 0.55
        stat.ci_high = 0.80
        stat.baseline_win_rate = 0.50
        stat.excess = 0.05
        with _patch("src.services.signal_hit_rate.get_market_for_stock", return_value="crypto"), \
             _patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
            Repo.return_value.get.return_value = stat
            fields = resolve_marker_hit_fields("rule_score", "BTCUSDT")
        # min_sample=3，stat.sample=1 → 不应判 verified
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


# ---------------------------------------------------------------------------
# M3-A6：resolve_marker_hit_fields 改读 signal_stats by (signal_type, market)
# ---------------------------------------------------------------------------

from unittest.mock import patch, MagicMock  # noqa: E402


def _stat(**kw):
    m = MagicMock()
    d = dict(sample=20, win_rate=0.68, ci_low=0.55, ci_high=0.80,
             baseline_win_rate=0.50, excess=0.05)
    d.update(kw)
    for k, v in d.items():
        setattr(m, k, v)
    return m


def test_resolve_reads_signal_stats_by_market_and_sets_verified_on_excess():
    with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
         patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
        Repo.return_value.get.return_value = _stat()
        f = resolve_marker_hit_fields("volume_breakout", "600519")
        assert f["hit_rate"] == 0.68 and f["hit_sample"] == 20
        assert f["ci_low"] == 0.55 and f["baseline_excess"] == 0.05
        assert f["verified"] is True   # 样本足 且 ci_low(0.55) > baseline(0.50)
        Repo.return_value.get.assert_called_with("volume_breakout", "cn")


def test_resolve_not_verified_when_ci_low_below_baseline():
    with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
         patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
        Repo.return_value.get.return_value = _stat(ci_low=0.45, baseline_win_rate=0.50, excess=-0.05)
        assert resolve_marker_hit_fields("x", "600519")["verified"] is False  # 无超额


def test_resolve_missing_bucket_is_sample_insufficient():
    with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
         patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
        Repo.return_value.get.return_value = None
        f = resolve_marker_hit_fields("x", "600519")
        assert f == {"hit_rate": None, "hit_sample": None, "verified": False,
                     "ci_low": None, "ci_high": None, "baseline_excess": None}


def test_resolve_stat_with_zero_sample_returns_all_none():
    """Coverage 3 (A6): stat 对象存在但 sample==0 时覆盖 `(stat.sample or 0) <= 0` 分支。

    与 stat=None 分支（test_resolve_missing_bucket_is_sample_insufficient）不同，
    此处 repo 返回一个真实 stat 对象，只是 sample 值为 0（桶存在但无样本）。
    两者都应返回 all-None/verified=False dict。
    """
    stat = _stat(sample=0, win_rate=None, ci_low=None, ci_high=None,
                 baseline_win_rate=0.50, excess=None)
    stat.excess = None
    with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
         patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
        Repo.return_value.get.return_value = stat
        f = resolve_marker_hit_fields("volume_breakout", "600519")
        assert f == {"hit_rate": None, "hit_sample": None, "verified": False,
                     "ci_low": None, "ci_high": None, "baseline_excess": None}
