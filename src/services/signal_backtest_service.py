# -*- coding: utf-8 -*-
"""自选池信号三重门回测批作业：逐股评估 → 按 (type×market) 聚合 → 落 signal_stats。

watchlist 来源与看板完全一致：读取 SystemConfigService 的 STOCK_LIST 配置项。
注意：不从 api.v1.endpoints.stocks 导入 _read_watchlist_codes，
因为导入该端点模块会触发 FastAPI/Starlette 全量初始化（耗时 ~10s，引入 58 个重模块），
CLI/批作业路径不应承担此代价。等价逻辑在本模块内独立实现，数据源相同。
"""
import logging
from datetime import date, timedelta
from typing import List, Optional

import pandas as pd

from src.config import get_config
from src.core.intraday_backtest import is_intraday_interval, validate_interval
from src.core.trading_calendar import get_market_for_stock
from src.repositories.signal_stats_repo import SignalStatsRepository
from src.services.signal_backtest import (
    aggregate_signal_stats,
    evaluate_baseline_outcomes,
    evaluate_signal_outcomes,
)
from src.services.signal_hit_rate import resolve_verified_min_sample
from src.services.volume_price_signals import VPSConfig
from src.services.stock_service import StockService
from src.services.system_config_service import SystemConfigService
from src.storage import SignalStatRow

logger = logging.getLogger(__name__)

# 批作业单股最小 bar 数（少于此数则跳过，避免统计无意义）
_MIN_BARS = 50

# 默认拉取天数（覆盖 horizon 预热 + 有效评估窗口）
_FETCH_DAYS = 365


# 各源分钟历史「单次安全回看上限」(日历天)：据 start_date 加深历史时按 市场×interval 夹取，
# 避免向源请求其单次调用无法稳定返回的过深窗口。
#   us(yfinance)：5m/15m≈60d、1h≈730d 为文档硬上限；1m=7d(且 1m 已在 fetcher fail-closed)。
#   cn(tushare)：stk_mins 单次有行数上限，下列为保守值——
#     ⚠ 实现期须在线核验 tushare 对 today-N 的 1m/5m/15m/1h 真实单次返回(优雅近端子集 / 报错 /
#       返回错窗)，据实校准本表(可放宽)；核验前以保守上限避免 cn 高频从「浅窗可用」恶化为单股 errors。
#       关键：tushare get_intraday_data 体内不读 days(no-op)，但真正消费 start_date，故旧「days 偏大
#       不取错数」的安全性不可迁移到 start_date——这正是本表对 cn 也必须夹取的原因。
# crypto 不在表中：按 days 锚定、不下传 start_date(见 _minute_fetch_start_date)，行为字节级不变。
_INTRADAY_MAX_DAYS = {
    # yfinance 要求请求严格在 last-N-天内；请求恰好 N 天会被硬拒(返回空→DataFetchError)。
    # 真网核验：5m 59天OK/60天FAIL、1h 729天OK/730天FAIL。
    # us/hk(分钟均走 yfinance 路径)band 取上限再留 1-4 天余量，抵消实时 vs 午夜基准 + HK UTC+8 偏移。
    "us": {"1m": 7, "5m": 58, "15m": 58, "1h": 725},
    "cn": {"1m": 30, "5m": 90, "15m": 365, "1h": 730},
    # hk 双源(akshare 东财主 + yfinance 兜底):yfinance 要求请求严格在 last-N-天内(真网核验 5m 60/1h 730 即被硬拒)，
    # 故 band 取 yfinance 上限再留 1-2 天余量(58/725);1m fail-closed 不设键
    "hk": {"5m": 58, "15m": 58, "1h": 725},
}


def _minute_fetch_days(*, market: str, interval: str) -> int:
    """分钟取数回看天数（传给 get_intraday_data 的 days，并据此推 start_date）。

    1h 历史更深取 730、其余取 365 为基线；再按 _INTRADAY_MAX_DAYS[market][interval] 夹取
    (us/cn 各源单次安全上限)。crypto 不在表中→返回基线(与旧 _minute_fetch_days 逐 interval 相等)。
    """
    base = 730 if interval == "1h" else 365
    band = _INTRADAY_MAX_DAYS.get(market, {})
    return min(base, band.get(interval, base))


