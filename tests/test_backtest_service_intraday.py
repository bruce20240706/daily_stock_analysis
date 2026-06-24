# -*- coding: utf-8 -*-
"""TDD tests for BacktestService interval param integration (Task 5).

All tests are fully offline: real DB / HTTP is never invoked.
"""

import json
import os
import tempfile
from datetime import date, datetime, timedelta
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
    analysis_date_val = date(2026, 5, 1)
    monkeypatch.setattr(
        svc,
        "_resolve_analysis_date",
        lambda analysis: analysis_date_val,
    )

    # ---- mock get_start_daily (Fix #2: intraday path now fetches daily bar for entry price) ----
    _fake_daily_t2 = SimpleNamespace(date=analysis_date_val, close=100.0)
    monkeypatch.setattr(svc.stock_repo, "get_start_daily", lambda code, analysis_date: _fake_daily_t2)

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
            "analysis_date": analysis_date_val,
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
    """interval='5m' + 港股 code → skipped (分钟路径暂不支持港股), processed=0。

    A股/美股已纳入分钟路径，故此处改用仍不支持的港股码验证"非支持市场被整体跳过"。
    """
    import os

    db_path = str(tmp_path / "t5_skip.db")
    os.environ["DATABASE_PATH"] = db_path

    from src.config import Config
    from src.storage import DatabaseManager

    Config._instance = None
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()

    svc = BacktestService(db_manager=db)

    candidate = _fake_analysis(code="HK00700", analysis_id=10)
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

    assert intraday_called["n"] == 0, "get_intraday_data must not be called for 港股 code"
    assert out["processed"] == 0, f"非支持市场应被整体跳过, got {out}"


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
    """interval='5m' + 港股 code → skipped_unsupported 计数 +1,且返回 dict 含该键。

    A股/美股已支持,改用仍不支持的港股码验证 skipped_unsupported 计数路径。
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

    candidate = _fake_analysis(code="HK00700", analysis_id=20)
    monkeypatch.setattr(svc.repo, "get_candidates", lambda **k: [candidate])
    monkeypatch.setattr(svc, "_resolve_analysis_date", lambda a: date(2026, 5, 1))

    out = svc.run_backtest(interval="5m", eval_window_days=2)

    assert out["processed"] == 0, "非支持市场不应被处理"
    assert "skipped_unsupported" in out, (
        "'skipped_unsupported' key must be in return dict for unsupported-market minute skips"
    )
    assert out["skipped_unsupported"] == 1, (
        f"skipped_unsupported should be 1 for one 美股 code, got {out.get('skipped_unsupported')}"
    )


# ---------------------------------------------------------------------------
# Test 6: service passes start_date to get_intraday_data (historical anchor)
# After Fix #2: start_date = analysis_date + 1 day (window starts after daily close)
# ---------------------------------------------------------------------------

def test_intraday_service_passes_start_date(monkeypatch, tmp_path):
    """interval='5m': service must pass start_date to get_intraday_data.
    After Fix #2: the start_date must be analysis_date + 1 day (the day AFTER daily bar close),
    not analysis_date itself. This anchors the minute window after the daily close.
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
    # Fix #2: window start = analysis_date + 1 day
    expected_window_start = analysis_date + timedelta(days=1)  # 2024-01-16
    monkeypatch.setattr(svc.repo, "get_candidates", lambda **k: [candidate])
    monkeypatch.setattr(svc, "_resolve_analysis_date", lambda a: analysis_date)

    # Fix #2: mock get_start_daily to return a daily bar
    _fake_daily_t6 = SimpleNamespace(date=analysis_date, close=105.0)
    monkeypatch.setattr(svc.stock_repo, "get_start_daily", lambda code, analysis_date: _fake_daily_t6)

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
    # Fix #2: The start_date must equal analysis_date + 1 day (window starts AFTER daily close)
    from datetime import date as date_cls
    passed_start = captured_kwargs["start_date"]
    # Accept date or ISO string representation
    if isinstance(passed_start, str):
        passed_start = date_cls.fromisoformat(passed_start)
    assert passed_start == expected_window_start, (
        f"Fix #2: start_date passed to get_intraday_data must be analysis_date+1={expected_window_start} "
        f"(window starts AFTER daily bar close), got {captured_kwargs['start_date']}"
    )


# ---------------------------------------------------------------------------
# Task 6: 成本可选后处理
# ---------------------------------------------------------------------------

