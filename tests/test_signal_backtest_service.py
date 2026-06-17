# -*- coding: utf-8 -*-
"""Task A5: SignalBacktestService 单元测试（TDD Step 1 — RED first）。"""
from unittest.mock import patch, MagicMock

from src.services.signal_backtest_service import SignalBacktestService
from src.services.signal_backtest import SignalOutcome


def _hist():
    return {
        "data": [
            {
                "date": f"2026-01-{i + 1:02d}",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 1_000_000,
            }
            for i in range(80)
        ]
    }


def test_run_aggregates_watchlist_and_writes_stats():
    with patch(
        "src.services.signal_backtest_service._read_watchlist_codes",
        return_value=["600519", "BTC/USDT"],
    ), patch(
        "src.services.signal_backtest_service.StockService"
    ) as SS, patch(
        "src.services.signal_backtest_service.get_market_for_stock",
        side_effect=["cn", "crypto"],
    ), patch(
        "src.services.signal_backtest_service.evaluate_signal_outcomes",
        return_value=[SignalOutcome("volume_breakout", "cn", "win")],
    ), patch(
        "src.services.signal_backtest_service.evaluate_baseline_outcomes",
        return_value=[SignalOutcome("__baseline__", "cn", "loss")],
    ), patch(
        "src.services.signal_backtest_service.SignalStatsRepository"
    ) as Repo:
        SS.return_value.get_history_data.return_value = _hist()
        Repo.return_value.save_batch.return_value = 1
        out = SignalBacktestService().run(horizon=10)
        assert out["processed"] == 2
        assert Repo.return_value.save_batch.call_count == 1


def test_run_skips_unknown_market_and_continues_on_error():
    with patch(
        "src.services.signal_backtest_service._read_watchlist_codes",
        return_value=["X", "600519"],
    ), patch(
        "src.services.signal_backtest_service.StockService"
    ) as SS, patch(
        "src.services.signal_backtest_service.get_market_for_stock",
        side_effect=[None, "cn"],
    ), patch(
        "src.services.signal_backtest_service.evaluate_signal_outcomes",
        return_value=[],
    ), patch(
        "src.services.signal_backtest_service.evaluate_baseline_outcomes",
        return_value=[],
    ), patch(
        "src.services.signal_backtest_service.SignalStatsRepository"
    ):
        SS.return_value.get_history_data.return_value = _hist()
        out = SignalBacktestService().run(horizon=10)
        assert out["skipped"] >= 1 and out["processed"] >= 1  # 未知市场跳过，其余继续


def test_run_continues_and_counts_error_when_a_code_raises():
    with patch(
        "src.services.signal_backtest_service._read_watchlist_codes",
        return_value=["000001", "600519"],
    ), patch(
        "src.services.signal_backtest_service.StockService"
    ) as SS, patch(
        "src.services.signal_backtest_service.get_market_for_stock",
        side_effect=["cn", "cn"],
    ), patch(
        "src.services.signal_backtest_service.evaluate_signal_outcomes",
        return_value=[SignalOutcome("volume_breakout", "cn", "win")],
    ), patch(
        "src.services.signal_backtest_service.evaluate_baseline_outcomes",
        return_value=[SignalOutcome("__baseline__", "cn", "loss")],
    ), patch(
        "src.services.signal_backtest_service.SignalStatsRepository"
    ) as Repo:
        SS.return_value.get_history_data.side_effect = [
            Exception("boom"),
            _hist(),
        ]
        Repo.return_value.save_batch.return_value = 1
        out = SignalBacktestService().run(horizon=10)
        assert out["errors"] == 1       # 第一个 code 抛异常计入 errors
        assert out["processed"] == 1    # 第二个 code 仍成功处理
        assert Repo.return_value.save_batch.call_count == 1  # 批次仍落库
