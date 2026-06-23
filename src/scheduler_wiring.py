"""调度装配:opt-in 后台任务注册(可单测)。

本模块仅提供可测纯函数,由 main.py 调度装配处调用,不持有全局状态。
"""
import logging

logger = logging.getLogger(__name__)


def maybe_register_intraday_backtest(scheduler, config) -> None:
    """仅当 INTRADAY_BACKTEST_ENABLED=true 时注册盘中回测后台任务。

    Args:
        scheduler: 具有 add_background_task(**kwargs) 方法的调度器对象。
        config:    持有以下字段的配置对象:
                   - intraday_backtest_enabled (bool, 默认 False)
                   - intraday_backtest_schedule_minutes (int, 默认 60)
                   - crypto_intraday_backtest_interval (str, 默认 "5m")
    """
    if not getattr(config, "intraday_backtest_enabled", False):
        return

    minutes = max(1, int(getattr(config, "intraday_backtest_schedule_minutes", 60)))
    interval = getattr(config, "crypto_intraday_backtest_interval", "5m")

    def _task():
        from src.services.backtest_service import BacktestService
        try:
            BacktestService().run_backtest(interval=interval)
        except Exception as exc:
            logger.warning("盘中回测后台任务失败: %s", exc)

    scheduler.add_background_task(
        task=_task,
        interval_seconds=minutes * 60,
        run_immediately=False,
        name="intraday_backtest",
    )
    logger.info("已注册盘中回测后台任务(每 %d 分钟, interval=%s)", minutes, interval)