def _make_intraday_svc_with_engine_return(monkeypatch, tmp_path, engine_return_pct: float, fee_bps: float = 0.0, slippage_bps: float = 0.0):
    """Helper shared by Task-6 cost tests.

    Returns (svc, saved_results_list).
    The mock engine returns simulated_return_pct=engine_return_pct.
    Config's fee/slip are set via monkeypatch on the Config instance.
    """
    import os

    db_path = str(tmp_path / "t6_cost.db")
    os.environ["DATABASE_PATH"] = db_path

    from src.config import Config
    from src.storage import DatabaseManager

    Config._instance = None
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()

    svc = BacktestService(db_manager=db)

    # Patch config's fee/slip attributes via get_config
    original_get_config = None
    from src import config as _config_mod
    real_cfg = _config_mod.get_config()
    monkeypatch.setattr(real_cfg, "crypto_intraday_backtest_fee_bps", fee_bps)
    monkeypatch.setattr(real_cfg, "crypto_intraday_backtest_slippage_bps", slippage_bps)

    candidate = _fake_analysis(code="BTC/USDT:PERP", analysis_id=101)
    analysis_date = date(2026, 5, 1)
    monkeypatch.setattr(svc.repo, "get_candidates", lambda **k: [candidate])
    monkeypatch.setattr(svc, "_resolve_analysis_date", lambda a: analysis_date)

    # Fix #2: mock get_start_daily so intraday path can obtain daily close for entry price
    _fake_daily_t6_cost = SimpleNamespace(date=analysis_date, close=100.0)
    monkeypatch.setattr(svc.stock_repo, "get_start_daily", lambda code, analysis_date: _fake_daily_t6_cost)

    fake_df = _minute_df(288, base=100.0)
    from data_provider.base import DataFetcherManager

    monkeypatch.setattr(
        DataFetcherManager,
        "get_intraday_data",
        lambda self, code, interval, **kw: (fake_df.copy(), "BinanceFetcher"),
    )

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
            "stock_return_pct": engine_return_pct,
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
            "simulated_return_pct": engine_return_pct,
        }

    monkeypatch.setattr(beng.BacktestEngine, "evaluate_single", staticmethod(fake_evaluate_single))

    saved_results: List[Any] = []

    monkeypatch.setattr(svc.repo, "save_results_batch", lambda results, **kw: (saved_results.extend(results), len(results))[1])
    monkeypatch.setattr(svc, "_recompute_summaries", lambda **k: None)

    return svc, saved_results


def test_cost_zero_keeps_return_unchanged(monkeypatch, tmp_path):
    """fee=0, slip=0 → persisted simulated_return_pct equals engine's value (no mutation)."""
    engine_val = 10.0
    svc, saved_results = _make_intraday_svc_with_engine_return(
        monkeypatch, tmp_path, engine_return_pct=engine_val, fee_bps=0.0, slippage_bps=0.0
    )

    out = svc.run_backtest(interval="5m", eval_window_days=1)

    assert out["completed"] == 1, f"expected completed=1, got {out}"
    assert len(saved_results) == 1, f"expected 1 saved result, got {len(saved_results)}"
    r = saved_results[0]
    assert r.simulated_return_pct == engine_val, (
        f"fee=0/slip=0: persisted simulated_return_pct should equal engine value {engine_val}, "
        f"got {r.simulated_return_pct}"
    )


def test_cost_positive_deducts_round_trip(monkeypatch, tmp_path):
    """fee=5bp, slip=5bp → persisted simulated_return_pct = engine value − 0.20 pct points.

    One round trip = 2 legs × (fee_bps + slippage_bps) / 100
                   = 2 × (5 + 5) / 100 = 0.20 pct points.
    """
    engine_val = 10.0
    fee_bps = 5.0
    slip_bps = 5.0
    expected = engine_val - 2.0 * (fee_bps + slip_bps) / 100.0  # 10.0 - 0.20 = 9.80

    svc, saved_results = _make_intraday_svc_with_engine_return(
        monkeypatch, tmp_path, engine_return_pct=engine_val, fee_bps=fee_bps, slippage_bps=slip_bps
    )

    out = svc.run_backtest(interval="5m", eval_window_days=1)

    assert out["completed"] == 1, f"expected completed=1, got {out}"
    assert len(saved_results) == 1, f"expected 1 saved result, got {len(saved_results)}"
    r = saved_results[0]
    assert abs(r.simulated_return_pct - expected) < 1e-9, (
        f"fee=5bp/slip=5bp: expected simulated_return_pct={expected}, "
        f"got {r.simulated_return_pct}"
    )


# ---------------------------------------------------------------------------
# Finding #1: validate_interval regression tests
# ---------------------------------------------------------------------------

