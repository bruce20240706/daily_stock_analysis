# -*- coding: utf-8 -*-
"""TDD tests for BacktestService interval param integration (Task 5).

All tests are fully offline: real DB / HTTP is never invoked.
"""

import json
import os
import tempfile
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.services.backtest_service import BacktestService


# ---------------------------------------------------------------------------
# helper: build a small minute DataFrame (n bars, starting at 2026-06-01 00:00)
# ---------------------------------------------------------------------------

def _minute_df(n: int, base: float = 100.0) -> pd.DataFrame:
    """Build n x 5m bars (datetime strings, OHLCV)."""
    return pd.DataFrame(
        [
            {
                "datetime": f"2026-06-01 {i // 60:02d}:{i % 60:02d}:00",
                "open": base,
                "high": base + 5,
                "low": base - 5,
                "close": base,
                "volume": 1.0,
            }
            for i in range(n)
        ]
    )


# ---------------------------------------------------------------------------
# helper: build a fake analysis candidate
# ---------------------------------------------------------------------------

def _fake_analysis(code: str = "BTC/USDT", analysis_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=analysis_id,
        code=code,
        operation_advice="买入",
        stop_loss=90.0,
        take_profit=120.0,
        created_at=datetime(2024, 1, 1),
        context_snapshot=json.dumps({"enhanced_context": {"date": "2026-05-01"}}),
    )


# ---------------------------------------------------------------------------
# Test 1: default interval='1d' must NOT call get_intraday_data
# ---------------------------------------------------------------------------

def test_run_backtest_default_interval_is_daily_path(monkeypatch):
    """interval 默认 '1d' → get_intraday_data 不被调用 + processed==0(无候选)。"""
    called = {"intraday": 0}

    from data_provider.base import DataFetcherManager

    monkeypatch.setattr(
        DataFetcherManager,
        "get_intraday_data",
        lambda self, *a, **k: called.__setitem__("intraday", called["intraday"] + 1),
    )

    svc = BacktestService.__new__(BacktestService)
    # No candidates → should return immediately without touching minute path
    # Use object.__setattr__ because __new__ skips __init__ so instance has no attrs yet
    object.__setattr__(svc, "repo", SimpleNamespace(get_candidates=lambda **k: []))
    object.__setattr__(svc, "stock_repo", SimpleNamespace())

    out = svc.run_backtest(interval="1d")

    assert called["intraday"] == 0, "daily path must never call get_intraday_data"
    assert out["processed"] == 0


# ---------------------------------------------------------------------------
# Test 2: interval='5m' tag/window/persistence semantics
# ---------------------------------------------------------------------------

