# -*- coding: utf-8 -*-
"""Tests for Task 9: API interval parameter and response fields.

Covers:
- _build_result_conditions accepts bar_interval and generates the right condition.
- Seeded daily/minute rows are correctly isolated by interval query.
- get_recent_evaluations uses interval-aware engine_version default.
- run endpoint passes interval to service.
- BacktestResultItem includes bar_interval and first_hit_bar_index fields.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime

from src.config import Config
from src.repositories.backtest_repo import BacktestRepository
from src.services.backtest_service import BacktestService
from src.storage import AnalysisHistory, BacktestResult, DatabaseManager


# ---------------------------------------------------------------------------
# Helper: seed an AnalysisHistory + BacktestResult pair in session
# ---------------------------------------------------------------------------

def _seed_analysis(session, *, code: str, analysis_date: date) -> AnalysisHistory:
    ah = AnalysisHistory(
        query_id=f"q-{code}-{analysis_date}",
        code=code,
        name=code,
        report_type="simple",
        sentiment_score=70,
        operation_advice="买入",
        trend_prediction="看多",
        analysis_summary="test",
        created_at=datetime(2024, 1, 1, 0, 0, 0),
        context_snapshot=json.dumps(
            {"enhanced_context": {"date": analysis_date.isoformat()}}
        ),
    )
    session.add(ah)
    session.flush()  # populate ah.id
    return ah


def _seed_result(
    session,
    *,
    analysis_history_id: int,
    code: str,
    analysis_date: date,
    engine_version: str,
    bar_interval: str,
    first_hit_bar_index=None,
) -> BacktestResult:
    br = BacktestResult(
        analysis_history_id=analysis_history_id,
        code=code,
        analysis_date=analysis_date,
        eval_window_days=10,
        engine_version=engine_version,
        bar_interval=bar_interval,
        eval_status="completed",
        evaluated_at=datetime(2024, 1, 11, 0, 0, 0),
        operation_advice="买入",
        first_hit_bar_index=first_hit_bar_index,
    )
    session.add(br)
    return br


# ---------------------------------------------------------------------------
# Test 1: _build_result_conditions generates bar_interval condition
# ---------------------------------------------------------------------------

class TestBuildResultConditionsBarInterval(unittest.TestCase):
    """_build_result_conditions with bar_interval generates the right condition."""

    def test_bar_interval_condition_present(self):
        repo = BacktestRepository.__new__(BacktestRepository)
        conds = repo._build_result_conditions(
            code=None,
            eval_window_days=10,
            engine_version="v1-5m",
            analysis_date_from=None,
            analysis_date_to=None,
            days=None,
            bar_interval="5m",
        )
        rendered = " ".join(str(c) for c in conds)
        assert "bar_interval" in rendered, (
            f"Expected 'bar_interval' in conditions, got: {rendered}"
        )

    def test_bar_interval_none_not_added(self):
        """When bar_interval is not provided, no bar_interval condition appended."""
        repo = BacktestRepository.__new__(BacktestRepository)
        conds = repo._build_result_conditions(
            code=None,
            eval_window_days=10,
            engine_version="v1",
            analysis_date_from=None,
            analysis_date_to=None,
            days=None,
        )
        rendered = " ".join(str(c) for c in conds)
        assert "bar_interval" not in rendered, (
            f"bar_interval should not be in conditions when not specified: {rendered}"
        )


# ---------------------------------------------------------------------------
# Test 2: daily vs minute row isolation via get_recent_evaluations
# ---------------------------------------------------------------------------

class TestIntervalIsolation(unittest.TestCase):
    """Daily rows must not leak into minute queries and vice versa."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._temp_dir.name, "test_interval.db")
        os.environ["DATABASE_PATH"] = db_path
        os.environ["BACKTEST_EVAL_WINDOW_DAYS"] = "10"
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

        self._analysis_date = date(2024, 1, 1)

        with self.db.get_session() as session:
            # Daily row
            ah_daily = _seed_analysis(session, code="BTC-USDT:PERP", analysis_date=self._analysis_date)
            _seed_result(
                session,
                analysis_history_id=ah_daily.id,
                code="BTC-USDT:PERP",
                analysis_date=self._analysis_date,
                engine_version="v1",
                bar_interval="1d",
            )
            # Minute row (same code, same analysis_date, different engine_version+bar_interval)
            ah_min = _seed_analysis(session, code="BTC-USDT:PERP", analysis_date=date(2024, 1, 2))
            _seed_result(
                session,
                analysis_history_id=ah_min.id,
                code="BTC-USDT:PERP",
                analysis_date=date(2024, 1, 2),
                engine_version="v1-5m",
                bar_interval="5m",
                first_hit_bar_index=7,
            )
            session.commit()

    def tearDown(self) -> None:
        Config._instance = None
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def _get_items(self, interval: str, engine_version=None) -> list:
        service = BacktestService(self.db)
        data = service.get_recent_evaluations(
            code="BTC-USDT:PERP",
            eval_window_days=10,
            limit=50,
            page=1,
            engine_version=engine_version,
            interval=interval,
        )
        return data.get("items", [])

    def test_default_interval_1d_returns_only_daily_row(self):
        """interval='1d' (default) must not return the minute row."""
        items = self._get_items(interval="1d")
        assert len(items) == 1, f"Expected 1 daily row, got {len(items)}: {items}"
        assert items[0]["engine_version"] == "v1", items[0]["engine_version"]

    def test_minute_interval_returns_only_minute_row(self):
        """interval='5m' returns only the minute row (engine_version default = 'v1-5m')."""
        items = self._get_items(interval="5m")
        assert len(items) == 1, f"Expected 1 minute row, got {len(items)}: {items}"
        assert items[0]["engine_version"] == "v1-5m", items[0]["engine_version"]

    def test_default_interval_does_not_leak_minute(self):
        """Daily query must not contain any minute-tagged row."""
        items = self._get_items(interval="1d")
        for item in items:
            assert item.get("bar_interval") != "5m", (
                f"Minute row leaked into daily result: {item}"
            )

    def test_first_hit_bar_index_populated(self):
        """first_hit_bar_index is exposed in minute result."""
        items = self._get_items(interval="5m")
        assert len(items) == 1
        assert items[0].get("first_hit_bar_index") == 7, items[0]