def test_invalid_interval_2h_raises_value_error():
    """interval='2h' must raise ValueError at service level — not silently fallback to daily."""
    svc = BacktestService.__new__(BacktestService)
    object.__setattr__(svc, "repo", SimpleNamespace(get_candidates=lambda **k: []))
    object.__setattr__(svc, "stock_repo", SimpleNamespace())

    with pytest.raises(ValueError, match="unsupported interval"):
        svc.run_backtest(interval="2h")


def test_invalid_interval_empty_raises_value_error():
    """interval='' must raise ValueError at service level."""
    svc = BacktestService.__new__(BacktestService)
    object.__setattr__(svc, "repo", SimpleNamespace(get_candidates=lambda **k: []))
    object.__setattr__(svc, "stock_repo", SimpleNamespace())

    with pytest.raises(ValueError, match="unsupported interval"):
        svc.run_backtest(interval="")


def test_invalid_interval_1D_raises_value_error():
    """interval='1D' (uppercase) must raise ValueError — strict reject, no coercion."""
    svc = BacktestService.__new__(BacktestService)
    object.__setattr__(svc, "repo", SimpleNamespace(get_candidates=lambda **k: []))
    object.__setattr__(svc, "stock_repo", SimpleNamespace())

    with pytest.raises(ValueError, match="unsupported interval"):
        svc.run_backtest(interval="1D")


def test_invalid_interval_5M_raises_value_error():
    """interval='5M' (uppercase) must raise ValueError — strict reject."""
    svc = BacktestService.__new__(BacktestService)
    object.__setattr__(svc, "repo", SimpleNamespace(get_candidates=lambda **k: []))
    object.__setattr__(svc, "stock_repo", SimpleNamespace())

    with pytest.raises(ValueError, match="unsupported interval"):
        svc.run_backtest(interval="5M")


def test_invalid_interval_does_not_touch_daily_db(monkeypatch, tmp_path):
    """A rejected interval call must NOT write any BacktestResult (daily rows untouched)."""
    import os

    db_path = str(tmp_path / "t_invalid.db")
    os.environ["DATABASE_PATH"] = db_path

    from src.config import Config
    from src.storage import DatabaseManager

    Config._instance = None
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()

    svc = BacktestService(db_manager=db)

    # Provide a candidate so we know the rejection happens before candidate processing
    candidate = _fake_analysis(code="BTC/USDT:PERP", analysis_id=77)
    monkeypatch.setattr(svc.repo, "get_candidates", lambda **k: [candidate])

    save_called = {"n": 0}
    monkeypatch.setattr(
        svc.repo,
        "save_results_batch",
        lambda results, **kw: save_called.__setitem__("n", save_called["n"] + len(results)) or 0,
    )

    with pytest.raises(ValueError, match="unsupported interval"):
        svc.run_backtest(interval="2h")

    assert save_called["n"] == 0, (
        "save_results_batch must NOT be called after interval validation error"
    )


# ---------------------------------------------------------------------------
# Finding #2: minute entry price == daily close; window start = analysis_date + 1 day
# ---------------------------------------------------------------------------

def test_intraday_entry_price_is_daily_close(monkeypatch, tmp_path):
    """Finding #2: minute path must use daily bar close as start_price (NOT first minute bar close).

    Daily bar close = 105.0 (stubbed get_start_daily).
    First minute bar close = 100.0 (the minute DataFrame).
    After fix: start_price passed to engine must be 105.0.
    """
    import os

    db_path = str(tmp_path / "t_fix2_price.db")
    os.environ["DATABASE_PATH"] = db_path

    from src.config import Config
    from src.storage import DatabaseManager

    Config._instance = None
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()

    svc = BacktestService(db_manager=db)

    candidate = _fake_analysis(code="BTC/USDT:PERP", analysis_id=200)
    analysis_date = date(2024, 1, 15)
    monkeypatch.setattr(svc.repo, "get_candidates", lambda **k: [candidate])
    monkeypatch.setattr(svc, "_resolve_analysis_date", lambda a: analysis_date)

    # Stub daily bar: close=105.0 (intentionally different from first minute bar)
    fake_daily_bar = SimpleNamespace(date=analysis_date, close=105.0)
    monkeypatch.setattr(svc.stock_repo, "get_start_daily", lambda code, analysis_date: fake_daily_bar)

    # Minute df: first bar close = 100.0 (must NOT be used as start_price)
    fake_df = _minute_df(288, base=100.0)
    from data_provider.base import DataFetcherManager

    monkeypatch.setattr(
        DataFetcherManager,
        "get_intraday_data",
        lambda self, code, interval, **kw: (fake_df.copy(), "BinanceFetcher"),
    )

    captured_eval_args: Dict[str, Any] = {}
    from src.core import backtest_engine as beng

    def fake_evaluate_single(**kwargs):
        captured_eval_args.update(kwargs)
        return {
            "eval_status": "completed",
            "analysis_date": analysis_date,
            "eval_window_days": kwargs["config"].eval_window_days,
            "engine_version": kwargs["config"].engine_version,
            "operation_advice": "买入",
            "position_recommendation": "long",
            "start_price": kwargs["start_price"],
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
            "simulated_entry_price": kwargs["start_price"],
            "simulated_exit_price": 110.0,
            "simulated_exit_reason": "window_end",
            "simulated_return_pct": 10.0,
        }

    monkeypatch.setattr(beng.BacktestEngine, "evaluate_single", staticmethod(fake_evaluate_single))
    monkeypatch.setattr(svc.repo, "save_results_batch", lambda results, **kw: len(results))
    monkeypatch.setattr(svc, "_recompute_summaries", lambda **k: None)

    out = svc.run_backtest(interval="5m", eval_window_days=1)

    assert out["completed"] == 1, f"expected completed=1, got {out}"
    actual_start_price = captured_eval_args.get("start_price")
    assert actual_start_price == 105.0, (
        f"Finding #2: minute path start_price must equal daily close (105.0), "
        f"got {actual_start_price!r}. "
        f"The first minute bar close (100.0) must NOT be used as entry price."
    )


