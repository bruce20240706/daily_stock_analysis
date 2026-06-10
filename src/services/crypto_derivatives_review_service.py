# -*- coding: utf-8 -*-
"""crypto 复盘永续情绪服务（编排层）。

职责：读 config 门控（crypto_derivatives_enabled）与篮子（crypto_market_review_symbols），
解析+过滤非法代码后调 data_provider 纯抓取聚合。data_provider 保持纯抓取。
"""
import logging
from typing import Any, Dict, List

import data_provider.crypto_derivatives as cd
from data_provider.base import is_crypto_code
from src.config import get_config

logger = logging.getLogger(__name__)


class CryptoDerivativesReviewService:
    def __init__(self, config=None):
        self.config = config or get_config()

    def _basket(self) -> List[str]:
        raw = getattr(self.config, "crypto_market_review_symbols", "") or ""
        symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
        return [s for s in symbols if is_crypto_code(s)]

    def collect(self) -> Dict[str, Any]:
        if not getattr(self.config, "crypto_derivatives_enabled", True):
            return {}
        symbols = self._basket()
        if not symbols:
            return {}
        return cd.fetch_perp_market_snapshot(symbols)