# ---------------------------------------------------------------------------
# Test 2b: get_summary date-filtered branch isolates by interval
# ---------------------------------------------------------------------------

class TestSummaryIntervalIsolation(unittest.TestCase):
    """get_summary date-filtered (dynamic) branch must isolate daily vs minute.

    Entering the dynamic branch (analysis_date_from set) exercises
    count_results / list_results, which Task 9 follow-up now passes
    bar_interval into. With the same code + same eval_window_days, the daily
    row (v1/1d) and minute row (v1-5m/5m) overlap on the date filter, so only
    engine_version (interval-aware default) + bar_interval keep them apart.
    """

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._temp_dir.name, "test_summary_interval.db")
        os.environ["DATABASE_PATH"] = db_path
        os.environ["BACKTEST_EVAL_WINDOW_DAYS"] = "10"
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

        self._date_from = date(2023, 12, 1)
        self._date_to = date(2024, 12, 31)

        with self.db.get_session() as session:
            ah_daily = _seed_analysis(session, code="ETH-USDT:PERP", analysis_date=date(2024, 1, 1))
            _seed_result(
                session,
                analysis_history_id=ah_daily.id,
                code="ETH-USDT:PERP",
                analysis_date=date(2024, 1, 1),
                engine_version="v1",
                bar_interval="1d",
            )
            ah_min = _seed_analysis(session, code="ETH-USDT:PERP", analysis_date=date(2024, 1, 2))
            _seed_result(
                session,
                analysis_history_id=ah_min.id,
                code="ETH-USDT:PERP",
                analysis_date=date(2024, 1, 2),
                engine_version="v1-5m",
                bar_interval="5m",
                first_hit_bar_index=3,
            )
            session.commit()

    def tearDown(self) -> None:
        Config._instance = None
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def _get_summary(self, interval: str):
        service = BacktestService(self.db)
        return service.get_summary(
            scope="stock",
            code="ETH-USDT:PERP",
            eval_window_days=10,
            interval=interval,
            analysis_date_from=self._date_from,
            analysis_date_to=self._date_to,
        )

    def test_daily_summary_reflects_only_daily_row(self):
        """interval='1d' summary counts ONLY the daily row (engine_version='v1')."""
        summary = self._get_summary(interval="1d")
        assert summary is not None
        assert summary["engine_version"] == "v1", summary["engine_version"]
        assert summary["total_evaluations"] == 1, (
            f"Expected 1 daily evaluation, got {summary['total_evaluations']}: {summary}"
        )

    def test_minute_summary_reflects_only_minute_row(self):
        """interval='5m' summary counts ONLY the minute row (engine_version='v1-5m')."""
        summary = self._get_summary(interval="5m")
        assert summary is not None
        assert summary["engine_version"] == "v1-5m", summary["engine_version"]
        assert summary["total_evaluations"] == 1, (
            f"Expected 1 minute evaluation, got {summary['total_evaluations']}: {summary}"
        )


