# -*- coding: utf-8 -*-
"""crypto 永续合约指标服务（编排层）。

职责：读 config 门控 + region 门控（仅 crypto），调 data_provider 纯抓取源，
presence-only。attach_crypto_contracts 供 pipeline 在分析前注入 context。
"""
import logging
from typing import Any, Dict, Optional

import data_provider.crypto_derivatives as cd
from data_provider.base import is_crypto_code
from src.config import get_config

logger = logging.getLogger(__name__)


class CryptoDerivativesService:
    def __init__(self, config=None):
        self.config = config or get_config()

    def collect(self, code: str) -> Dict[str, Any]:
        if not getattr(self.config, "crypto_derivatives_enabled", True):
            return {}
        if not is_crypto_code(code or ""):
            return {}
        base, _, quote = (code or "").partition("/")
        return cd.fetch_perp_metrics(base, quote)


def attach_crypto_contracts(context: Dict[str, Any], config: Optional[Any] = None) -> None:
    """crypto 标的：拉永续指标写入 context['crypto_contracts']（presence-only）。失败/非 crypto/禁用 → 不写。"""
    code = context.get("code") if isinstance(context, dict) else None
    if not code:
        return
    try:
        metrics = CryptoDerivativesService(config=config).collect(code)
    except Exception as e:
        logger.warning("[合约指标] 收集失败，跳过: %s", e)
        return
    if metrics:
        context["crypto_contracts"] = metrics
