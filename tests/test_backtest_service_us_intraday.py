# -*- coding: utf-8 -*-
"""美股分钟回测 service 集成(US-T5):放行美股 + 市场化窗口 + end_date 缓冲推广。

全程离线:mock 门面 get_intraday_data / get_start_daily / evaluate_single / save。
验证 美股候选被处理(非 skipped)、引擎切片用 us 市场化窗口(5m×10=780)、落库语义,
以及 us 取数窗口 end_date 走 cn 同款长假/周末缓冲(crypto=N,其余=max(N*2,N*3//2+14))。
"""
import json
import os
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List

import pandas as pd

from src.services.backtest_service import BacktestService


def _minute_df(n, base=100.0):
    return pd.DataFrame([
        {"datetime": f"2026-06-22 {9 + i // 60:02d}:{i % 60:02d}:00",
         "open": base, "high": base + 1, "low": base - 1, "close": base, "volume": 1.0}
        for i in range(n)
    ])


def _fake_analysis(code="AAPL", aid=1):
    return SimpleNamespace(id=aid, code=code, operation_advice="买入",
                           stop_loss=90.0, take_profit=120.0,
                           context_snapshot=json.dumps({"enhanced_context": {"date": "2026-06-01"}}))


def _build(monkeypatch, tmp_path, captured):
    os.environ["DATABASE_PATH"] = str(tmp_path / "us_intraday.db")
    from src.config import Config
    from src.storage import DatabaseManager
    Config._instance = None
    DatabaseManager.reset_instance()
    svc = BacktestService(db_manager=DatabaseManager.get_instance())

    analysis_date = date(2026, 6, 1)
    monkeypatch.setattr(svc.repo, "get_candidates", lambda **k: [_fake_analysis()])
    monkeypatch.setattr(svc, "_resolve_analysis_date", lambda a: analysis_date)
    monkeypatch.setattr(svc.stock_repo, "get_start_daily",
                        lambda code, analysis_date: SimpleNamespace(date=analysis_date, close=100.0))

    from data_provider.base import DataFetcherManager

    def fake_intraday(self, code, interval, **kw):
        captured.update(kw)
        return _minute_df(780).copy(), "YfinanceFetcher"

    monkeypatch.setattr(DataFetcherManager, "get_intraday_data", fake_intraday)

    captured_eval: Dict[str, Any] = {}

    def fake_eval(**kwargs):
        captured_eval.update(kwargs)
        return {"eval_status": "completed", "analysis_date": analysis_date,
                "eval_window_days": kwargs["config"].eval_window_days,
                "engine_version": kwargs["config"].engine_version,
                "operation_advice": "买入", "position_recommendation": "long",
                "start_price": kwargs["start_price"], "end_close": 110.0, "max_high": 115.0,
                "min_low": 95.0, "stock_return_pct": 10.0, "direction_expected": "up",
                "direction_correct": True, "outcome": "win", "stop_loss": 90.0, "take_profit": 120.0,
                "hit_stop_loss": False, "hit_take_profit": False, "first_hit": "neither",
                "first_hit_date": None, "first_hit_trading_days": 42,
                "simulated_entry_price": kwargs["start_price"], "simulated_exit_price": 110.0,
                "simulated_exit_reason": "window_end", "simulated_return_pct": 10.0}

    from src.core import backtest_engine as beng
    monkeypatch.setattr(beng.BacktestEngine, "evaluate_single", staticmethod(fake_eval))

    saved: List[Any] = []
    monkeypatch.setattr(svc.repo, "save_results_batch",
                        lambda results, **kw: (saved.extend(results), len(results))[1])
    monkeypatch.setattr(svc, "_recompute_summaries", lambda **k: None)
    return svc, saved, captured_eval, analysis_date


def test_us_intraday_processed_with_us_window(monkeypatch, tmp_path):
    captured: Dict[str, Any] = {}
    svc, saved, captured_eval, analysis_date = _build(monkeypatch, tmp_path, captured)
    out = svc.run_backtest(interval="5m", eval_window_days=10)

    assert out["processed"] == 1 and out["completed"] == 1, out
    assert out.get("skipped_unsupported", 0) == 0
    assert captured_eval["config"].eval_window_days == 780     # 10 * bars_per_day(5m,us)=78
    r = saved[0]
    assert r.eval_window_days == 10 and r.bar_interval == "5m"
    assert r.engine_version == "v1-5m" and r.first_hit_bar_index == 42
    assert r.first_hit_trading_days is None
    # end_date 缓冲(us 走 cn 同款):start=analysis_date+1,end 宽出 >= 24 天
    start, end = captured["start_date"], captured["end_date"]
    start = date.fromisoformat(start) if isinstance(start, str) else start
    end = date.fromisoformat(end) if isinstance(end, str) else end
    assert start == analysis_date + timedelta(days=1)
    assert (end - start).days >= 24