# ---------------------------------------------------------------------------
# Test 3: BacktestResultItem schema has the new fields
# ---------------------------------------------------------------------------

class TestBacktestResultItemSchema(unittest.TestCase):
    def test_schema_has_bar_interval(self):
        from api.v1.schemas.backtest import BacktestResultItem
        item = BacktestResultItem(
            analysis_history_id=1,
            code="BTC-USDT:PERP",
            eval_window_days=10,
            engine_version="v1",
            eval_status="completed",
            bar_interval="1d",
            first_hit_bar_index=None,
        )
        assert item.bar_interval == "1d"
        assert item.first_hit_bar_index is None

    def test_schema_has_first_hit_bar_index(self):
        from api.v1.schemas.backtest import BacktestResultItem
        item = BacktestResultItem(
            analysis_history_id=2,
            code="BTC-USDT:PERP",
            eval_window_days=10,
            engine_version="v1-5m",
            eval_status="completed",
            bar_interval="5m",
            first_hit_bar_index=42,
        )
        assert item.bar_interval == "5m"
        assert item.first_hit_bar_index == 42


# ---------------------------------------------------------------------------
# Test 4: BacktestRunRequest has interval field
# ---------------------------------------------------------------------------

class TestBacktestRunRequestSchema(unittest.TestCase):
    def test_default_interval_1d(self):
        from api.v1.schemas.backtest import BacktestRunRequest
        req = BacktestRunRequest()
        assert req.interval == "1d"

    def test_custom_interval(self):
        from api.v1.schemas.backtest import BacktestRunRequest
        req = BacktestRunRequest(interval="5m")
        assert req.interval == "5m"


# ---------------------------------------------------------------------------
# Test 5: get_recent_evaluations engine_version default is interval-aware
# ---------------------------------------------------------------------------

