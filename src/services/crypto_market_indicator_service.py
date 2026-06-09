# -*- coding: utf-8 -*-
"""crypto 大盘宏观指标服务（编排层）。

职责：读 config 门控，调度 data_provider 纯抓取源（global + fng），合并为单 dict（presence-only）。
data_provider 保持纯抓取。单源失败已在源内降级为 {}，合并天然保留另一源。
"""
import logging
from typing import Any, Dict

import data_provider.crypto_market_indicators as cmi
from src.config import get_config

logger = logging.getLogger(__name__)


class CryptoMarketIndicatorService:
    def __init__(self, config=None):
        self.config = config or get_config()

    def collect(self) -> Dict[str, Any]:
        if not getattr(self.config, "crypto_market_indicators_enabled", True):
            return {}
        out: Dict[str, Any] = {}
        out.update(cmi.fetch_global_market())   # 失败已在源内降级为 {}
        fng = cmi.fetch_fear_greed()
        if fng:
            out["fear_greed"] = fng
        return out
