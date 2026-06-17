# -*- coding: utf-8 -*-
"""Task A5: SignalBacktestService 单元测试（TDD Step 1 — RED first）。"""
import os
from unittest.mock import patch, MagicMock

from src.services.signal_backtest_service import SignalBacktestService
from src.services.signal_backtest import SignalOutcome
from src.services.volume_price_signals import VPSConfig


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


def test_a5_config_passthrough_uses_for_market_per_code(monkeypatch):
    """I1/I2 回归：A5 批作业必须用 VPSConfig.for_market(market) 透传 config 给每个 evaluate_*。

    - crypto code 收到 config.breakout_window == VPSConfig.for_market('crypto').breakout_window
    - cn code 收到 config.breakout_window == VPSConfig.for_market('cn').breakout_window
    若 fix #1 被回退（不传 config= 或用 from_env()），当 VPS_CRYPTO_BREAKOUT_WINDOW ≠ VPS_BREAKOUT_WINDOW
    时，此测试必须失败。
    """
    monkeypatch.setenv("VPS_BREAKOUT_WINDOW", "20")
    monkeypatch.setenv("VPS_CRYPTO_BREAKOUT_WINDOW", "7")  # ≠ A-stock default → 触发差异

    captured_configs: list = []

    def spy_eval_sig(df, *, market, horizon, config=None, **kw):
        captured_configs.append(("sig", market, config))
        return []

    def spy_eval_base(df, *, market, horizon, config=None, **kw):
        captured_configs.append(("base", market, config))
        return []

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
        side_effect=spy_eval_sig,
    ), patch(
        "src.services.signal_backtest_service.evaluate_baseline_outcomes",
        side_effect=spy_eval_base,
    ), patch(
        "src.services.signal_backtest_service.SignalStatsRepository"
    ) as Repo:
        SS.return_value.get_history_data.return_value = _hist()
        Repo.return_value.save_batch.return_value = 0
        SignalBacktestService().run(horizon=10)

    # cn code → config.breakout_window == for_market('cn').breakout_window (== from_env, 20)
    cn_sig = next(c for c in captured_configs if c[0] == "sig" and c[1] == "cn")
    assert cn_sig[2] is not None
    assert cn_sig[2].breakout_window == VPSConfig.for_market("cn").breakout_window

    # crypto code → config.breakout_window == for_market('crypto').breakout_window (== 7)
    crypto_sig = next(c for c in captured_configs if c[0] == "sig" and c[1] == "crypto")
    assert crypto_sig[2] is not None
    assert crypto_sig[2].breakout_window == VPSConfig.for_market("crypto").breakout_window
    assert crypto_sig[2].breakout_window == 7  # 明确断言 crypto 值不是 A-stock 默认