def test_intraday_window_start_is_after_analysis_date(monkeypatch, tmp_path):
    """Finding #2: minute window start must be analysis_date + 1 day (day after daily close).

    analysis_date = 2024-01-15 → minute window start = 2024-01-16 (00:00 UTC).
    The start_date passed to get_intraday_data must equal '2024-01-16', not '2024-01-15'.
    """
    import os

    db_path = str(tmp_path / "t_fix2_window.db")
    os.environ["DATABASE_PATH"] = db_path

    from src.config import Config
    from src.storage import DatabaseManager

    Config._instance = None
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()

    svc = BacktestService(db_manager=db)

    candidate = _fake_analysis(code="BTC/USDT:PERP", analysis_id=201)
    analysis_date = date(2024, 1, 15)
    expected_window_start = date(2024, 1, 16)  # day AFTER analysis_date
    monkeypatch.setattr(svc.repo, "get_candidates", lambda **k: [candidate])
    monkeypatch.setattr(svc, "_resolve_analysis_date", lambda a: analysis_date)

    fake_daily_bar = SimpleNamespace(date=analysis_date, close=105.0)
    monkeypatch.setattr(svc.stock_repo, "get_start_daily", lambda code, analysis_date: fake_daily_bar)

    captured_intraday_kwargs: Dict[str, Any] = {}
    fake_df = _minute_df(288, base=100.0)
    from data_provider.base import DataFetcherManager

    def fake_get_intraday(self, code, interval, **kw):
        captured_intraday_kwargs.update(kw)
        return fake_df.copy(), "BinanceFetcher"

    monkeypatch.setattr(DataFetcherManager, "get_intraday_data", fake_get_intraday)

    from src.core import backtest_engine as beng

    def fake_evaluate_single(**kwargs):
        return {
            "eval_status": "completed",
            "analysis_date": analysis_date,
            "eval_window_days": kwargs["config"].eval_window_days,
            "engine_version": kwargs["config"].engine_version,
            "operation_advice": "买入",
            "position_recommendation": "long",
            "start_price": kwargs["start_price"],
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
            "simulated_entry_price": kwargs["start_price"],
            "simulated_exit_price": 110.0,
            "simulated_exit_reason": "window_end",
            "simulated_return_pct": 10.0,
        }

    monkeypatch.setattr(beng.BacktestEngine, "evaluate_single", staticmethod(fake_evaluate_single))
    monkeypatch.setattr(svc.repo, "save_results_batch", lambda results, **kw: len(results))
    monkeypatch.setattr(svc, "_recompute_summaries", lambda **k: None)

    out = svc.run_backtest(interval="5m", eval_window_days=1)

    assert out["completed"] == 1, f"expected completed=1, got {out}"

    passed_start_date = captured_intraday_kwargs.get("start_date")
    assert passed_start_date is not None, (
        "start_date must be passed to get_intraday_data"
    )
    # Accept date or ISO string
    from datetime import date as date_cls
    if isinstance(passed_start_date, str):
        passed_date = date_cls.fromisoformat(passed_start_date)
    else:
        passed_date = passed_start_date

    assert passed_date == expected_window_start, (
        f"Finding #2: minute window start must be analysis_date + 1 day = {expected_window_start}, "
        f"got {passed_date!r}. The window must start AFTER the daily bar close."
    )