def _minute_fetch_start_date(*, market: str, interval: str, today: Optional[date] = None) -> Optional[str]:
    """非 crypto 分钟取数的历史起点（ISO date 字符串）。

    crypto 返回 None → 维持 get_intraday_data 的 days-only 近窗行为(字节级不变)；
    非 crypto 返回 today - _minute_fetch_days，使 tushare/yfinance 真正加深历史。
    today 默认 date.today()；helper 单测可显式注入 today，_load_bars 集成测试经 monkeypatch
    模块级 date 锁定(本函数不透传 today)。
    """
    if market == "crypto":
        return None
    ref = today or date.today()
    return (ref - timedelta(days=_minute_fetch_days(market=market, interval=interval))).isoformat()


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
        interval: str = "1d",
    ) -> dict:
        """对自选池（或指定列表）跑信号三重门回测，聚合后写入 signal_stats。

        Args:
            codes:    指定股票代码列表；None 时读取 STOCK_LIST 自选池。
            horizon:  前瞻 bar 数；None 时取 config.signal_backtest_horizon_bars（默认 10）。
            interval: bar 粒度；'1d' 走日线（行为不变），分钟（1m/5m/15m/1h）在分钟 bar
                      上重算 VPS 信号 + 前向三重门，落库行标记 interval=<interval>。
                      分钟覆盖 crypto/A股沪深/美股个股/港股个股(best-effort);指数等无分钟取数,单股计入 errors。

        Returns:
            dict，字段：processed, codes, stats_written, skipped, errors, interval。
        """
        validate_interval(interval)
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

                df = self._load_bars(svc, code, interval, market)
                if df is None or len(df) < _MIN_BARS:
                    bars = 0 if df is None else len(df)
                    logger.debug("跳过数据不足股票: %s (bars=%d)", code, bars)
                    skipped += 1
                    continue

                cfg_m = VPSConfig.for_market_interval(market, interval)
                all_sig.extend(evaluate_signal_outcomes(df, market=market, horizon=hz, config=cfg_m))
                all_base.extend(evaluate_baseline_outcomes(df, market=market, horizon=hz, config=cfg_m))
                processed += 1

            except Exception as exc:  # 单股失败不拖垮整批
                errors += 1
                logger.warning("信号回测跳过 %s: %s", code, exc)

        # 聚合所有股票的结果（一次调用）
        stats = aggregate_signal_stats(
            all_sig, all_base, horizon=hz, interval=interval,
            fwer_alpha=float(getattr(cfg, "signal_backtest_fwer_alpha", 0.05)),
            min_sample=resolve_verified_min_sample(cfg),
        )

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
                ci_low_corrected=s.ci_low_corrected,
                family_size=s.family_size,
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
            "interval": interval,
        }

    def _load_bars(self, svc, code, interval, market):
        """按 interval 取 bar：日线走 StockService（不变），分钟走链路A get_intraday_data。

        分钟路径按 market 计算回看深度与历史起点：crypto 维持 days-only(字节级不变)，
        非 crypto 下传 start_date 真正加深历史(美股夹 yfinance band)。把 'datetime' 列重命名为
        'date' 复用既有 'date' 列契约。取不到数据返回 None(交由 run 计入 skipped)。
        """
        if not is_intraday_interval(interval):       # '1d'
            hist = svc.get_history_data(stock_code=code, period="daily", days=_FETCH_DAYS)
            rows = (hist or {}).get("data") or []
            return pd.DataFrame(rows) if rows else None
        from data_provider.base import DataFetcherManager
        days = _minute_fetch_days(market=market, interval=interval)
        start_date = _minute_fetch_start_date(market=market, interval=interval)
        df, _src = DataFetcherManager().get_intraday_data(
            code, interval, start_date=start_date, days=days)
        if df is None or df.empty:
            return None
        return df.rename(columns={"datetime": "date"})
