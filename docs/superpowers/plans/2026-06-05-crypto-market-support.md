# 数字货币（crypto）市场支持 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 按任务逐个实现。步骤用 checkbox（`- [ ]`）跟踪。

**Goal:** 把数字货币现货（如 `BTC/USDT`）作为第 4 个 `region` 纳入现有每日分析流程，从 Binance/OKX/Coinbase 公共行情（只读、免 Key）取数，复用技术分析 + LLM 买卖建议 + 报告 + 推送。

**Architecture:** crypto 用 `/` 判别（`is_crypto_code`），主防线是"数据路由只选 crypto fetcher"（`get_daily_data`/`get_realtime_quote`/`_filter_daily_fetchers_for_market`），次防线是共享股票判别对 `/` 返回 False。三个交易所 fetcher 继承统一基类 `CryptoExchangeBase`。交易日历对 crypto 恒开市（7×24）。下游报告/通知靠现有 Optional 字段降级，仅增 crypto LLM 指引与展示格式。

**Tech Stack:** Python 3.10、pandas、requests（已有依赖，不引入 ccxt）、pytest（`-m "not network"` 离线 + `@pytest.mark.network` 在线 smoke）。

**Spec:** `docs/superpowers/specs/2026-06-05-crypto-market-support-design.md`（v2.1）。

**全局约定（每个任务都遵守）：** 注释/docstring/commit 用中文（专业术语英文），代码标识符英文；commit 用 `<类型>: <描述>`，**不加** `Co-Authored-By`；每个任务最后一步 commit。

---

## Task 1: 符号识别基础 —— `is_crypto_code` / `SUPPORTED_QUOTES` / normalize crypto 分支

**Files:**
- Modify: `data_provider/base.py`（在 `normalize_stock_code` 之上新增常量与函数；并改 `normalize_stock_code:68`）
- Modify: `data_provider/__init__.py:45-62`（导出 `is_crypto_code`、`SUPPORTED_QUOTES`）
- Test: `tests/test_crypto_symbol_routing.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_crypto_symbol_routing.py
"""crypto 符号识别与规范化测试。"""
import pytest
from data_provider import is_crypto_code, SUPPORTED_QUOTES
from data_provider.base import normalize_stock_code


@pytest.mark.parametrize("code,expected", [
    ("BTC/USDT", True), ("eth/usdt", True), ("BTC/USDC", True), ("ETH/BTC", True),
    ("BTC/CNY", False),   # 不受支持的计价币
    ("BTCUSDT", False),   # 无斜杠不算 crypto
    ("AAPL", False), ("600519", False), ("00700", False), ("hk00700", False),
    ("", False), ("BTC/", False), ("/USDT", False), ("A/B/C", False),
])
def test_is_crypto_code(code, expected):
    assert is_crypto_code(code) is expected


def test_supported_quotes_contains_usdt():
    assert "USDT" in SUPPORTED_QUOTES


@pytest.mark.parametrize("raw,norm", [
    ("btc/usdt", "BTC/USDT"), ("BTC/USDT", "BTC/USDT"), (" eth/usdt ", "ETH/USDT"),
])
def test_normalize_keeps_crypto(raw, norm):
    assert normalize_stock_code(raw) == norm


@pytest.mark.parametrize("code,norm", [
    ("SH600519", "600519"), ("600519", "600519"), ("AAPL", "AAPL"), ("hk1810", "HK01810"),
])
def test_normalize_stock_unchanged(code, norm):
    # 回归：股票代码规范化行为不变
    assert normalize_stock_code(code) == norm
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_crypto_symbol_routing.py -q`
Expected: FAIL（`ImportError: cannot import name 'is_crypto_code'`）

- [ ] **Step 3: 实现 `SUPPORTED_QUOTES` 与 `is_crypto_code`，并给 `normalize_stock_code` 加 crypto 前置分支**

在 `data_provider/base.py` 中 `STANDARD_COLUMNS = [...]`（约 `:38`）之后新增：

```python
# 受支持的 crypto 计价币（QUOTE）。法币（CNY/JPY 等）不支持，避免跨汇率换算。
SUPPORTED_QUOTES = {"USDT", "USDC", "USD", "BUSD", "BTC", "ETH"}


def is_crypto_code(code: str) -> bool:
    """判定是否为受支持的数字货币现货代码（形如 BASE/QUOTE，如 BTC/USDT）。

    规则：含且仅含一个 '/'，BASE 非空，QUOTE 属于 SUPPORTED_QUOTES。
    A股/港股/美股代码均不含 '/'，因此零冲突。
    """
    if not code or "/" not in code:
        return False
    parts = code.strip().upper().split("/")
    if len(parts) != 2:
        return False
    base, quote = parts
    return bool(base) and quote in SUPPORTED_QUOTES
```

在 `normalize_stock_code`（`:68`）函数体最前面（`code = stock_code.strip()` 之后、`upper = code.upper()` 之前）插入：

```python
    code = stock_code.strip()
    # crypto（BASE/QUOTE）保持原样、仅大写；股票分支逻辑不变
    if "/" in code:
        return code.upper()
    upper = code.upper()
```

- [ ] **Step 4: 导出新符号**

在 `data_provider/__init__.py` 顶部从 `.base` 导入处加入 `is_crypto_code, SUPPORTED_QUOTES`（与现有 `normalize_stock_code` 同一来源），并在 `__all__`（`:45-62`）追加：

```python
    'is_crypto_code',
    'SUPPORTED_QUOTES',
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_crypto_symbol_routing.py -q`
Expected: PASS（全部用例）

- [ ] **Step 6: commit**

```bash
git add data_provider/base.py data_provider/__init__.py tests/test_crypto_symbol_routing.py
git commit -m "feat: 新增 crypto 符号识别 is_crypto_code 与 SUPPORTED_QUOTES"
```

---

## Task 2: 共享股票判别对 `/` 显式排除（defense-in-depth）

> 说明：`is_us_stock_code` 正则与 `_is_hk_market` 本就对含 `/` 的代码返回 False；本任务加一行显式 guard，固化契约、防未来正则放宽误伤。

**Files:**
- Modify: `data_provider/us_index_mapping.py:65`（`is_us_stock_code`）
- Modify: `data_provider/base.py:149`（`_is_hk_market`）
- Modify: `data_provider/akshare_fetcher.py:117`（`_is_hk_code`）
- Test: `tests/test_crypto_symbol_routing.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_crypto_symbol_routing.py
from data_provider.us_index_mapping import is_us_stock_code
from data_provider.base import _is_hk_market
from data_provider.akshare_fetcher import _is_hk_code


@pytest.mark.parametrize("code", ["BTC/USDT", "ETH/USDT", "eth/btc"])
def test_stock_predicates_reject_crypto(code):
    assert is_us_stock_code(code) is False
    assert _is_hk_market(code) is False
    assert _is_hk_code(code) is False
```

