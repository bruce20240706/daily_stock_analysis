"""OKX 永续合约（SWAP）公共行情 fetcher（免 API Key）。

复用 OkxFetcher 的 candles/ticker 抓取与解析，仅把代码映射为 SWAP instId。
notation: BASE/QUOTE:PERP（如 BTC/USDT:PERP）-> OKX instId BASE-QUOTE-SWAP。
"""
import os

from .base import parse_perp_code
from .okx_fetcher import OkxFetcher


class OkxPerpetualFetcher(OkxFetcher):
    name = "OkxPerpetualFetcher"
    priority = int(os.getenv("OKX_PERPETUAL_PRIORITY", "55"))

    def _to_exchange_symbol(self, code: str) -> str:
        base, quote = parse_perp_code(code)
        return f"{base}-{quote}-SWAP"
