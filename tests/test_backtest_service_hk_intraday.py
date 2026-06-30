# -*- coding: utf-8 -*-
"""港股分钟回测 service 集成:放行 HK + 市场化窗口(330/日)。全程离线 mock。"""
import json
import os
from datetime import date
from types import SimpleNamespace
from typing import Any, Dict, List

import pandas as pd

from src.services.backtest_service import BacktestService


def _minute_df(n: int, base: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame([
        {"datetime": f"2026-05-06 {9 + i // 60:02d}:{i % 60:02d}:00",
         "open": base, "high": base + 1, "low": base - 1, "close": base, "volume": 1.0}
        for i in range(n)
    ])


def _completed_eval(**kwargs):
    return {
        "eval_status": "completed", "analysis_date": kwargs.get("analysis_date"),
        "eval_window_days": kwargs["config"].eval_window_days,
        "engine_version": kwargs["config"].engine_version,
        "operation_advice": "买入", "position_recommendation": "long",
        "start_price": kwargs["start_price"], "end_close": 105.0,
        "max_high": 110.0, "min_low": 95.0, "stock_return_pct": 5.0,
        "direction_expected": "up", "direction_correct": True, "outcome": "win",
        "stop_loss": 90.0, "take_profit": 120.0,
        "hit_stop_loss": False, "hit_take_profit": False,
        "first_hit": "neither", "first_hit_date": None, "first_hit_trading_days": 12,
        "simulated_entry_price": kwargs["start_price"], "simulated_exit_price": 105.0,
        "simulated_exit_reason": "window_end", "simulated_return_pct": 5.0,
    }


def test_hk_intraday_processed_with_hk_market_window(monkeypatch, tmp_path):
    """HK00700 + 5m + 10 交易日:被处理(非跳过),引擎切片=660(hk 330/日),落库语义正确。"""
    os.environ["DATABASE_PATH"] = str(tmp_path / "hk_intraday.db")
    from src.config import Config
    from src.storage import DatabaseManager
    Config._instance = None
    DatabaseManager.reset_instance()
    svc = BacktestService(db_manager=DatabaseManager.get_instance())

    candidate = SimpleNamespace(
        id=7, code="HK00700", operation_advice="买入", stop_loss=90.0, take_profit=120.0,
        context_snapshot=json.dumps({"enhanced_context": {"date": "2026-05-01"}}),
    )
    monkeypatch.setattr(svc.repo, "get_candidates", lambda **k: [candidate])
    monkeypatch.setattr(svc, "_resolve_analysis_date", lambda a: date(2026, 5, 1))
    monkeypatch.setattr(svc.stock_repo, "get_start_daily",
                        lambda code, analysis_date: SimpleNamespace(date=analysis_date, close=100.0))

    from data_provider.base import DataFetcherManager
    monkeypatch.setattr(DataFetcherManager, "get_intraday_data",
                        lambda self, code, interval, **kw: (_minute_df(660).copy(), "AkshareFetcher"))

    captured_eval: Dict[str, Any] = {}

    def fake_eval(**kwargs):
        captured_eval.update(kwargs)
        return _completed_eval(**kwargs)

    from src.core import backtest_engine as beng
    monkeypatch.setattr(beng.BacktestEngine, "evaluate_single", staticmethod(fake_eval))

    saved: List[Any] = []
    monkeypatch.setattr(svc.repo, "save_results_batch",
                        lambda results, **kw: (saved.extend(results), len(results))[1])
    monkeypatch.setattr(svc, "_recompute_summaries", lambda **k: None)

    out = svc.run_backtest(interval="5m", eval_window_days=10)

    assert out["processed"] == 1 and out["completed"] == 1, f"HK 应被处理: {out}"
    assert out["skipped_unsupported"] == 0
    assert captured_eval["config"].eval_window_days == 660    # 10 * bars_per_day(5m, hk)=66
    assert len(saved) == 1
    r = saved[0]
    assert r.eval_window_days == 10
    assert r.bar_interval == "5m"
    assert r.engine_version == "v1-5m"
