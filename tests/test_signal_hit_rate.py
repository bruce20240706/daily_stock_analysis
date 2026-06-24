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

    def test_sample_below_threshold_returns_all_none(self) -> None:
        # spec §4.1: sample < min_sample → all-None（样本不足/null），而非 hit_rate 有值但 verified=False
        stat = self._make_stat(sample=2, win_rate=1.0, ci_low=0.55)
        with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
             patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
            Repo.return_value.get.return_value = stat
            fields = resolve_marker_hit_fields("rule_score", "600519")
            # sample=2 < min_sample=3 → 返回全 None，不透出 hit_rate/hit_sample
            self.assertIsNone(fields["hit_rate"])
            self.assertIsNone(fields["hit_sample"])
            self.assertFalse(fields["verified"])
            self.assertIsNone(fields["ci_low"])
            self.assertIsNone(fields["ci_high"])
            self.assertIsNone(fields["baseline_excess"])

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
        # min_sample=3，stat.sample=1 < min_sample → spec §4.1 返回全 None
        self.assertIsNone(fields["hit_rate"])
        self.assertIsNone(fields["hit_sample"])
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
        Repo.return_value.get.assert_called_with("volume_breakout", "cn", interval="1d", horizon=10)


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
                     "ci_low": None, "ci_high": None, "baseline_excess": None,
                     "horizon": None}


def test_resolve_stat_with_zero_sample_returns_all_none():
    """Coverage 3 (A6): stat 对象存在但 sample==0 时覆盖 `(stat.sample or 0) < min_sample` 分支。

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
                     "ci_low": None, "ci_high": None, "baseline_excess": None,
                     "horizon": None}


def test_resolve_marker_hit_fields_real_repo_roundtrip(tmp_path):
    """Fix 4 (M7): resolver ↔ repo SQLite 真实读写 roundtrip，无 mock 路径。

    验证 resolve_marker_hit_fields 能从真实落库的 SignalStatRow 读回正确的
    hit_rate / hit_sample / verified / ci_low / ci_high / baseline_excess 值。
    """
    import os
    import tempfile
    from src.config import Config
    from src.repositories.signal_stats_repo import SignalStatsRepository
    from src.storage import DatabaseManager, SignalStatRow

    # 1. 建隔离 SQLite 数据库
    db_file = str(tmp_path / "roundtrip_test.db")
    os.environ["DATABASE_PATH"] = db_file
    Config._instance = None
    # 设置 min_sample=5，horizon=10（默认），确保样本足
    os.environ["SIGNAL_HIT_VERIFIED_MIN_SAMPLE"] = "5"
    os.environ.pop("SIGNAL_BACKTEST_HORIZON_BARS", None)
    Config.reset_instance()
    DatabaseManager.reset_instance()

    try:
        db = DatabaseManager.get_instance()
        repo = SignalStatsRepository(db)

        # 2. 写入 SignalStatRow：sample(10) >= min_sample(5)，ci_low(0.60) > baseline(0.50)
        row = SignalStatRow(
            signal_type="volume_breakout",
            market="cn",
            interval="1d",
            horizon=10,
            win=7,
            loss=3,
            sample=10,
            win_rate=0.70,
            ci_low=0.60,
            ci_high=0.82,
            baseline_win_rate=0.50,
            excess=0.10,
        )
        repo.save_batch([row])

        # 3. 通过 resolve_marker_hit_fields 读回（monkeypatch get_market + SignalStatsRepository）
        import src.services.signal_hit_rate as shr_mod
        _orig_market = shr_mod.get_market_for_stock
        _orig_repo_cls = shr_mod.SignalStatsRepository

        shr_mod.get_market_for_stock = lambda code: "cn"  # type: ignore[assignment]
        shr_mod.SignalStatsRepository = lambda db_mgr=None: SignalStatsRepository(db)  # type: ignore[assignment]

        try:
            fields = resolve_marker_hit_fields("volume_breakout", "600519")
        finally:
            shr_mod.get_market_for_stock = _orig_market
            shr_mod.SignalStatsRepository = _orig_repo_cls

        # 4. 校验字段与落库行完全一致
        assert fields["hit_rate"] == 0.70, f"hit_rate mismatch: {fields['hit_rate']}"
        assert fields["hit_sample"] == 10, f"hit_sample mismatch: {fields['hit_sample']}"
        assert fields["verified"] is True, f"verified should be True: {fields['verified']}"
        assert fields["ci_low"] == 0.60, f"ci_low mismatch: {fields['ci_low']}"
        assert fields["ci_high"] == 0.82, f"ci_high mismatch: {fields['ci_high']}"
        assert abs(fields["baseline_excess"] - 0.10) < 1e-9, \
            f"baseline_excess mismatch: {fields['baseline_excess']}"
    finally:
        DatabaseManager.reset_instance()
        Config._instance = None
        os.environ.pop("SIGNAL_HIT_VERIFIED_MIN_SAMPLE", None)


# === 链路B 分钟化:resolve_marker_hit_fields interval 透传(B-T3)===
from types import SimpleNamespace
from src.services import signal_hit_rate as shr


def test_resolve_marker_hit_fields_passes_interval(monkeypatch):
    """resolve_marker_hit_fields 把 interval 透传到 SignalStatsRepository.get;默认 1d。"""
    seen = {}

    class _Repo:
        def get(self, signal_type, market, *, interval="1d", horizon=None):
            seen.update(interval=interval, horizon=horizon)
            return SimpleNamespace(sample=999, win_rate=0.6, ci_low=0.55,
                                   ci_high=0.7, baseline_win_rate=0.5, excess=0.05)

    monkeypatch.setattr(shr, "SignalStatsRepository", lambda *a, **k: _Repo())
    monkeypatch.setattr(shr, "get_market_for_stock", lambda code: "crypto")

    shr.resolve_marker_hit_fields("vps_x", "BTC/USDT", interval="5m")
    assert seen["interval"] == "5m"
    shr.resolve_marker_hit_fields("vps_x", "BTC/USDT")     # 默认
    assert seen["interval"] == "1d"
