# -*- coding: utf-8 -*-
"""A股分钟回测 service 集成(A-T7):放行 A股 + 市场化窗口。

全程离线:mock 门面 get_intraday_data / get_start_daily / evaluate_single / save。
验证 A股候选被处理(非 skipped_unsupported)、引擎切片用 cn 市场化窗口(5m×10日=480)、
落库 eval_window_days=交易日数(10)/bar_interval='5m'/engine_version='v1-5m'/first_hit_bar_index,
以及 cn 取数窗口的 end_date 比起点宽出周末/节假日缓冲(>= +(N*2) 日历日)。
"""
import json
import os
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List

import pandas as pd

from src.services.backtest_service import BacktestService


def _minute_df(n: int, base: float = 1700.0) -> pd.DataFrame:
    """n 根 5m bar(datetime 字符串 + OHLCV),用于 _df_to_bars。"""
    return pd.DataFrame([
        {
            "datetime": f"2026-05-06 {9 + i // 60:02d}:{i % 60:02d}:00",
            "open": base, "high": base + 5, "low": base - 5, "close": base, "volume": 1.0,
        }
        for i in range(n)
    ])


def _fake_analysis(code: str = "600519", analysis_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=analysis_id, code=code, operation_advice="买入",
        stop_loss=1600.0, take_profit=1900.0,
        context_snapshot=json.dumps({"enhanced_context": {"date": "2026-05-01"}}),
    )


def _completed_eval(**kwargs):
    return {
        "eval_status": "completed",
        "analysis_date": kwargs.get("analysis_date"),
        "eval_window_days": kwargs["config"].eval_window_days,
        "engine_version": kwargs["config"].engine_version,
        "operation_advice": "买入", "position_recommendation": "long",
        "start_price": kwargs["start_price"], "end_close": 1750.0,
        "max_high": 1800.0, "min_low": 1650.0, "stock_return_pct": 3.0,
        "direction_expected": "up", "direction_correct": True, "outcome": "win",
        "stop_loss": 1600.0, "take_profit": 1900.0,
        "hit_stop_loss": False, "hit_take_profit": False,
        "first_hit": "neither", "first_hit_date": None,
        "first_hit_trading_days": 37,   # 引擎在该字段回传 bar index
        "simulated_entry_price": kwargs["start_price"], "simulated_exit_price": 1750.0,
        "simulated_exit_reason": "window_end", "simulated_return_pct": 3.0,
    }


def _build_svc(monkeypatch, tmp_path, code, analysis_date, captured):
    db_path = str(tmp_path / "ashare_intraday.db")
    os.environ["DATABASE_PATH"] = db_path

    from src.config import Config
    from src.storage import DatabaseManager

    Config._instance = None
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    svc = BacktestService(db_manager=db)

    candidate = _fake_analysis(code=code, analysis_id=7)
    monkeypatch.setattr(svc.repo, "get_candidates", lambda **k: [candidate])
    monkeypatch.setattr(svc, "_resolve_analysis_date", lambda a: analysis_date)
    _daily = SimpleNamespace(date=analysis_date, close=1700.0)
    monkeypatch.setattr(svc.stock_repo, "get_start_daily", lambda code, analysis_date: _daily)

    fake_df = _minute_df(480, base=1700.0)
    from data_provider.base import DataFetcherManager

    def fake_get_intraday(self, code, interval, **kw):
        captured.update(kw)
        return fake_df.copy(), "TushareFetcher"

    monkeypatch.setattr(DataFetcherManager, "get_intraday_data", fake_get_intraday)

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
    return svc, saved, captured_eval


def test_ashare_intraday_processed_with_cn_market_window(monkeypatch, tmp_path):
    """A股 600519 + 5m + 10 交易日:被处理(非跳过),引擎切片=480(cn 240/日),落库语义正确。"""
    captured: Dict[str, Any] = {}
    analysis_date = date(2026, 5, 1)
    svc, saved, captured_eval = _build_svc(monkeypatch, tmp_path, "600519", analysis_date, captured)

    out = svc.run_backtest(interval="5m", eval_window_days=10)

    assert out["processed"] == 1 and out["completed"] == 1, f"A股应被处理: {out}"
    assert out.get("skipped_unsupported", 0) == 0

    # 引擎切片 = 市场化 window_bar_cnt = 10 * (240//5) = 480
    assert captured_eval["config"].eval_window_days == 480

    assert len(saved) == 1
    r = saved[0]
    assert r.eval_window_days == 10              # 落库存交易日数,非 bar 数
    assert r.bar_interval == "5m"
    assert r.engine_version == "v1-5m"           # A股 leverage 恒 1,无 -xN
    assert r.first_hit_bar_index == 37           # 引擎 first_hit_trading_days 路由到 bar_index
    assert r.first_hit_trading_days is None      # 分钟域消歧


def test_ashare_intraday_cn_end_date_has_holiday_buffer(monkeypatch, tmp_path):
    """cn 每日仅 240 分钟,N 交易日需更宽日历窗口:end_date 比起点宽出 >= N*2 日历日。"""
    captured: Dict[str, Any] = {}
    analysis_date = date(2026, 5, 1)
    svc, saved, _ = _build_svc(monkeypatch, tmp_path, "600519", analysis_date, captured)

    svc.run_backtest(interval="5m", eval_window_days=10)

    start = captured["start_date"]
    end = captured["end_date"]
    if isinstance(start, str):
        start = date.fromisoformat(start)
    if isinstance(end, str):
        end = date.fromisoformat(end)
    assert start == analysis_date + timedelta(days=1)   # 窗口起点 = 日线收盘次日
    # 10 交易日需 ~14 自然日;缓冲取 max(N*2, N+10)=20 → end 至少宽出 N*2
    assert (end - start).days >= 20
