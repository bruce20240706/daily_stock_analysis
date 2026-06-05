"""数字货币交易所行情基类（只读公共行情，免 API Key）。

子类实现交易所 HTTP 细节；本基类负责统一标准化、pct_chg 计算与异常包装。
"""
import logging
from typing import Optional

import pandas as pd
import requests

from .base import BaseFetcher, STANDARD_COLUMNS, DataFetchError, is_crypto_code
from .realtime_types import UnifiedRealtimeQuote

logger = logging.getLogger(__name__)


class CryptoExchangeBase(BaseFetcher):
    """crypto 现货行情基类。支持市场仅 'crypto'。"""

    name = "CryptoExchangeBase"
    priority = 50
    timeout = 10
    MAX_LIMIT = 1000  # 子类可覆盖（OKX 基础接口为 100）

    # ---- 子类需实现的钩子 ----
    def _to_exchange_symbol(self, code: str) -> str:
        """BTC/USDT -> 交易所符号（如 BTCUSDT / BTC-USDT）。"""
        raise NotImplementedError

    def _request_klines(self, symbol: str, days: int) -> list:
        """HTTP 取日线原始数据，返回交易所原始结构（list）。"""
        raise NotImplementedError

    def _parse_klines(self, raw: list) -> pd.DataFrame:
        """把原始结构解析为含 [date, open, high, low, close, volume, amount] 的 DataFrame。

        date 为毫秒时间戳或可被 pandas 解析的值；amount 缺失时填 None 列。
        """
        raise NotImplementedError

    def _request_ticker(self, symbol: str) -> dict:
        raise NotImplementedError

    def _parse_ticker(self, raw: dict, code: str) -> UnifiedRealtimeQuote:
        raise NotImplementedError

    # ---- 通用实现 ----
    def _days_to_limit(self, days: int) -> int:
        buffer = 5  # 给 MA 计算留首行余量
        return max(1, min(int(days) + buffer, self.MAX_LIMIT))

    def _http_get(self, url: str, params: dict) -> object:
        resp = requests.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        if not is_crypto_code(stock_code):
            raise DataFetchError(f"{self.name} 仅支持 crypto 现货代码（BASE/QUOTE），收到 {stock_code}")
        symbol = self._to_exchange_symbol(stock_code)
        days = self._infer_days(start_date, end_date)
        raw = self._request_klines(symbol, days)
        if not raw:
            raise DataFetchError(f"{self.name} 未取到 {stock_code} 的 K 线")
        return self._parse_klines(raw)

    @staticmethod
    def _infer_days(start_date: str, end_date: str) -> int:
        try:
            s = pd.to_datetime(start_date)
            e = pd.to_datetime(end_date)
            return max(1, (e - s).days + 1)
        except Exception:
            return 60

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        df = df.copy()
        # date(毫秒时间戳) -> 'YYYY-MM-DD'（三个子类均产出毫秒时间戳）
        df["date"] = pd.to_datetime(df["date"], unit="ms").dt.strftime("%Y-%m-%d")
        for col in ("open", "high", "low", "close", "volume", "amount"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "amount" not in df.columns:
            df["amount"] = None
        # pct_chg 由 close 计算（首行 0）
        df = df.sort_values("date").reset_index(drop=True)
        df["pct_chg"] = (df["close"].pct_change() * 100).fillna(0.0)
        df["code"] = stock_code
        keep = ["code"] + STANDARD_COLUMNS
        return df[[c for c in keep if c in df.columns]]

    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        if not is_crypto_code(stock_code):
            return None
        try:
            symbol = self._to_exchange_symbol(stock_code)
            raw = self._request_ticker(symbol)
            return self._parse_ticker(raw, stock_code)
        except Exception as e:  # 单源失败由 manager fallback
            logger.info("[%s] 实时行情失败 %s: %s", self.name, stock_code, e)
            return None
