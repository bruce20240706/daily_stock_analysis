"""数字货币交易所行情基类（只读公共行情，免 API Key）。

子类实现交易所 HTTP 细节；本基类负责统一标准化、pct_chg 计算与异常包装。
"""
import logging
import os
from typing import Optional

import pandas as pd
import requests

from .base import BaseFetcher, STANDARD_COLUMNS, DataFetchError, is_crypto_code, is_perp_code
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

    def _request_klines(self, symbol: str, days: int, interval: str = "1d") -> list:
        """HTTP 取 K 线原始数据，返回交易所原始结构（list）。interval 默认 '1d' 兼容日线调用。"""
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

    def _intraday_limit(self, days: int, interval: str) -> int:
        """分钟 K 线总条数 = days × bars_per_day(interval)，不加 buffer（不算指标）。"""
        from src.core.intraday_backtest import bars_per_day
        return int(days) * bars_per_day(interval)

    def _fetch_timeout(self) -> float:
        """请求超时秒数，可经 CRYPTO_FETCH_TIMEOUT_SECONDS 覆盖（默认沿用类属性 timeout）。

        地区受限/网络较差时可调大；非法值回退到默认。
        """
        raw = os.getenv("CRYPTO_FETCH_TIMEOUT_SECONDS")
        if raw:
            try:
                value = float(raw)
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass
        return self.timeout

    def _fetch_max_retries(self) -> int:
        """额外重试次数，可经 CRYPTO_FETCH_MAX_RETRIES 覆盖（默认 0：单次请求，保持快速 fallback）。"""
        raw = os.getenv("CRYPTO_FETCH_MAX_RETRIES")
        if raw:
            try:
                value = int(raw)
                if value >= 0:
                    return value
            except (TypeError, ValueError):
                pass
        return 0

    def _http_get(self, url: str, params: dict) -> object:
        timeout = self._fetch_timeout()
        max_retries = self._fetch_max_retries()
        for attempt in range(max_retries + 1):
            try:
                resp = requests.get(url, params=params, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                # 4xx（如 451 地区限制）为确定性失败，不重试，立即 fallback 到下一数据源；
                # 仅对网络/超时/5xx 等瞬时错误按 max_retries 重试。
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and 400 <= status < 500:
                    raise
                if attempt >= max_retries:
                    raise

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        if not (is_crypto_code(stock_code) or is_perp_code(stock_code)):
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

    def get_intraday_data(
        self,
        stock_code: str,
        interval: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30,
    ) -> pd.DataFrame:
        """获取分钟级 K 线数据（纯 OHLCV + datetime，不计算技术指标）。

        仅支持 crypto 代码；非 crypto 由门面层在路由时拒绝。
        返回 DataFrame 列：datetime, open, high, low, close, volume（及 code/amount/pct_chg 如存在）。
        """
        symbol = self._to_exchange_symbol(stock_code)
        raw = self._request_klines(symbol, days=days, interval=interval)
        if not raw:
            raise DataFetchError(f"{self.name} 未取到 {stock_code} 的分钟 K 线 (interval={interval})")
        df = self._parse_klines(raw)
        # _normalize_data 会把 date 毫秒时间戳转成 'YYYY-MM-DD' 字符串，丢失时分秒。
        # 分钟数据需要保留完整时间戳，在此先提取 datetime 列，再走标准化流程。
        if "date" in df.columns:
            df["datetime"] = pd.to_datetime(df["date"], unit="ms")
        df = self._normalize_data(df, stock_code)
        df = self._clean_data(df)
        # 确保 datetime 列存在（_normalize_data 不产出 datetime 列）
        if "datetime" not in df.columns and "date" in df.columns:
            df["datetime"] = pd.to_datetime(df["date"])
        return df

    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        if not (is_crypto_code(stock_code) or is_perp_code(stock_code)):
            return None
        try:
            symbol = self._to_exchange_symbol(stock_code)
            raw = self._request_ticker(symbol)
            return self._parse_ticker(raw, stock_code)
        except Exception as e:  # 单源失败由 manager fallback
            logger.info("[%s] 实时行情失败 %s: %s", self.name, stock_code, e)
            return None
