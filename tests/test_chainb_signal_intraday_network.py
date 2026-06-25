# -*- coding: utf-8 -*-
"""链路B 信号可信度分钟化：联网观测（-m network）。

真拉一支 crypto 分钟数据跑 run(interval='5m')，验证分钟路径端到端可达。
连接异常带重试后 skip（不把网络抖动当失败），与既有 network 观测口径一致。
"""
import logging
import time

import pytest

pytestmark = pytest.mark.network

# 硬性无网(DNS 解析/建连失败)：重试无意义,立即 skip
_NO_NETWORK = ("name resolution", "Temporary failure", "gaierror",
               "Failed to establish a new connection")
# 瞬时抖动(重试可能恢复)：超时/断连/限频或地域限制
_TRANSIENT = ("timed out", "RemoteDisconnected", "Connection reset",
              "Connection aborted", "Max retries", "451")


def test_chainb_run_5m_crypto_real(tmp_path, caplog):
    from src.services.signal_backtest_service import SignalBacktestService
    from src.storage import DatabaseManager

    # 隔离临时 DB,避免观测落库污染默认库
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path}/chainb.db")
    try:
        # run() 对单股取数失败是 fail-closed：吞异常 + 计入 errors + WARNING 日志,
        # 不向上抛。因此观测测试不能等异常冒泡来判 skip,而要从捕获日志判失败类别:
        #   - processed>=1            → 分钟路径端到端可达,PASS(有网时非恒真)
        #   - processed==0 且硬性无网 → 立即 SKIP(重试 DNS 失败无意义)
        #   - processed==0 且瞬时抖动 → 重试,持续失败后 SKIP
        #   - processed==0 且非连接失败 → 分钟路径真实回归,FAIL
        last_msg = ""
        for _ in range(4):
            caplog.clear()
            with caplog.at_level(logging.WARNING):
                out = SignalBacktestService(db_manager=db).run(codes=["BTC/USDT"], interval="5m")
            # interval 贯通与网络无关,始终校验
            assert out["interval"] == "5m"
            if out["processed"] >= 1:
                return
            last_msg = " ".join(r.getMessage() for r in caplog.records)
            if any(k in last_msg for k in _NO_NETWORK):
                pytest.skip(f"无外网(DNS/建连失败),跳过分钟联网观测: {last_msg[:300]}")
            if any(k in last_msg for k in _TRANSIENT):
                time.sleep(3)
                continue
            pytest.fail(f"分钟路径未处理任何 code 且非连接类失败: {last_msg or out}")
        pytest.skip(f"crypto 分钟端点持续抖动,跳过观测: {last_msg}")
    finally:
        DatabaseManager.reset_instance()
