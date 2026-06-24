# -*- coding: utf-8 -*-
"""链路B 信号可信度分钟化：联网观测（-m network）。

真拉一支 crypto 分钟数据跑 run(interval='5m')，验证分钟路径端到端可达。
连接异常带重试后 skip（不把网络抖动当失败），与既有 network 观测口径一致。
"""
import time

import pytest

pytestmark = pytest.mark.network

_HINTS = ("Connection", "Max retries", "timed out", "Temporary failure",
          "RemoteDisconnected", "451")


def test_chainb_run_5m_crypto_real(tmp_path):
    from src.services.signal_backtest_service import SignalBacktestService
    from src.storage import DatabaseManager

    # 隔离临时 DB,避免观测落库污染默认库
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path}/chainb.db")
    try:
        last = None
        for _ in range(4):
            try:
                out = SignalBacktestService(db_manager=db).run(codes=["BTC/USDT"], interval="5m")
                # 数据可达时 BTC/USDT 5m 近窗远超 _MIN_BARS=50 → 应真正处理该 code(非恒真)
                assert out["interval"] == "5m" and out["processed"] >= 1
                return
            except Exception as e:  # noqa: BLE001 — 观测测试,网络抖动重试后 skip
                last = e
                if any(k in str(e) for k in _HINTS):
                    time.sleep(3)
                    continue
                raise
        pytest.skip(f"crypto 分钟端点不可达,跳过观测: {last}")
    finally:
        DatabaseManager.reset_instance()
