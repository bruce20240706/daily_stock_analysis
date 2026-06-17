# -*- coding: utf-8 -*-
"""自选池信号三重门回测批作业：逐股评估 → 按 (type×market) 聚合 → 落 signal_stats。

watchlist 来源与看板完全一致：读取 SystemConfigService 的 STOCK_LIST 配置项。
注意：不从 api.v1.endpoints.stocks 导入 _read_watchlist_codes，
因为导入该端点模块会触发 FastAPI/Starlette 全量初始化（耗时 ~10s，引入 58 个重模块），
CLI/批作业路径不应承担此代价。等价逻辑在本模块内独立实现，数据源相同。
"""
import logging
from typing import List, Optional

import pandas as pd

from src.config import get_config
from src.core.trading_calendar import get_market_for_stock
from src.repositories.signal_stats_repo import SignalStatsRepository
from src.services.signal_backtest import (
    aggregate_signal_stats,
    evaluate_baseline_outcomes,
    evaluate_signal_outcomes,
)
from src.services.stock_service import StockService
from src.services.system_config_service import SystemConfigService
from src.storage import SignalStatRow

logger = logging.getLogger(__name__)

# 批作业单股最小 bar 数（少于此数则跳过，避免统计无意义）
_MIN_BARS = 50

# 默认拉取天数（覆盖 horizon 预热 + 有效评估窗口）
_FETCH_DAYS = 365


def _read_watchlist_codes(service: SystemConfigService) -> list:
    """读取 STOCK_LIST 配置项，返回股票代码列表（与看板同源，不做规范化）。

    等价于 api.v1.endpoints.stocks._read_watchlist_codes，但不依赖 FastAPI 端点模块，
    避免在 CLI/批作业路径引入重型 Web 框架初始化（~10s, 58 个模块）。
    """
    config_data = service.get_config(include_schema=False)
    stock_list_str = ""
    for item in config_data.get("items", []):
        if item.get("key") == "STOCK_LIST":
            stock_list_str = str(item.get("value", ""))
            break
    return [c.strip() for c in stock_list_str.split(",") if c.strip()]


class SignalBacktestService:
    """自选池批量信号回测服务。

    对自选池每支股票独立评估三重门信号回测结果，
    汇总后按 (signal_type × market) 聚合统计，持久化至 signal_stats 表。
    """

    def __init__(self, db_manager=None):
        self.repo = SignalStatsRepository(db_manager)

    def run(
        self,
        *,
        codes: Optional[List[str]] = None,
        horizon: Optional[int] = None,
    ) -> dict:
        """对自选池（或指定列表）跑信号三重门回测，聚合后写入 signal_stats。

        Args:
            codes:   指定股票代码列表；None 时读取 STOCK_LIST 自选池。
            horizon: 前瞻 bar 数；None 时取 config.signal_backtest_horizon_bars（默认 10）。

        Returns:
            dict，字段：processed, codes, stats_written, skipped, errors。
        """
        cfg = get_config()
        hz = int(horizon or getattr(cfg, "signal_backtest_horizon_bars", 10))

        if codes is None:
            codes = _read_watchlist_codes(SystemConfigService())

        svc = StockService()
        all_sig: list = []
        all_base: list = []
        processed = 0
        skipped = 0
        errors = 0

        for code in codes:
            try:
                market = get_market_for_stock(code)
                if market is None:
                    logger.debug("跳过未知市场股票: %s", code)
                    skipped += 1
                    continue

                hist = svc.get_history_data(stock_code=code, period="daily", days=_FETCH_DAYS)
                rows = (hist or {}).get("data") or []
                if len(rows) < _MIN_BARS:
                    logger.debug("跳过数据不足股票: %s (bars=%d)", code, len(rows))
                    skipped += 1
                    continue

                df = pd.DataFrame(rows)
                all_sig.extend(evaluate_signal_outcomes(df, market=market, horizon=hz))
                all_base.extend(evaluate_baseline_outcomes(df, market=market, horizon=hz))
                processed += 1

            except Exception as exc:  # 单股失败不拖垮整批
                errors += 1
                logger.warning("信号回测跳过 %s: %s", code, exc)

        # 聚合所有股票的结果（一次调用）
        stats = aggregate_signal_stats(all_sig, all_base, horizon=hz)

        # 构造 ORM 行并落库
        orm_rows = [
            SignalStatRow(
                signal_type=s.signal_type,
                market=s.market,
                interval=s.interval,
                horizon=s.horizon,
                win=s.win,
                loss=s.loss,
                sample=s.sample,
                win_rate=s.win_rate,
                ci_low=s.ci_low,
                ci_high=s.ci_high,
                baseline_win_rate=s.baseline_win_rate,
                excess=s.excess,
            )
            for s in stats
        ]
        written = self.repo.save_batch(orm_rows, replace_existing=True)

        return {
            "processed": processed,
            "codes": len(codes),
            "stats_written": written,
            "skipped": skipped,
            "errors": errors,
        }
