"""
Tests for opt-in scheduled intraday backtest hook (Task 8).
Only registers when INTRADAY_BACKTEST_ENABLED=true; defaults to off.
"""
from types import SimpleNamespace

from src.scheduler_wiring import maybe_register_intraday_backtest


def _fake_scheduler():
    calls = []
    return SimpleNamespace(add_background_task=lambda **k: calls.append(k), _calls=calls)


def test_disabled_by_default_not_registered():
    sched = _fake_scheduler()
    cfg = SimpleNamespace(
        intraday_backtest_enabled=False,
        intraday_backtest_schedule_minutes=60,
        crypto_intraday_backtest_interval="5m",
    )
    maybe_register_intraday_backtest(sched, cfg)
    assert sched._calls == []


def test_enabled_registers_with_interval_seconds():
    sched = _fake_scheduler()
    cfg = SimpleNamespace(
        intraday_backtest_enabled=True,
        intraday_backtest_schedule_minutes=30,
        crypto_intraday_backtest_interval="5m",
    )
    maybe_register_intraday_backtest(sched, cfg)
    assert len(sched._calls) == 1
    assert sched._calls[0]["interval_seconds"] == 30 * 60