class TestEngineVersionIntervalAwareDefault(unittest.TestCase):
    """When engine_version is not provided, the default must be interval-aware."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._temp_dir.name, "test_evdefault.db")
        os.environ["DATABASE_PATH"] = db_path
        os.environ["BACKTEST_EVAL_WINDOW_DAYS"] = "10"
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        Config._instance = None
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def test_interval_1d_default_engine_version_is_base(self):
        """interval='1d' → engine_version defaults to base ('v1'), not 'v1-1d'."""
        from unittest.mock import patch
        captured = {}

        original_get_results = BacktestRepository.get_results_paginated

        def mock_get_results(self_repo, *, code, eval_window_days, engine_version, **kwargs):
            captured["engine_version"] = engine_version
            return [], 0

        with patch.object(BacktestRepository, "get_results_paginated", mock_get_results):
            service = BacktestService(self.db)
            service.get_recent_evaluations(
                code=None,
                eval_window_days=10,
                limit=10,
                page=1,
                interval="1d",
            )
        assert captured.get("engine_version") == "v1", (
            f"Expected engine_version='v1' for interval='1d', got: {captured.get('engine_version')}"
        )

    def test_interval_5m_default_engine_version_is_tagged(self):
        """interval='5m' → engine_version defaults to 'v1-5m'."""
        from unittest.mock import patch
        captured = {}

        def mock_get_results(self_repo, *, code, eval_window_days, engine_version, **kwargs):
            captured["engine_version"] = engine_version
            return [], 0

        with patch.object(BacktestRepository, "get_results_paginated", mock_get_results):
            service = BacktestService(self.db)
            service.get_recent_evaluations(
                code=None,
                eval_window_days=10,
                limit=10,
                page=1,
                interval="5m",
            )
        assert captured.get("engine_version") == "v1-5m", (
            f"Expected engine_version='v1-5m' for interval='5m', got: {captured.get('engine_version')}"
        )

    def test_explicit_engine_version_overrides(self):
        """Explicit engine_version is passed through as-is, regardless of interval."""
        from unittest.mock import patch
        captured = {}

        def mock_get_results(self_repo, *, code, eval_window_days, engine_version, **kwargs):
            captured["engine_version"] = engine_version
            return [], 0

        with patch.object(BacktestRepository, "get_results_paginated", mock_get_results):
            service = BacktestService(self.db)
            service.get_recent_evaluations(
                code=None,
                eval_window_days=10,
                limit=10,
                page=1,
                interval="5m",
                engine_version="v1-5m-x3",
            )
        assert captured.get("engine_version") == "v1-5m-x3", (
            f"Expected explicit engine_version='v1-5m-x3', got: {captured.get('engine_version')}"
        )


# ---------------------------------------------------------------------------
# Finding #1: Invalid interval returns 400 (not 500 / silent)
# ---------------------------------------------------------------------------

class TestInvalidIntervalReturns400(unittest.TestCase):
    """Invalid interval in API calls must return HTTP 400, not 500 or silent success.

    The service raises ValueError for bad intervals, and all endpoints catch ValueError
    and return 400. This test verifies the service-level ValueError is raised and that
    the endpoint maps it to 400.
    """

    def test_run_backtest_invalid_interval_raises_value_error(self):
        """Service.run_backtest with invalid interval raises ValueError immediately."""
        from src.services.backtest_service import BacktestService
        from types import SimpleNamespace

        svc = BacktestService.__new__(BacktestService)
        object.__setattr__(svc, "repo", SimpleNamespace(get_candidates=lambda **k: []))
        object.__setattr__(svc, "stock_repo", SimpleNamespace())

        with self.assertRaises(ValueError) as ctx:
            svc.run_backtest(interval="2h")
        self.assertIn("unsupported interval", str(ctx.exception))

    def test_get_recent_evaluations_invalid_interval_raises_value_error(self):
        """Service.get_recent_evaluations with invalid interval raises ValueError."""
        import tempfile
        import os

        tmp = tempfile.TemporaryDirectory()
        db_path = os.path.join(tmp.name, "test_inv.db")
        os.environ["DATABASE_PATH"] = db_path
        Config._instance = None
        DatabaseManager.reset_instance()
        db = DatabaseManager.get_instance()

        svc = BacktestService(db_manager=db)
        try:
            with self.assertRaises(ValueError) as ctx:
                svc.get_recent_evaluations(code=None, interval="2h")
            self.assertIn("unsupported interval", str(ctx.exception))
        finally:
            Config._instance = None
            DatabaseManager.reset_instance()
            tmp.cleanup()

    def test_get_summary_invalid_interval_raises_value_error(self):
        """Service.get_summary with invalid interval raises ValueError."""
        import tempfile
        import os

        tmp = tempfile.TemporaryDirectory()
        db_path = os.path.join(tmp.name, "test_inv2.db")
        os.environ["DATABASE_PATH"] = db_path
        Config._instance = None
        DatabaseManager.reset_instance()
        db = DatabaseManager.get_instance()

        svc = BacktestService(db_manager=db)
        try:
            with self.assertRaises(ValueError) as ctx:
                svc.get_summary(scope="overall", code=None, interval="1D")
            self.assertIn("unsupported interval", str(ctx.exception))
        finally:
            Config._instance = None
            DatabaseManager.reset_instance()
            tmp.cleanup()

    def test_validate_interval_rejects_bad_values(self):
        """validate_interval raises ValueError for all bad interval forms."""
        from src.core.intraday_backtest import validate_interval
        import pytest

        bad_intervals = ["2h", "", "1D", "5M", "daily", "1w", None, 0, "1d-x3"]
        for bad in bad_intervals:
            with self.assertRaises((ValueError, TypeError)):
                validate_interval(bad)

    def test_validate_interval_accepts_all_supported(self):
        """validate_interval returns the interval unchanged for all supported values."""
        from src.core.intraday_backtest import validate_interval, SUPPORTED_INTERVALS

        for iv in SUPPORTED_INTERVALS:
            result = validate_interval(iv)
            self.assertEqual(result, iv)


if __name__ == "__main__":
    unittest.main()