def test_intraday_tag_and_window_and_persistence(monkeypatch, tmp_path):
    """interval='5m' + eval_window_days=2:
    - engine_version tag = 'v1-5m'  (base='v1', no leverage suffix)
    - engine slice = window_bar_cnt = 2*288 = 576 bars
    - persisted eval_window_days = 2 (calendar days, NOT 576)
    - first_hit_bar_index carries engine's first_hit_trading_days value (7)
    - first_hit_trading_days = None  (disambiguation: bar domain, not calendar)
    - bar_interval = '5m'
    """
    import os

    # Need a real DB for save_results_batch → use temp SQLite
    db_path = str(tmp_path / "t5_test.db")
    os.environ["DATABASE_PATH"] = db_path

    from src.config import Config
    from src.storage import DatabaseManager

    Config._instance = None
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()

    svc = BacktestService(db_manager=db)

    # ---- mock get_candidates to return one perp candidate ----
    candidate = _fake_analysis(code="BTC/USDT:PERP", analysis_id=42)
    monkeypatch.setattr(svc.repo, "get_candidates", lambda **k: [candidate])

    # ---- mock _resolve_analysis_date ----
    monkeypatch.setattr(
        svc,
        "_resolve_analysis_date",
        lambda analysis: date(2026, 5, 1),
    )

    # ---- mock get_intraday_data to return 576 bars ----
    fake_df = _minute_df(576, base=100.0)
    from data_provider.base import DataFetcherManager

    monkeypatch.setattr(
        DataFetcherManager,
        "get_intraday_data",
        lambda self, code, interval, **kw: (fake_df.copy(), "BinanceFetcher"),
    )

    # ---- mock BacktestEngine.evaluate_single to return completed + first_hit_trading_days=7 ----
    captured_eval_args: Dict[str, Any] = {}

    def fake_evaluate_single(**kwargs):
        captured_eval_args.update(kwargs)
        return {
            "eval_status": "completed",
            "analysis_date": date(2026, 5, 1),
            "eval_window_days": kwargs["config"].eval_window_days,
            "engine_version": kwargs["config"].engine_version,
            "operation_advice": "买入",
            "position_recommendation": "long",
            "start_price": 100.0,
            "end_close": 110.0,
            "max_high": 115.0,
            "min_low": 95.0,
            "stock_return_pct": 10.0,
            "direction_expected": "up",
            "direction_correct": True,
            "outcome": "win",
            "stop_loss": 90.0,
            "take_profit": 120.0,
            "hit_stop_loss": False,
            "hit_take_profit": False,
            "first_hit": "neither",
            "first_hit_date": None,
            "first_hit_trading_days": 7,   # engine returns bar index here (shared field)
            "simulated_entry_price": 100.0,
            "simulated_exit_price": 110.0,
            "simulated_exit_reason": "window_end",
            "simulated_return_pct": 10.0,
        }

    from src.core import backtest_engine as beng
    monkeypatch.setattr(beng.BacktestEngine, "evaluate_single", staticmethod(fake_evaluate_single))

    # ---- capture save_results_batch ----
    saved_results: List[Any] = []
    real_save = svc.repo.save_results_batch

    def fake_save(results, **kw):
        saved_results.extend(results)
        return len(results)

    monkeypatch.setattr(svc.repo, "save_results_batch", fake_save)
    # mock _recompute_summaries to avoid full DB query
    monkeypatch.setattr(svc, "_recompute_summaries", lambda **k: None)

    # ---- run ----
    out = svc.run_backtest(interval="5m", eval_window_days=2)

    # basic run result
    assert out["processed"] == 1, f"expected 1 processed, got {out}"
    assert out["completed"] == 1

    # check captured BacktestResult
    assert len(saved_results) == 1, "expected exactly 1 BacktestResult saved"
    r = saved_results[0]

    # 1. engine_version tag
    assert r.engine_version == "v1-5m", (
        f"engine_version should be 'v1-5m', got {r.engine_version!r}"
    )

    # 2. engine slice used window_bar_cnt = 2 * 288 = 576
    assert captured_eval_args["config"].eval_window_days == 576, (
        f"engine eval_window_days should be 576 (bar count), got {captured_eval_args['config'].eval_window_days}"
    )

    # 3. persisted eval_window_days = 2 (calendar days, NOT bar count)
    assert r.eval_window_days == 2, (
        f"persisted eval_window_days should be 2 (calendar days), got {r.eval_window_days}"
    )

    # 4. first_hit_bar_index = engine's first_hit_trading_days (7)
    assert r.first_hit_bar_index == 7, (
        f"first_hit_bar_index should be 7 (engine's bar index), got {r.first_hit_bar_index}"
    )

    # 5. first_hit_trading_days = None (disambiguation)
    assert r.first_hit_trading_days is None, (
        f"first_hit_trading_days should be None for minute bars, got {r.first_hit_trading_days}"
    )

    # 6. bar_interval = '5m'
    assert r.bar_interval == "5m", (
        f"bar_interval should be '5m', got {r.bar_interval!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: non-crypto codes are skipped when interval != '1d'
# ---------------------------------------------------------------------------

def test_intraday_non_crypto_skipped(monkeypatch, tmp_path):
    """interval='5m' + A-share code → skipped (not crypto), processed=0."""
    import os

    db_path = str(tmp_path / "t5_skip.db")
    os.environ["DATABASE_PATH"] = db_path

    from src.config import Config
    from src.storage import DatabaseManager

    Config._instance = None
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()

    svc = BacktestService(db_manager=db)

    candidate = _fake_analysis(code="600519", analysis_id=10)
    monkeypatch.setattr(svc.repo, "get_candidates", lambda **k: [candidate])
    monkeypatch.setattr(svc, "_resolve_analysis_date", lambda a: date(2026, 5, 1))

    intraday_called = {"n": 0}
    from data_provider.base import DataFetcherManager

    monkeypatch.setattr(
        DataFetcherManager,
        "get_intraday_data",
        lambda self, *a, **k: intraday_called.__setitem__("n", intraday_called["n"] + 1),
    )

    out = svc.run_backtest(interval="5m", eval_window_days=2)

    assert intraday_called["n"] == 0, "get_intraday_data must not be called for A-share code"
    assert out["processed"] == 0, f"non-crypto should be skipped entirely, got {out}"


# ---------------------------------------------------------------------------
# Test 4: _df_to_bars produces bar namedtuples with correct date type
# ---------------------------------------------------------------------------

def test_df_to_bars_shape_and_date():
    """_df_to_bars converts minute DataFrame rows to bar objects.
    Each bar has: date, high, low, close, open.
    bar.date must be a plain Python date (not pd.Timestamp / datetime.datetime).
    """
    from datetime import date as date_cls, datetime as dt_cls
    import pandas as pd

    df = _minute_df(3)
    # Ensure the 'datetime' column contains pd.Timestamps as produced by _normalize_intraday
    df["datetime"] = pd.to_datetime(df["datetime"])

    bars = BacktestService._df_to_bars(df)
    assert len(bars) == 3
    b = bars[0]
    assert hasattr(b, "date")
    assert hasattr(b, "high")
    assert hasattr(b, "low")
    assert hasattr(b, "close")
    assert hasattr(b, "open")
    assert b.high == 105.0
    assert b.low == 95.0
    assert b.close == 100.0
    assert b.open == 100.0

    # ★ FINDING #2: date must be a plain Python date, NOT pd.Timestamp or datetime
    assert isinstance(b.date, date_cls), (
        f"bar.date must be a Python date, got {type(b.date)}"
    )
    assert not isinstance(b.date, dt_cls), (
        f"bar.date must NOT be a datetime (pd.Timestamp IS datetime subclass), got {type(b.date)}"
    )
    # type() is exact check — pd.Timestamp is NOT `date`, it's `datetime`
    assert type(b.date) is date_cls, (
        f"bar.date type must be exactly datetime.date, got {type(b.date)}"
    )


# ---------------------------------------------------------------------------
# Test 5: non-crypto skip uses skipped_unsupported counter (not skipped_non_perp)
# ---------------------------------------------------------------------------

def test_intraday_non_crypto_skip_counter(monkeypatch, tmp_path):
    """interval='5m' + A-share code → skipped_unsupported counter incremented,
    and 'skipped_unsupported' key present in return dict.
    """
    import os

    db_path = str(tmp_path / "t5_counter.db")
    os.environ["DATABASE_PATH"] = db_path

    from src.config import Config
    from src.storage import DatabaseManager

    Config._instance = None
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()

    svc = BacktestService(db_manager=db)

    candidate = _fake_analysis(code="600519", analysis_id=20)
    monkeypatch.setattr(svc.repo, "get_candidates", lambda **k: [candidate])
    monkeypatch.setattr(svc, "_resolve_analysis_date", lambda a: date(2026, 5, 1))

    out = svc.run_backtest(interval="5m", eval_window_days=2)

    assert out["processed"] == 0, "non-crypto must not be processed"
    assert "skipped_unsupported" in out, (
        "'skipped_unsupported' key must be in return dict for non-crypto minute skips"
    )
    assert out["skipped_unsupported"] == 1, (
        f"skipped_unsupported should be 1 for one A-share code, got {out.get('skipped_unsupported')}"
    )


# ---------------------------------------------------------------------------
# Test 6: service passes start_date to get_intraday_data (historical anchor)
# ---------------------------------------------------------------------------

def test_intraday_service_passes_start_date(monkeypatch, tmp_path):
    """interval='5m': service must pass start_date (the candidate's analysis window start)
    to get_intraday_data, so the fetch is historically anchored — not anchored to 'now'.
    """
    import os

    db_path = str(tmp_path / "t5_anchor.db")
    os.environ["DATABASE_PATH"] = db_path

    from src.config import Config
    from src.storage import DatabaseManager

    Config._instance = None
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()

    svc = BacktestService(db_manager=db)

    candidate = _fake_analysis(code="BTC/USDT:PERP", analysis_id=99)
    analysis_date = date(2024, 1, 15)
    monkeypatch.setattr(svc.repo, "get_candidates", lambda **k: [candidate])
    monkeypatch.setattr(svc, "_resolve_analysis_date", lambda a: analysis_date)

    captured_kwargs: Dict[str, Any] = {}
    fake_df = _minute_df(288, base=100.0)

    from data_provider.base import DataFetcherManager

    def fake_get_intraday(self, code, interval, **kw):
        captured_kwargs.update(kw)
        return fake_df.copy(), "BinanceFetcher"

    monkeypatch.setattr(DataFetcherManager, "get_intraday_data", fake_get_intraday)

    # Mock engine so we don't need real bars to evaluate
    from src.core import backtest_engine as beng

    def fake_evaluate_single(**kwargs):
        return {
            "eval_status": "completed",
            "analysis_date": analysis_date,
            "eval_window_days": kwargs["config"].eval_window_days,
            "engine_version": kwargs["config"].engine_version,
            "operation_advice": "买入",
            "position_recommendation": "long",
            "start_price": 100.0,
            "end_close": 110.0,
            "max_high": 115.0,
            "min_low": 95.0,
            "stock_return_pct": 10.0,
            "direction_expected": "up",
            "direction_correct": True,
            "outcome": "win",
            "stop_loss": 90.0,
            "take_profit": 120.0,
            "hit_stop_loss": False,
            "hit_take_profit": False,
            "first_hit": "neither",
            "first_hit_date": None,
            "first_hit_trading_days": 3,
            "simulated_entry_price": 100.0,
            "simulated_exit_price": 110.0,
            "simulated_exit_reason": "window_end",
            "simulated_return_pct": 10.0,
        }

    monkeypatch.setattr(beng.BacktestEngine, "evaluate_single", staticmethod(fake_evaluate_single))
    monkeypatch.setattr(svc.repo, "save_results_batch", lambda results, **kw: len(results))
    monkeypatch.setattr(svc, "_recompute_summaries", lambda **k: None)

    out = svc.run_backtest(interval="5m", eval_window_days=1)

    assert "start_date" in captured_kwargs, (
        "service must pass start_date kwarg to get_intraday_data; "
        f"got kwargs={list(captured_kwargs.keys())}"
    )
    # The start_date must equal the candidate's analysis_date (window start)
    from datetime import date as date_cls
    passed_start = captured_kwargs["start_date"]
    # Accept date or ISO string representation
    if isinstance(passed_start, str):
        passed_start = date_cls.fromisoformat(passed_start)
    assert passed_start == analysis_date, (
        f"start_date passed to get_intraday_data must be analysis_date={analysis_date}, "
        f"got {captured_kwargs['start_date']}"
    )