- [ ] **Step 2: 运行确认通过或失败**

Run: `python -m pytest tests/test_crypto_symbol_routing.py::test_stock_predicates_reject_crypto -q`
Expected: 很可能已 PASS（正则天然不匹配）。若已 PASS，仍执行 Step 3 固化显式 guard 后再次确认。

- [ ] **Step 3: 加显式 guard**

`data_provider/us_index_mapping.py` 的 `is_us_stock_code`，在 `normalized = (code or '').strip().upper()` 之后加：

```python
    if "/" in normalized:  # crypto（BASE/QUOTE），非美股
        return False
```

`data_provider/base.py` 的 `_is_hk_market`，在 `normalized = (code or "").strip().upper()` 之后加：

```python
    if "/" in normalized:  # crypto，非港股
        return False
```

`data_provider/akshare_fetcher.py` 的 `_is_hk_code`，在 `code = stock_code.strip().lower()` 之后加：

```python
    if "/" in code:  # crypto，非港股
        return False
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_crypto_symbol_routing.py -q`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add data_provider/us_index_mapping.py data_provider/base.py data_provider/akshare_fetcher.py tests/test_crypto_symbol_routing.py
git commit -m "feat: 共享股票判别函数对 crypto 代码显式返回 False"
```

---

## Task 3: `CryptoExchangeBase` 交易所行情基类

**Files:**
- Create: `data_provider/crypto_base.py`
- Test: `tests/test_crypto_fetchers.py`

设计：基类实现 `BaseFetcher` 的抽象方法 `_fetch_raw_data`/`_normalize_data` 与 `get_realtime_quote`；交易所差异由子类实现 4 个钩子：`_to_exchange_symbol`、`_request_klines`、`_parse_klines`、`_request_ticker`、`_parse_ticker`。`_request_*`（HTTP）在子类，`_parse_*`（纯解析）便于离线 fixture 测试。

- [ ] **Step 1: 写失败测试（用一个最小假子类，仅测纯解析/规范化，不打网络）**

```python
# tests/test_crypto_fetchers.py
"""crypto fetcher 解析与规范化测试（离线）。"""
import pandas as pd
import pytest
from data_provider.base import STANDARD_COLUMNS
from data_provider.crypto_base import CryptoExchangeBase
from data_provider.realtime_types import UnifiedRealtimeQuote, RealtimeSource


class _FakeCrypto(CryptoExchangeBase):
    name = "FakeCrypto"
    priority = 99

    def _to_exchange_symbol(self, code):
        return code.replace("/", "")

    def _request_klines(self, symbol, days):
        # 两根日线：[date_ms, o, h, l, c, volume, amount]
        return [
            [1717200000000, "100", "110", "90", "105", "10", "1050"],
            [1717286400000, "105", "120", "100", "115", "20", "2300"],
        ]

    def _parse_klines(self, raw):
        return pd.DataFrame(
            [
                {"date": r[0], "open": float(r[1]), "high": float(r[2]),
                 "low": float(r[3]), "close": float(r[4]),
                 "volume": float(r[5]), "amount": float(r[6])}
                for r in raw
            ]
        )

    def _request_ticker(self, symbol):
        return {"price": "115", "pct": "9.52", "vol": "20", "amt": "2300", "high": "120", "low": "100"}

    def _parse_ticker(self, raw, code):
        return UnifiedRealtimeQuote(
            code=code, name=code, source=RealtimeSource.FALLBACK,
            price=float(raw["price"]), change_pct=float(raw["pct"]),
            volume=float(raw["vol"]), amount=float(raw["amt"]),
            high=float(raw["high"]), low=float(raw["low"]),
        )


def test_fetch_and_normalize_to_standard_columns():
    f = _FakeCrypto()
    # 直接测纯解析 + 规范化（不依赖技术指标计算与网络）
    raw = f._fetch_raw_data("BTC/USDT", "2024-06-01", "2024-06-02")
    norm = f._normalize_data(raw, "BTC/USDT")
    for col in STANDARD_COLUMNS:
        assert col in norm.columns
    # 日期已转为 YYYY-MM-DD 字符串
    assert isinstance(norm.iloc[0]["date"], str) and len(norm.iloc[0]["date"]) == 10
    # pct_chg 由 close 计算：第二根 (115-105)/105*100 ≈ 9.52
    assert round(norm.iloc[1]["pct_chg"], 2) == 9.52
    assert norm.iloc[0]["code"] == "BTC/USDT"


def test_realtime_quote_returns_unified():
    f = _FakeCrypto()
    q = f.get_realtime_quote("BTC/USDT")
    assert isinstance(q, UnifiedRealtimeQuote)
    assert q.price == 115.0 and q.pe_ratio is None  # 估值字段保持 None


