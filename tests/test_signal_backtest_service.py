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


# === 链路B 分钟化:run(interval=) + 分钟取数分叉(B-T2)===
import pandas as pd
import pytest
from types import SimpleNamespace
from src.services import signal_backtest_service as sbs
from src.services.signal_backtest import SignalOutcome, BASELINE_SIGNAL_TYPE


def _minute_df(n=120, base=100.0):
    return pd.DataFrame([
        {"datetime": pd.Timestamp("2026-06-22 09:30:00") + pd.Timedelta(minutes=5 * i),
         "open": base, "high": base + 1, "low": base - 1, "close": base, "volume": 1.0}
        for i in range(n)
    ])


def test_run_interval_5m_uses_intraday_and_tags(monkeypatch):
    captured = {}
    svc = sbs.SignalBacktestService.__new__(sbs.SignalBacktestService)
    saved = []
    svc.repo = SimpleNamespace(save_batch=lambda rows, **k: (saved.extend(rows), len(rows))[1])

    monkeypatch.setattr(sbs, "_read_watchlist_codes", lambda s: ["BTC/USDT"])
    monkeypatch.setattr(sbs, "get_market_for_stock", lambda code: "crypto")

    from data_provider.base import DataFetcherManager
    def fake_intraday(self, code, interval, **kw):
        captured["interval"] = interval
        return _minute_df(), "BinanceFetcher"
    monkeypatch.setattr(DataFetcherManager, "get_intraday_data", fake_intraday)
    # StockService 日线路径不得被调用
    monkeypatch.setattr(sbs.StockService, "get_history_data",
                        lambda *a, **k: pytest.fail("daily path must not run for 5m"))

    # 捕获传入 evaluate_* 的 df(验证 datetime→date 适配)与 aggregate 的 interval
    seen = {}
    monkeypatch.setattr(sbs, "evaluate_signal_outcomes",
                        lambda df, **k: (seen.update(cols=set(df.columns)),
                                         [SignalOutcome("vps_x", "crypto", "win")])[1])
    monkeypatch.setattr(sbs, "evaluate_baseline_outcomes",
                        lambda df, **k: [SignalOutcome(BASELINE_SIGNAL_TYPE, "crypto", "win"),
                                         SignalOutcome(BASELINE_SIGNAL_TYPE, "crypto", "loss")])

    out = svc.run(interval="5m")
    assert captured["interval"] == "5m"
    assert "date" in seen["cols"] and "datetime" not in seen["cols"]   # datetime→date 适配
    assert any(getattr(r, "interval", None) == "5m" for r in saved)    # 落库行 interval=5m
    assert out["processed"] == 1


def test_run_interval_1d_uses_daily_path(monkeypatch):
    svc = sbs.SignalBacktestService.__new__(sbs.SignalBacktestService)
    svc.repo = SimpleNamespace(save_batch=lambda rows, **k: len(rows))
    monkeypatch.setattr(sbs, "_read_watchlist_codes", lambda s: ["600519"])
    monkeypatch.setattr(sbs, "get_market_for_stock", lambda code: "cn")
    daily = {"data": [{"date": f"2024-01-{d:02d}", "open": 10, "high": 11, "low": 9,
                       "close": 10, "volume": 100} for d in range(1, 28)] * 3}
    monkeypatch.setattr(sbs.StockService, "get_history_data", lambda self, **k: daily)
    from data_provider.base import DataFetcherManager
    monkeypatch.setattr(DataFetcherManager, "get_intraday_data",
                        lambda *a, **k: pytest.fail("intraday path must not run for 1d"))
    out = svc.run(interval="1d")        # 默认日线路径,不触发 get_intraday_data
    assert out["processed"] >= 0


def test_run_rejects_bad_interval():
    svc = sbs.SignalBacktestService.__new__(sbs.SignalBacktestService)
    svc.repo = SimpleNamespace(save_batch=lambda rows, **k: len(rows))
    with pytest.raises(ValueError):
        svc.run(interval="2h")