def test_days_to_limit_capping():
    f = _FakeCrypto()
    assert f._days_to_limit(30) >= 30
    assert f._days_to_limit(5000) <= f.MAX_LIMIT
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_crypto_fetchers.py -q`
Expected: FAIL（`ModuleNotFoundError: data_provider.crypto_base`）

- [ ] **Step 3: 实现 `data_provider/crypto_base.py`**

```python
"""数字货币交易所行情基类（只读公共行情，免 API Key）。

子类实现交易所 HTTP 细节；本基类负责统一标准化、pct_chg 计算与异常包装。
"""
import logging
from typing import List, Optional

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
        date 为毫秒时间戳或可被 pandas 解析的值；amount 缺失时填 None 列。"""
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
```

> 注：若 `DataFetchError` 不在 `data_provider/base.py` 顶层导出，执行时确认其定义位置（`grep -n "class DataFetchError" data_provider/`）并修正 import。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_crypto_fetchers.py -q`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add data_provider/crypto_base.py tests/test_crypto_fetchers.py
git commit -m "feat: 新增 CryptoExchangeBase 数字货币行情基类"
```

---

## Task 4: `BinanceFetcher`

**Files:**
- Create: `data_provider/binance_fetcher.py`
- Test: `tests/test_crypto_fetchers.py`（追加，用录制 fixture）

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_crypto_fetchers.py
from data_provider.binance_fetcher import BinanceFetcher

# Binance /api/v3/klines 真实结构（截断到本测试需要的列）
_BINANCE_KLINES = [
    [1717200000000, "100", "110", "90", "105", "10.0", 1717286399999, "1050.0", 5, "6", "630", "0"],
    [1717286400000, "105", "120", "100", "115", "20.0", 1717372799999, "2300.0", 8, "12", "1380", "0"],
]
# Binance /api/v3/ticker/24hr（截断）
_BINANCE_TICKER = {"lastPrice": "115.0", "priceChangePercent": "9.52",
                   "volume": "20.0", "quoteVolume": "2300.0", "highPrice": "120.0", "lowPrice": "100.0"}


def test_binance_symbol_and_parse():
    f = BinanceFetcher()
    assert f._to_exchange_symbol("BTC/USDT") == "BTCUSDT"
    df = f._parse_klines(_BINANCE_KLINES)
    norm = f._normalize_data(df, "BTC/USDT")
    assert list(norm.columns)[:3] == ["code", "date", "open"]
    assert norm.iloc[1]["amount"] == 2300.0      # quoteAssetVolume
    assert norm.iloc[1]["volume"] == 20.0        # base volume
    q = f._parse_ticker(_BINANCE_TICKER, "BTC/USDT")
    assert q.price == 115.0 and q.amount == 2300.0
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_crypto_fetchers.py::test_binance_symbol_and_parse -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 `data_provider/binance_fetcher.py`**

```python
"""Binance 现货公共行情 fetcher（免 API Key）。"""
import os
import pandas as pd

from .crypto_base import CryptoExchangeBase
from .realtime_types import UnifiedRealtimeQuote, RealtimeSource


class BinanceFetcher(CryptoExchangeBase):
    name = "BinanceFetcher"
    priority = int(os.getenv("BINANCE_PRIORITY", "50"))
    MAX_LIMIT = 1000

    @property
    def _base_url(self) -> str:
        return os.getenv("BINANCE_BASE_URL", "https://api.binance.com").rstrip("/")

    def _to_exchange_symbol(self, code: str) -> str:
        return code.strip().upper().replace("/", "")

    def _request_klines(self, symbol: str, days: int) -> list:
        return self._http_get(
            f"{self._base_url}/api/v3/klines",
            {"symbol": symbol, "interval": "1d", "limit": self._days_to_limit(days)},
        )

    def _parse_klines(self, raw: list) -> pd.DataFrame:
        # [openTime, open, high, low, close, volume, closeTime, quoteAssetVolume, ...]
        return pd.DataFrame(
            [{"date": r[0], "open": r[1], "high": r[2], "low": r[3],
              "close": r[4], "volume": r[5], "amount": r[7]} for r in raw]
        )

    def _request_ticker(self, symbol: str) -> dict:
        return self._http_get(f"{self._base_url}/api/v3/ticker/24hr", {"symbol": symbol})

    def _parse_ticker(self, raw: dict, code: str) -> UnifiedRealtimeQuote:
        def f(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None
        return UnifiedRealtimeQuote(
            code=code, name=code, source=RealtimeSource.FALLBACK,
            price=f(raw.get("lastPrice")), change_pct=f(raw.get("priceChangePercent")),
            volume=f(raw.get("volume")), amount=f(raw.get("quoteVolume")),
            high=f(raw.get("highPrice")), low=f(raw.get("lowPrice")),
        )
```

> 注：`RealtimeSource` 是枚举，若无 `FALLBACK` 之外更合适的成员，沿用 `FALLBACK`；如需新增 `BINANCE/OKX/COINBASE` 成员见 Task 8 备注（可选）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_crypto_fetchers.py -q`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add data_provider/binance_fetcher.py tests/test_crypto_fetchers.py
git commit -m "feat: 新增 BinanceFetcher 现货行情数据源"
```

---

## Task 5: `OkxFetcher`

**Files:**
- Create: `data_provider/okx_fetcher.py`
- Test: `tests/test_crypto_fetchers.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_crypto_fetchers.py
from data_provider.okx_fetcher import OkxFetcher

# OKX /api/v5/market/candles 返回 {"data": [[ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm], ...]}（新→旧）
_OKX_DATA = [
    ["1717286400000", "105", "120", "100", "115", "20", "0.18", "2300", "1"],
    ["1717200000000", "100", "110", "90", "105", "10", "0.1", "1050", "1"],
]
_OKX_TICKER = {"last": "115", "open24h": "105", "vol24h": "20", "volCcy24h": "2300", "high24h": "120", "low24h": "100"}


def test_okx_symbol_and_parse():
    f = OkxFetcher()
    assert f._to_exchange_symbol("BTC/USDT") == "BTC-USDT"
    df = f._parse_klines(_OKX_DATA)
    norm = f._normalize_data(df, "BTC/USDT")   # 内部按 date 升序
    assert norm.iloc[0]["close"] == 105.0 and norm.iloc[1]["close"] == 115.0
    assert norm.iloc[1]["amount"] == 2300.0    # volCcyQuote
    q = f._parse_ticker(_OKX_TICKER, "BTC/USDT")
    assert q.price == 115.0 and round(q.change_pct, 2) == 9.52  # (115-105)/105*100
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_crypto_fetchers.py::test_okx_symbol_and_parse -q`
Expected: FAIL

- [ ] **Step 3: 实现 `data_provider/okx_fetcher.py`**

```python
"""OKX 现货公共行情 fetcher（免 API Key）。"""
import os
import pandas as pd

from .crypto_base import CryptoExchangeBase
from .realtime_types import UnifiedRealtimeQuote, RealtimeSource


class OkxFetcher(CryptoExchangeBase):
    name = "OkxFetcher"
    priority = int(os.getenv("OKX_PRIORITY", "51"))
    MAX_LIMIT = 100  # 基础 candles 接口上限

    BASE_URL = "https://www.okx.com"

    def _to_exchange_symbol(self, code: str) -> str:
        return code.strip().upper().replace("/", "-")

    def _request_klines(self, symbol: str, days: int) -> list:
        data = self._http_get(
            f"{self.BASE_URL}/api/v5/market/candles",
            {"instId": symbol, "bar": "1D", "limit": self._days_to_limit(days)},
        )
        return (data or {}).get("data", [])

    def _parse_klines(self, raw: list) -> pd.DataFrame:
        # [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        return pd.DataFrame(
            [{"date": int(r[0]), "open": r[1], "high": r[2], "low": r[3],
              "close": r[4], "volume": r[5], "amount": r[7]} for r in raw]
        )

    def _request_ticker(self, symbol: str) -> dict:
        data = self._http_get(f"{self.BASE_URL}/api/v5/market/ticker", {"instId": symbol})
        items = (data or {}).get("data", [])
        return items[0] if items else {}

    def _parse_ticker(self, raw: dict, code: str) -> UnifiedRealtimeQuote:
        def f(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None
        last = f(raw.get("last"))
        open24 = f(raw.get("open24h"))
        pct = ((last - open24) / open24 * 100) if (last is not None and open24) else None
        return UnifiedRealtimeQuote(
            code=code, name=code, source=RealtimeSource.FALLBACK,
            price=last, change_pct=pct,
            volume=f(raw.get("vol24h")), amount=f(raw.get("volCcy24h")),
            high=f(raw.get("high24h")), low=f(raw.get("low24h")),
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_crypto_fetchers.py -q`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add data_provider/okx_fetcher.py tests/test_crypto_fetchers.py
git commit -m "feat: 新增 OkxFetcher 现货行情数据源"
```

---

## Task 6: `CoinbaseFetcher`（列序特殊、无 quote volume）

**Files:**
- Create: `data_provider/coinbase_fetcher.py`
- Test: `tests/test_crypto_fetchers.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_crypto_fetchers.py
from data_provider.coinbase_fetcher import CoinbaseFetcher

# Coinbase /products/{id}/candles 返回 [[time, low, high, open, close, volume], ...]（注意列序）
_CB_CANDLES = [
    [1717286400, 100, 120, 105, 115, 20],
    [1717200000, 90, 110, 100, 105, 10],
]
_CB_TICKER = {"price": "115", "volume": "20"}


def test_coinbase_symbol_parse_amount_none():
    f = CoinbaseFetcher()
    assert f._to_exchange_symbol("BTC/USDT") == "BTC-USDT"
    df = f._parse_klines(_CB_CANDLES)
    norm = f._normalize_data(df, "BTC/USDT")
    assert norm.iloc[0]["open"] == 100.0 and norm.iloc[1]["open"] == 105.0  # 升序后
    assert pd.isna(norm.iloc[0]["amount"])      # 无 quote volume -> None/NaN，不伪造
    q = f._parse_ticker(_CB_TICKER, "BTC/USDT")
    assert q.price == 115.0 and q.amount is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_crypto_fetchers.py::test_coinbase_symbol_parse_amount_none -q`
Expected: FAIL

- [ ] **Step 3: 实现 `data_provider/coinbase_fetcher.py`**

```python
"""Coinbase Exchange 现货公共行情 fetcher（免 API Key）。

注意：candles 列序为 [time, low, high, open, close, volume]，且无 quote volume，amount 置 None。
"""
import os
import pandas as pd

from .crypto_base import CryptoExchangeBase
from .realtime_types import UnifiedRealtimeQuote, RealtimeSource


class CoinbaseFetcher(CryptoExchangeBase):
    name = "CoinbaseFetcher"
    priority = int(os.getenv("COINBASE_PRIORITY", "52"))
    MAX_LIMIT = 300

    BASE_URL = "https://api.exchange.coinbase.com"

    def _to_exchange_symbol(self, code: str) -> str:
        return code.strip().upper().replace("/", "-")

    def _request_klines(self, symbol: str, days: int) -> list:
        return self._http_get(
            f"{self.BASE_URL}/products/{symbol}/candles", {"granularity": 86400}
        )

    def _parse_klines(self, raw: list) -> pd.DataFrame:
        # [time(秒), low, high, open, close, volume]
        return pd.DataFrame(
            [{"date": int(r[0]) * 1000, "open": r[3], "high": r[2], "low": r[1],
              "close": r[4], "volume": r[5], "amount": None} for r in raw]
        )

    def _request_ticker(self, symbol: str) -> dict:
        return self._http_get(f"{self.BASE_URL}/products/{symbol}/ticker", {})

    def _parse_ticker(self, raw: dict, code: str) -> UnifiedRealtimeQuote:
        def f(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None
        return UnifiedRealtimeQuote(
            code=code, name=code, source=RealtimeSource.FALLBACK,
            price=f(raw.get("price")), volume=f(raw.get("volume")), amount=None,
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_crypto_fetchers.py -q`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add data_provider/coinbase_fetcher.py tests/test_crypto_fetchers.py
git commit -m "feat: 新增 CoinbaseFetcher 现货行情数据源"
```

---

## Task 7: 注册 crypto fetcher 与日线路由

**Files:**
- Modify: `data_provider/__init__.py`（导出三个 fetcher）
- Modify: `data_provider/base.py`：`_DAILY_MARKET_FETCHER_SUPPORT:566`、`_filter_daily_fetchers_for_market:691`、`get_daily_data` 市场判别段（约 `:1152-1270`）、`DataFetcherManager` 初始化（约 `:1095-1114` 之前构造实例）
- Test: `tests/test_crypto_fetchers.py`（追加，mock fetcher 选择）

- [ ] **Step 1: 追加失败测试（验证 crypto 路由到 crypto fetcher，且白名单过滤生效）**

```python
# 追加到 tests/test_crypto_fetchers.py
from data_provider.base import DataFetcherManager


def test_filter_keeps_only_crypto_fetchers():
    mgr = DataFetcherManager()
    fetchers = mgr._get_fetchers_snapshot()
    kept = mgr._filter_daily_fetchers_for_market(fetchers, "crypto")
    names = {f.name for f in kept}
    assert names <= {"BinanceFetcher", "OkxFetcher", "CoinbaseFetcher"}
    assert "EfinanceFetcher" not in names and "YfinanceFetcher" not in names
    assert {"BinanceFetcher", "OkxFetcher", "CoinbaseFetcher"} & names


def test_crypto_fetchers_registered():
    mgr = DataFetcherManager()
    names = {f.name for f in mgr._get_fetchers_snapshot()}
    assert {"BinanceFetcher", "OkxFetcher", "CoinbaseFetcher"} <= names
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_crypto_fetchers.py::test_filter_keeps_only_crypto_fetchers tests/test_crypto_fetchers.py::test_crypto_fetchers_registered -q`
Expected: FAIL（crypto fetcher 未注册；白名单短路返回全部）

- [ ] **Step 3a: 导出 fetcher**

`data_provider/__init__.py`：在导入区加入

```python
from .binance_fetcher import BinanceFetcher
from .okx_fetcher import OkxFetcher
from .coinbase_fetcher import CoinbaseFetcher
```

`__all__` 追加 `'BinanceFetcher', 'OkxFetcher', 'CoinbaseFetcher'`。

- [ ] **Step 3b: 支持表加 crypto**

`data_provider/base.py` 的 `_DAILY_MARKET_FETCHER_SUPPORT`（`:566`）追加：

```python
        "BinanceFetcher": {"crypto"},
        "OkxFetcher": {"crypto"},
        "CoinbaseFetcher": {"crypto"},
```

- [ ] **Step 3c: 白名单放行 crypto**

`_filter_daily_fetchers_for_market`（`:691`）把

```python
        if market not in {"cn", "hk", "us"}:
            return fetchers
```

改为

```python
        if market not in {"cn", "hk", "us", "crypto"}:
            return fetchers
```

- [ ] **Step 3d: `get_daily_data` 增 crypto 路由分支**

在 `is_hk = (not is_us) and _is_hk_market(stock_code)` 与其 `if is_hk:` 块之后、`fetchers = self._filter_fetchers_by_capability(...)` 之前，插入：

```python
        is_crypto = is_crypto_code(stock_code)
        if is_crypto:
            fetchers = self._filter_daily_fetchers_for_market(fetchers, "crypto")
```

并把后面 `market_label = "美股指数" if is_us_index else "美股" if is_us else "港股" if is_hk else "A股"` 改为：

```python
            market_label = "美股指数" if is_us_index else "美股" if is_us else "港股" if is_hk else "crypto" if is_crypto else "A股"
```

（crypto 走 `is_us` 之后的通用 fetcher 循环；filter 已只留 crypto fetcher，按 priority 顺序即 Binance→OKX→Coinbase。）

- [ ] **Step 3e: 初始化注册三个 crypto fetcher**

在 `DataFetcherManager` 初始化构造各 fetcher 处（`self._fetchers = [...]` 之前，约 `:1095` 上方），新增：

```python
        from .binance_fetcher import BinanceFetcher
        from .okx_fetcher import OkxFetcher
        from .coinbase_fetcher import CoinbaseFetcher
        crypto_fetchers = [BinanceFetcher(), OkxFetcher(), CoinbaseFetcher()]
        # 按 CRYPTO_DATA_PRIORITY 调整三者优先级（默认 binance,okx,coinbase）
        try:
            order = [s.strip().lower() for s in get_config().crypto_data_priority.split(",") if s.strip()]
            name_map = {"binance": "BinanceFetcher", "okx": "OkxFetcher", "coinbase": "CoinbaseFetcher"}
            for idx, key in enumerate(order):
                for cf in crypto_fetchers:
                    if cf.name == name_map.get(key):
                        cf.priority = 50 + idx
        except Exception:
            pass
```

并把 `self._fetchers = [efinance, akshare, pytdx, baostock, yfinance, *optional_fetchers]` 改为追加 `*crypto_fetchers`：

```python
            self._fetchers = [
                efinance,
                akshare,
                pytdx,
                baostock,
                yfinance,
                *optional_fetchers,
                *crypto_fetchers,
            ]
```

> 注：`get_config()` 在 base.py 的导入名以实际为准（`grep -n "get_config\|from .* import .*config" data_provider/base.py`）。`crypto_data_priority` 配置在 Task 13 添加；本任务可先用默认顺序，Task 13 后该读取生效。若 Task 13 尚未完成，把上面 try 块替换为直接默认顺序（priority 50/51/52 已在各类定义）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_crypto_fetchers.py -q`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add data_provider/__init__.py data_provider/base.py tests/test_crypto_fetchers.py
git commit -m "feat: 注册 crypto fetcher 并在日线路由识别 crypto 市场"
```

---

## Task 8: 实时行情 crypto 路由

**Files:**
- Modify: `data_provider/base.py` 的 `get_realtime_quote`（`source_priority` 计算处约 `:1587`，源遍历 `elif` 链约 `:1601-1704`）
- Test: `tests/test_crypto_fetchers.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_crypto_fetchers.py
def test_realtime_routes_crypto_to_crypto_priority(monkeypatch):
    mgr = DataFetcherManager()
    captured = {}

    def fake_get_by_name(name, capability=None):
        for f in mgr._get_fetchers_snapshot():
            if f.name == name:
                return f
        return None

    monkeypatch.setattr(mgr, "_get_fetcher_by_name", fake_get_by_name)

    def fake_call(fetcher, method, *a, **k):
        captured["fetcher"] = fetcher.name
        from data_provider.realtime_types import UnifiedRealtimeQuote, RealtimeSource
        return UnifiedRealtimeQuote(code="BTC/USDT", name="BTC/USDT",
                                    source=RealtimeSource.FALLBACK, price=115.0)

    monkeypatch.setattr(mgr, "_call_fetcher_method", fake_call)
    q = mgr.get_realtime_quote("BTC/USDT")
    assert q is not None and q.price == 115.0
    assert captured["fetcher"] in {"BinanceFetcher", "OkxFetcher", "CoinbaseFetcher"}
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_crypto_fetchers.py::test_realtime_routes_crypto_to_crypto_priority -q`
Expected: FAIL（crypto 落到股票源、`_get_fetcher_by_name("EfinanceFetcher")` 等，captured 不是 crypto fetcher）

- [ ] **Step 3a: crypto 用专属优先级**

在 `get_realtime_quote` 内计算 `source_priority` 处（现为 `source_priority = [source.strip().lower() ... config.realtime_source_priority.split(',') ...]`，约 `:1587`），改为：

```python
        if is_crypto_code(stock_code):
            _priority_str = getattr(config, "crypto_realtime_priority", "binance,okx,coinbase")
        else:
            _priority_str = config.realtime_source_priority
        source_priority = [
            source.strip().lower()
            for source in _priority_str.split(',')
            if source.strip()
        ]
```

- [ ] **Step 3b: 源遍历加 crypto 分派分支**

在源遍历 `elif source == "tushare":` 分支之后，新增：

```python
                elif source == "binance":
                    fetcher = self._get_fetcher_by_name("BinanceFetcher", capability="realtime_quote")
                    if fetcher is not None and hasattr(fetcher, 'get_realtime_quote'):
                        quote = self._call_fetcher_method(fetcher, 'get_realtime_quote', stock_code)

                elif source == "okx":
                    fetcher = self._get_fetcher_by_name("OkxFetcher", capability="realtime_quote")
                    if fetcher is not None and hasattr(fetcher, 'get_realtime_quote'):
                        quote = self._call_fetcher_method(fetcher, 'get_realtime_quote', stock_code)

                elif source == "coinbase":
                    fetcher = self._get_fetcher_by_name("CoinbaseFetcher", capability="realtime_quote")
                    if fetcher is not None and hasattr(fetcher, 'get_realtime_quote'):
                        quote = self._call_fetcher_method(fetcher, 'get_realtime_quote', stock_code)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_crypto_fetchers.py -q`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add data_provider/base.py tests/test_crypto_fetchers.py
git commit -m "feat: 实时行情按 crypto 专属优先级路由到 crypto 数据源"
```

---

## Task 9: 交易日历对 crypto 恒开市（7×24）

**Files:**
- Modify: `src/core/trading_calendar.py`：`MARKET_TIMEZONE:42`、`is_market_open:132`、`get_open_markets_today:509`、`get_market_for_stock:109`
- Test: `tests/test_crypto_calendar.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_crypto_calendar.py
"""crypto 交易日历与市场识别测试。"""
from datetime import date
from src.core.trading_calendar import (
    is_market_open, get_open_markets_today, get_market_for_stock,
)


def test_crypto_market_for_stock():
    assert get_market_for_stock("BTC/USDT") == "crypto"
    assert get_market_for_stock("600519") == "cn"   # 回归
    assert get_market_for_stock("AAPL") == "us"      # 回归


def test_crypto_always_open():
    # 选一个周六（2026-06-06 是周六）
    assert is_market_open("crypto", date(2026, 6, 6)) is True
    assert is_market_open("crypto", date(2026, 1, 1)) is True  # 元旦


def test_open_markets_today_includes_crypto():
    assert "crypto" in get_open_markets_today()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_crypto_calendar.py -q`
Expected: FAIL（`get_market_for_stock("BTC/USDT")` 返回 None；`get_open_markets_today` 无 crypto）

- [ ] **Step 3a: `MARKET_TIMEZONE` 加 crypto（不加 `MARKET_EXCHANGE`）**

`src/core/trading_calendar.py` 的 `MARKET_TIMEZONE`（`:42`）改为：

```python
MARKET_TIMEZONE = {
    "cn": "Asia/Shanghai",
    "hk": "Asia/Hong_Kong",
    "us": "America/New_York",
    "crypto": "UTC",
}
```

- [ ] **Step 3b: `is_market_open` 显式特判**

在 `is_market_open` 函数体最前（`if not _XCALS_AVAILABLE:` 之前）加：

```python
    if market == "crypto":  # 数字货币 7×24 交易，恒开市
        return True
```

- [ ] **Step 3c: `get_market_for_stock` 识别 crypto**

在 `get_market_for_stock` 的 `code = (code or "").strip().upper()` 之后、`from data_provider import ...` 之前加：

```python
    from data_provider import is_crypto_code
    if is_crypto_code(code):
        return "crypto"
```

- [ ] **Step 3d: `get_open_markets_today` 恒含 crypto**

把 `if not _XCALS_AVAILABLE: return {"cn", "hk", "us"}` 改为 `return set(MARKET_TIMEZONE.keys())`。其余不动——循环遍历 `MARKET_TIMEZONE` 时会对 crypto 调 `is_market_open("crypto", today)` 返回 True 并加入集合。

```python
    if not _XCALS_AVAILABLE:
        return set(MARKET_TIMEZONE.keys())
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_crypto_calendar.py -q`
Expected: PASS

- [ ] **Step 5: 回归（交易日历相关）+ commit**

Run: `python -m pytest tests/ -k "calendar or trading" -q -m "not network"`
Expected: PASS（现有日历测试不回归）

```bash
git add src/core/trading_calendar.py tests/test_crypto_calendar.py
git commit -m "feat: 交易日历对 crypto 恒开市并纳入今日开放市场"
```

---

## Task 10: 市场画像与 LLM 语境（CRYPTO_PROFILE + detect_market + 市场指引）

**Files:**
- Modify: `src/core/market_profile.py`（新增 `CRYPTO_PROFILE`、`get_profile:70` 加分支）
- Modify: `src/market_context.py`（`detect_market:16` 加 crypto、`_MARKET_ROLES`/`_MARKET_GUIDELINES:48` 加 crypto）
- Test: `tests/test_crypto_calendar.py`（追加，或新建 `tests/test_crypto_profile.py`）

- [ ] **Step 1: 追加失败测试**

```python
# tests/test_crypto_profile.py
from src.core.market_profile import get_profile, CRYPTO_PROFILE
from src.market_context import detect_market, get_market_guidelines, get_market_role


def test_crypto_profile():
    p = get_profile("crypto")
    assert p is CRYPTO_PROFILE
    assert p.region == "crypto" and p.has_market_stats is False


def test_detect_market_crypto():
    assert detect_market("BTC/USDT") == "crypto"
    assert detect_market("600519") == "cn"   # 回归
    assert detect_market("AAPL") == "us"      # 回归


def test_crypto_guidelines_present():
    g = get_market_guidelines("BTC/USDT", lang="zh")
    assert "数字货币" in g or "加密" in g
    assert get_market_role("BTC/USDT", lang="zh")  # 非空
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_crypto_profile.py -q`
Expected: FAIL（无 `CRYPTO_PROFILE`；`detect_market("BTC/USDT")` 返回 cn）

- [ ] **Step 3a: 新增 `CRYPTO_PROFILE` 与 `get_profile` 分支**

`src/core/market_profile.py` 在 `US_PROFILE` 定义之后新增：

```python
CRYPTO_PROFILE = MarketProfile(
    region="crypto",
    mood_index_code="BTC/USDT",
    news_queries=[
        "比特币 行情",
        "以太坊 行情",
        "crypto market",
        "加密货币 大盘",
    ],
    prompt_index_hint="以 BTC/ETH 等主流币走势衡量加密市场整体情绪与风险偏好",
    has_market_stats=False,
    has_sector_rankings=False,
)
```

`get_profile`（`:70`）改为：

```python
def get_profile(region: str) -> MarketProfile:
    """根据 region 返回对应的 MarketProfile"""
    if region == "us":
        return US_PROFILE
    if region == "hk":
        return HK_PROFILE
    if region == "crypto":
        return CRYPTO_PROFILE
    return CN_PROFILE
```

- [ ] **Step 3b: `detect_market` 识别 crypto（LLM 语境）**

`src/market_context.py` 的 `detect_market`，在 `code = stock_code.strip().upper()` 之后、HK 判断之前加：

```python
    # crypto 现货：BASE/QUOTE（如 BTC/USDT），优先于股票规则
    if "/" in code:
        return "crypto"
```

- [ ] **Step 3c: `_MARKET_ROLES` / `_MARKET_GUIDELINES` 加 crypto**

`_MARKET_ROLES` 追加：

```python
    "crypto": {
        "zh": "数字货币",
        "en": "Cryptocurrency",
    },
```

`_MARKET_GUIDELINES` 追加：

```python
    "crypto": {
        "zh": (
            "- 本次分析对象为 **数字货币现货**（如 BTC/USDT）。\n"
            "- crypto 为 7×24 连续交易，无涨跌停、无 T+1、无盘前盘后；波动极大、"
            "受流动性/资金费率/宏观与监管消息影响显著，需关注杠杆与交易所价差风险。\n"
            "- 不存在市盈率/换手率等传统基本面指标，相关字段缺失属正常，请勿据此编造。"
        ),
        "en": (
            "- This analysis covers a **cryptocurrency spot pair** (e.g. BTC/USDT).\n"
            "- Crypto trades 24/7 with no price limits, no T+1, no pre/after-market. "
            "Extremely volatile; watch liquidity, funding rates, macro/regulatory news, leverage and exchange spreads.\n"
            "- Traditional fundamentals (PE, turnover) do not exist; missing such fields is expected — do not fabricate."
        ),
    },
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_crypto_profile.py -q`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add src/core/market_profile.py src/market_context.py tests/test_crypto_profile.py
git commit -m "feat: 新增 crypto 市场画像与 LLM 市场指引"
```

---

## Task 11: 展示格式（价格动态精度 + 成交量/额单位）

**Files:**
- Modify: `src/analyzer.py`：`_format_price:3354`、`_format_volume:3323`、`_format_amount:3334`
- Test: `tests/test_crypto_format.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_crypto_format.py
from src.analyzer import StockAnalyzer  # 若 _format_* 在其它类，import 对应类（见 Step 3 注）

a = StockAnalyzer()


def test_price_dynamic_precision():
    assert a._format_price(45000.0) == "45000.00"   # 大额仍 2 位
    assert a._format_price(0.00000123) not in ("0.00", "N/A")  # 小币不丢精度
    assert a._format_price(None) == "N/A"


def test_volume_unit_market_aware():
    # 默认（股票）行为不变
    assert "股" in a._format_volume(12000.0)
    # crypto：传 unit 时以币种 base 为单位、不带"股"
    out = a._format_volume(12000.0, unit="BTC")
    assert "股" not in out and "BTC" in out


def test_amount_unit_market_aware():
    assert "元" in a._format_amount(12000.0)
    out = a._format_amount(12000.0, currency="USDT")
    assert "USDT" in out and "元" not in out
    assert a._format_amount(None) == "N/A"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_crypto_format.py -q`
Expected: FAIL（`_format_volume` 不接受 `unit`；小币显示 `0.00`）

- [ ] **Step 3: 实现（保持股票默认行为，新增可选参数）**

> 注：执行前确认 `_format_*` 所属类：`grep -n "def _format_price" src/analyzer.py` 并看其类名（测试 import 对应类）。

`_format_price` 改为动态精度：

```python
    def _format_price(self, value: Optional[float]) -> str:
        """格式化价格显示（按数量级动态精度，兼容大额股票与极小币种）"""
        if value is None:
            return 'N/A'
        try:
            v = float(value)
        except (TypeError, ValueError):
            return 'N/A'
        av = abs(v)
        if av >= 1:
            return f"{v:.2f}"
        if av >= 0.0001:
            return f"{v:.6f}"
        return f"{v:.8f}"
```

`_format_volume` 增加可选 `unit`：

```python
    def _format_volume(self, volume: Optional[float], unit: Optional[str] = None) -> str:
        """格式化成交量显示。unit 为 None 时按股票（亿股/万股/股）；指定时按该 base 资产单位。"""
        if volume is None:
            return 'N/A'
        if unit:
            if volume >= 1e8:
                return f"{volume / 1e8:.2f} 亿{unit}"
            if volume >= 1e4:
                return f"{volume / 1e4:.2f} 万{unit}"
            return f"{volume:.4f} {unit}"
        if volume >= 1e8:
            return f"{volume / 1e8:.2f} 亿股"
        elif volume >= 1e4:
            return f"{volume / 1e4:.2f} 万股"
        else:
            return f"{volume:.0f} 股"
```

`_format_amount` 增加可选 `currency`：

```python
    def _format_amount(self, amount: Optional[float], currency: Optional[str] = None) -> str:
        """格式化成交额显示。currency 为 None 时按人民币（亿元/万元/元）；指定时按该计价币。"""
        if amount is None:
            return 'N/A'
        cur = currency or "元"
        if amount >= 1e8:
            return f"{amount / 1e8:.2f} 亿{cur}"
        elif amount >= 1e4:
            return f"{amount / 1e4:.2f} 万{cur}"
        else:
            return f"{amount:.0f} {cur}"
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_crypto_format.py -q`
Expected: PASS

- [ ] **Step 5: 接入 crypto 单位（调用点）**

找到报告中调用 `_format_volume(`/`_format_amount(` 的位置，对 crypto 标的传入单位：

Run: `grep -n "_format_volume(\|_format_amount(" src/analyzer.py`

对每个消费实时/日线行情的调用点，按以下模式改造（`code` 为当前标的）：

```python
        from data_provider import is_crypto_code
        if is_crypto_code(code):
            base, quote = code.upper().split("/")
            vol_str = self._format_volume(volume, unit=base)
            amt_str = self._format_amount(amount, currency=quote)
        else:
            vol_str = self._format_volume(volume)
            amt_str = self._format_amount(amount)
```

- [ ] **Step 6: 运行确认通过（回归 analyzer 相关）+ commit**

Run: `python -m pytest tests/test_crypto_format.py tests/ -k "analyzer or format" -q -m "not network"`
Expected: PASS

```bash
git add src/analyzer.py tests/test_crypto_format.py
git commit -m "feat: 报告展示支持 crypto 价格动态精度与币种单位"
```

---

## Task 12: API / 前端 symbol 校验放行 crypto

**Files:**
- Modify: `api/v1/endpoints/stocks.py:77`（`_STOCK_CODE_RE`）
- Modify: `apps/dsa-web/`（前端 `validateStockCode`，镜像同规则）
- Test: `tests/test_crypto_api_validation.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_crypto_api_validation.py
import pytest
from api.v1.endpoints.stocks import _validate_and_normalize_stock_code
from fastapi import HTTPException


def test_accepts_crypto():
    assert _validate_and_normalize_stock_code("BTC/USDT") == "BTC/USDT"
    assert _validate_and_normalize_stock_code("eth/usdt") == "ETH/USDT"


def test_rejects_unsupported_quote():
    with pytest.raises(HTTPException):
        _validate_and_normalize_stock_code("BTC/CNY")


def test_stock_still_valid():
    assert _validate_and_normalize_stock_code("600519") == "600519"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_crypto_api_validation.py -q`
Expected: FAIL（`BTC/USDT` 被拒 400）

- [ ] **Step 3: 正则加 crypto 分支**

`api/v1/endpoints/stocks.py` 的 `_STOCK_CODE_RE`，在 US ticker 这一行之后、`r")$"` 之前加一行 crypto 分支（与 `SUPPORTED_QUOTES` 对齐）：

```python
    r"|[A-Z0-9]{1,10}/(?:USDT|USDC|USD|BUSD|BTC|ETH)"   # crypto BASE/QUOTE
```

（已有 `re.IGNORECASE`，小写输入也匹配。`BTC/CNY` 因 QUOTE 不在集合而被拒。）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_crypto_api_validation.py -q`
Expected: PASS

- [ ] **Step 5: 同步前端校验**

Run: `grep -rn "validateStockCode" apps/dsa-web/src`
在该函数的代码格式校验中加入等价的 crypto 正则分支：`/^[A-Z0-9]{1,10}\/(USDT|USDC|USD|BUSD|BTC|ETH)$/i`，并确保提交前 `cd apps/dsa-web && npm run lint && npm run build` 通过。

- [ ] **Step 6: commit**

```bash
git add api/v1/endpoints/stocks.py apps/dsa-web tests/test_crypto_api_validation.py
git commit -m "feat: API 与前端 symbol 校验放行 crypto 交易对"
```

---

## Task 13: 配置项（CRYPTO_DATA_PRIORITY / CRYPTO_REALTIME_PRIORITY / BINANCE_BASE_URL）+ 文档

**Files:**
- Modify: `src/config.py`（仿 `realtime_source_priority:906` 与 `_resolve_realtime_source_priority:2279`）
- Modify: `.env.example`、`NOTES.md`、`docs/CHANGELOG.md`
- Test: `tests/test_crypto_config.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_crypto_config.py
from src.config import Config


def test_crypto_config_defaults(monkeypatch):
    for k in ("CRYPTO_DATA_PRIORITY", "CRYPTO_REALTIME_PRIORITY", "BINANCE_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    cfg = Config()  # 若有 from_env 工厂则改用之
    assert cfg.crypto_data_priority == "binance,okx,coinbase"
    assert cfg.crypto_realtime_priority == "binance,okx,coinbase"
    assert cfg.binance_base_url == "https://api.binance.com"
```

> 注：执行前确认 `Config` 的实例化/装载方式（`grep -n "class Config\|def from_env\|get_config" src/config.py`），测试按真实工厂调整。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_crypto_config.py -q`
Expected: FAIL（无 `crypto_data_priority` 属性）

- [ ] **Step 3: 加配置字段**

在 `src/config.py` 的 `realtime_source_priority` 字段（`:906`）附近新增：

```python
    # crypto 日线数据源优先级（逗号分隔，默认 binance,okx,coinbase）
    crypto_data_priority: str = "binance,okx,coinbase"
    # crypto 实时行情数据源优先级
    crypto_realtime_priority: str = "binance,okx,coinbase"
    # Binance 公共行情 Base URL（地区受限可切 https://data-api.binance.vision）
    binance_base_url: str = "https://api.binance.com"
```

若该 dataclass 通过环境变量装载（参照 `_resolve_realtime_source_priority`），在装载处用 `os.getenv` 注入：

```python
        crypto_data_priority=os.getenv("CRYPTO_DATA_PRIORITY", "binance,okx,coinbase"),
        crypto_realtime_priority=os.getenv("CRYPTO_REALTIME_PRIORITY", "binance,okx,coinbase"),
        binance_base_url=os.getenv("BINANCE_BASE_URL", "https://api.binance.com"),
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_crypto_config.py -q`
Expected: PASS

- [ ] **Step 5: 更新 `.env.example` / `NOTES.md` / `CHANGELOG`**

`.env.example`（在 LLM 配置块后另起一段）追加：

```bash
# ============ 数字货币（crypto）行情（只读公共行情，免 API Key）============
# 自选股可混排 crypto：STOCK_LIST=600519,hk00700,AAPL,BTC/USDT,ETH/USDT
# 计价币（QUOTE）支持：USDT/USDC/USD/BUSD/BTC/ETH
# CRYPTO_DATA_PRIORITY=binance,okx,coinbase        # 日线源顺序
# CRYPTO_REALTIME_PRIORITY=binance,okx,coinbase    # 实时源顺序
# BINANCE_BASE_URL=https://api.binance.com         # 地区受限可切 https://data-api.binance.vision
```

`NOTES.md` 在"自选股代码格式"处补一行：`数字货币：BASE/QUOTE，如 BTC/USDT、ETH/USDT（只读行情，无需 API Key）`。

`docs/CHANGELOG.md` 的 `[Unreleased]` 段按扁平格式追加（每条独立一行）：

```markdown
- [新功能] 新增数字货币（crypto）市场支持：A股/港股/美股之外可分析 BTC/USDT 等现货（Binance/OKX/Coinbase 公共行情，只读）
```

- [ ] **Step 6: commit**

```bash
git add src/config.py .env.example NOTES.md docs/CHANGELOG.md tests/test_crypto_config.py
git commit -m "feat: 新增 crypto 数据源配置项与文档"
```

---

## Task 14: 端到端验证与回归

**Files:** 无新增（验证任务）

- [ ] **Step 1: 全量离线测试（回归 + 新增）**

Run: `python -m pytest -m "not network" -q`
Expected: PASS（现有 A股/港股/美股 行为不回归；crypto 新测试通过）

- [ ] **Step 2: 编译检查**

Run: `python -m py_compile data_provider/crypto_base.py data_provider/binance_fetcher.py data_provider/okx_fetcher.py data_provider/coinbase_fetcher.py data_provider/base.py src/core/trading_calendar.py src/core/market_profile.py src/market_context.py src/analyzer.py src/config.py api/v1/endpoints/stocks.py`
Expected: 无输出（成功）

- [ ] **Step 3: 在线 smoke（需外网；若环境无外网则跳过并记录）**

Run: `.venv/bin/python main.py --dry-run --stocks BTC/USDT --no-notify`
Expected: 日志显示用 crypto 数据源（Binance/OKX/Coinbase 其一）取到 K 线与实时价；不报错、不误路由到 A股源。

- [ ] **Step 4: 仅 crypto + 非交易日 不被跳过（验证日历旁路）**

Run: `.venv/bin/python main.py --dry-run --stocks BTC/USDT --no-notify --no-market-review`
Expected: 不出现"今日所有相关市场均为非交易日，跳过执行"（crypto 恒纳入）。

- [ ] **Step 5: `ci_gate` + commit（如有未提交的零散改动）**

Run: `./scripts/ci_gate.sh`
Expected: PASS

```bash
git add -A
git commit -m "test: crypto 端到端与回归验证"
```

---

## 验收对照（实现完成后逐条核对 spec §9）

1. `main.py --dry-run --stocks BTC/USDT` 走 crypto 源取数 → Task 4-8、14。
2. crypto 任意自然日纳入分析 → Task 9、14-Step4。
3. Binance 失败 fallback OKX/Coinbase，全失败仅跳过该标的 → Task 7（manager fallback）。
4. 缺失字段 N/A、小币价不为 0.00、量单位非"股" → Task 10-11。
5. API/Web 接受 BTC/USDT、拒绝 BTC/CNY → Task 12。
6. 现有 A股/港股/美股 回归全绿 → Task 14-Step1。
7. 全程只读、无下单/私钥 → 设计层面（无任何交易 API 调用）。
