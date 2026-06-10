# crypto 一等公民永续合约标的 Implementation Plan（C+D）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `python main.py --stocks BTC/USDT:PERP` 识别为 OKX 永续标的，从 OKX SWAP 拉永续日线 + 实时做技术分析，复用子项目 A 的 funding/mark/OI 注入，产出与现货一致结构的报告。

**Architecture:** 新增独立 `is_perp_code`（notation `BASE/QUOTE:PERP`，不动 spot-only `is_crypto_code`）；新 `OkxPerpetualFetcher(OkxFetcher)` 仅覆写 `_to_exchange_symbol`→`BTC-USDT-SWAP`；日线经新市场标签 `crypto_perp`（disjoint 池）路由、实时经 `okx_perp` 专属分发；crypto_base 校验/derivatives 注入/单位解析放宽为 crypto-like。无 Web 改动（perp 复用 crypto 渲染）。

**Tech Stack:** Python（pytest，requests-mock via monkeypatch `_http_get`）。

**测试运行约定：** `PYTHONPATH="$PWD" .venv/bin/python -m pytest ...`。本子项目**无前端改动**，回归仅 `ci_gate.sh all`。

**关键事实（已读码核实，2026-06-10）：**
- `is_crypto_code`（`data_provider/base.py:46-58`）spot-only，保持不变。
- `normalize_stock_code:113` `if "/" in code: return code.upper()` → perp 含 `/` 已正确保留，**无需改动**。
- `_filter_daily_fetchers_for_market:720-744`：guard `if market not in {"cn","hk","us","crypto"}: return fetchers`（**必须补 `crypto_perp`**）；过滤逻辑 `supported=_DAILY_MARKET_FETCHER_SUPPORT.get(name); skip if supported is not None and market not in supported`。
- `_DAILY_MARKET_FETCHER_SUPPORT`（`:591-604`）；`_init_default_fetchers` `crypto_fetchers=[BinanceFetcher(),OkxFetcher(),CoinbaseFetcher()]`（`:1127`）→ `self._fetchers`（`:1150`）。
- `get_daily_data` 路由块（`:1211-1219`）：`is_us/is_hk/is_crypto` + `if is_hk/elif is_crypto` 过滤。
- `get_realtime_quote`（`:1553`）：`if is_crypto_code: crypto_realtime_priority else realtime_source_priority`（`:1638`）；source 分发循环 `elif source=="okx": _get_fetcher_by_name("OkxFetcher",...)`、`elif source=="coinbase": ...`（`:1692-1700`）。
- `OkxFetcher`（`okx_fetcher.py`）：`_to_exchange_symbol`(`/`→`-`)、`_request_klines`(`/market/candles`)、`_request_ticker`(`/market/ticker`)、`_parse_*`。`CryptoExchangeBase`（`crypto_base.py`）`_fetch_raw_data:98` 与 `get_realtime_quote:133` 均 `if not is_crypto_code(code): reject`（**放宽为 crypto-like**）；`_http_get` 是抓取 seam（可 monkeypatch）；import 行 `crypto_base.py:12`。
- `market_context.py:28` `if "/" in code: return "crypto"` → perp 已覆盖，**无需改动**。
- `_crypto_volume_amount_units`（`analyzer.py:159-171`）`if not is_crypto_code: return None,None; base,quote=split("/")`（**加 perp 分支**）。
- derivatives 注入：`CryptoDerivativesService.collect`（`crypto_derivatives_service.py`）gate `is_crypto_code` + `partition("/")`（**放宽 + perp 解析**）。
- `pipeline.py:316` `if realtime_quote:` → 实时缺失优雅降级（非致命）。

---

### Task 1: 分类 `is_perp_code` / `parse_perp_code` / `is_crypto_like`

**Files:**
- Test: `tests/test_perp_code.py`（新）
- Modify: `data_provider/base.py`（在 `is_crypto_code` 之后追加）

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_perp_code.py`：

```python
# -*- coding: utf-8 -*-
"""OKX 永续标的分类：BASE/QUOTE:PERP（线性 USDT/USDC）。"""
from data_provider.base import is_perp_code, parse_perp_code, is_crypto_like, is_crypto_code


def test_is_perp_code_linear():
    assert is_perp_code("BTC/USDT:PERP")
    assert is_perp_code("eth/usdc:perp")          # 大小写不敏感
    assert is_perp_code("BTC/USDT:perp")


def test_is_perp_code_rejects():
    assert not is_perp_code("BTC/USD:PERP")        # 非线性计价
    assert not is_perp_code("BTC/USDT")            # 现货
    assert not is_perp_code("BTC-USDT-SWAP")       # 非本系统 notation
    assert not is_perp_code("600519")
    assert not is_perp_code("")
    assert not is_perp_code(None)
    assert not is_perp_code("/USDT:PERP")          # base 空


def test_parse_perp_code():
    assert parse_perp_code("BTC/USDT:PERP") == ("BTC", "USDT")
    assert parse_perp_code("eth/usdc:perp") == ("ETH", "USDC")
    assert parse_perp_code("BTC/USDT") == (None, None)


def test_is_crypto_like():
    assert is_crypto_like("BTC/USDT")              # 现货
    assert is_crypto_like("BTC/USDT:PERP")         # 永续
    assert not is_crypto_like("600519")
    # 互斥：perp 不是 spot
    assert is_perp_code("BTC/USDT:PERP") and not is_crypto_code("BTC/USDT:PERP")
```

- [ ] **Step 2: 运行确认失败** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_perp_code.py -q`（ImportError）。

- [ ] **Step 3: 实现** — 在 `data_provider/base.py` `is_crypto_code` 函数之后追加：

```python
_LINEAR_PERP_QUOTES = {"USDT", "USDC"}   # OKX 线性永续计价
_PERP_SUFFIX = ":PERP"


def is_perp_code(code: str) -> bool:
    """识别 OKX 永续标的：BASE/QUOTE:PERP（QUOTE ∈ USDT/USDC）。is_crypto_code 现货保持独立。"""
    if not code or not str(code).strip():
        return False
    s = str(code).strip().upper()
    if not s.endswith(_PERP_SUFFIX) or "/" not in s:
        return False
    base, _, quote = s[: -len(_PERP_SUFFIX)].partition("/")
    return bool(base) and quote in _LINEAR_PERP_QUOTES


def parse_perp_code(code: str):
    """BTC/USDT:PERP -> ('BTC','USDT')；非 perp -> (None, None)。"""
    if not is_perp_code(code):
        return None, None
    s = str(code).strip().upper()
    base, _, quote = s[: -len(_PERP_SUFFIX)].partition("/")
    return base, quote


def is_crypto_like(code: str) -> bool:
    """crypto 现货或永续——用于 crypto 区路由（市场/日历/数据/实时）。"""
    return is_crypto_code(code) or is_perp_code(code)
```

- [ ] **Step 4: 运行确认通过** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_perp_code.py -q` → 4 passed。

- [ ] **Step 5: 提交**
```bash
git add tests/test_perp_code.py data_provider/base.py
git commit -m "feat: OKX 永续标的分类 is_perp_code/parse_perp_code/is_crypto_like（notation BASE/QUOTE:PERP）"
```

---

### Task 2: `OkxPerpetualFetcher` + crypto_base 校验放宽

**Files:**
- Test: `tests/test_okx_perpetual_fetcher.py`（新）
- Create: `data_provider/okx_perpetual_fetcher.py`
- Modify: `data_provider/crypto_base.py`（:12 import、:98 与 :133 校验放宽）

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_okx_perpetual_fetcher.py`：

```python
# -*- coding: utf-8 -*-
"""OkxPerpetualFetcher：perp code -> OKX SWAP instId；复用 OKX candles/ticker 解析。"""
from data_provider.okx_perpetual_fetcher import OkxPerpetualFetcher


def test_to_exchange_symbol_swap():
    f = OkxPerpetualFetcher()
    assert f._to_exchange_symbol("BTC/USDT:PERP") == "BTC-USDT-SWAP"
    assert f._to_exchange_symbol("eth/usdc:perp") == "ETH-USDC-SWAP"


def test_get_daily_data_parses_swap_candles(monkeypatch):
    f = OkxPerpetualFetcher()
    # OKX candles 响应：data=[[ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm], ...]
    fake = {"code": "0", "data": [
        ["1781049600000", "62000", "63000", "61000", "62500", "100", "6200000", "6250000", "1"],
        ["1780963200000", "61000", "62000", "60000", "61500", "120", "7200000", "7380000", "1"],
    ]}
    monkeypatch.setattr(f, "_http_get", lambda url, params=None: fake)
    df, source = f.get_daily_data("BTC/USDT:PERP", days=5)
    assert source == "OkxPerpetualFetcher"
    assert list(df["code"].unique()) == ["BTC/USDT:PERP"]      # code 串原样保留
    assert set(["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]).issubset(df.columns)
    assert df.iloc[-1]["close"] == 62500.0                      # 升序后末行=最新


def test_realtime_quote_swap_ticker(monkeypatch):
    f = OkxPerpetualFetcher()
    fake = {"code": "0", "data": [{"last": "62500", "open24h": "61000", "vol24h": "100",
                                   "volCcy24h": "6200000", "high24h": "63000", "low24h": "60000"}]}
    monkeypatch.setattr(f, "_http_get", lambda url, params=None: fake)
    q = f.get_realtime_quote("BTC/USDT:PERP")
    assert q is not None and q.price == 62500.0 and q.code == "BTC/USDT:PERP"
```

- [ ] **Step 2: 运行确认失败** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_okx_perpetual_fetcher.py -q`（ImportError；及 crypto_base 仍拒绝 perp）。

- [ ] **Step 3a: 放宽 crypto_base 校验** — `data_provider/crypto_base.py`：
  - import 行（`:12`）`from .base import BaseFetcher, STANDARD_COLUMNS, DataFetchError, is_crypto_code` 改为追加 `is_perp_code`：
    ```python
    from .base import BaseFetcher, STANDARD_COLUMNS, DataFetchError, is_crypto_code, is_perp_code
    ```
  - `_fetch_raw_data`（`:98`）`if not is_crypto_code(stock_code):` 改为 `if not (is_crypto_code(stock_code) or is_perp_code(stock_code)):`。
  - `get_realtime_quote`（`:133`）`if not is_crypto_code(stock_code):` 同样改为 `if not (is_crypto_code(stock_code) or is_perp_code(stock_code)):`。

- [ ] **Step 3b: 新建 fetcher** — `data_provider/okx_perpetual_fetcher.py`：

```python
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
```

- [ ] **Step 4: 运行确认通过** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_okx_perpetual_fetcher.py -q` → 3 passed。回归现货：`PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/ -q -k "okx or crypto_base or crypto_format" 2>/dev/null | tail -3`（spot 行为不变）。

- [ ] **Step 5: 提交**
```bash
git add tests/test_okx_perpetual_fetcher.py data_provider/okx_perpetual_fetcher.py data_provider/crypto_base.py
git commit -m "feat: OkxPerpetualFetcher（SWAP candles/ticker，复用 OkxFetcher）+ crypto_base 校验放宽 crypto-like"
```

---

### Task 3: 日线路由（注册 + guard 集 + get_daily_data 分支）

**Files:**
- Test: `tests/test_perp_daily_routing.py`（新）
- Modify: `data_provider/base.py`（:591 SUPPORT、:726 guard、:1127 注册、:1211-1219 路由）

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_perp_daily_routing.py`：

```python
# -*- coding: utf-8 -*-
"""perp 日线路由：选 crypto_perp 池（仅 OkxPerpetualFetcher），现货仍选 crypto 池。"""
from data_provider.base import DataFetcherManager


def _names(fetchers):
    return {f.name for f in fetchers}


def test_default_pool_includes_perp_fetcher():
    m = DataFetcherManager()
    assert "OkxPerpetualFetcher" in _names(m._get_fetchers_snapshot())


def test_filter_crypto_perp_keeps_only_perp_fetcher():
    m = DataFetcherManager()
    snap = m._get_fetchers_snapshot()
    perp = m._filter_daily_fetchers_for_market(snap, "crypto_perp")
    assert _names(perp) == {"OkxPerpetualFetcher"}        # disjoint：无股票/spot 源


def test_filter_crypto_excludes_perp_fetcher():
    m = DataFetcherManager()
    snap = m._get_fetchers_snapshot()
    spot = m._filter_daily_fetchers_for_market(snap, "crypto")
    assert "OkxPerpetualFetcher" not in _names(spot)
    assert {"BinanceFetcher", "OkxFetcher", "CoinbaseFetcher"}.issubset(_names(spot))
```

- [ ] **Step 2: 运行确认失败** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_perp_daily_routing.py -q`（perp 未注册；guard 对 crypto_perp 原样返回全部 → 第二个测试失败）。

- [ ] **Step 3a: 注册 SUPPORT** — `data_provider/base.py` `_DAILY_MARKET_FETCHER_SUPPORT`（`:591-604`）追加：
```python
        "OkxPerpetualFetcher": {"crypto_perp"},
```

- [ ] **Step 3b: 放宽 guard 集** — `_filter_daily_fetchers_for_market`（`:726`）：
```python
        if market not in {"cn", "hk", "us", "crypto", "crypto_perp"}:
            return fetchers
```

- [ ] **Step 3c: 注册到默认池** — `_init_default_fetchers`（`:1127`）：
```python
        from .okx_perpetual_fetcher import OkxPerpetualFetcher
        crypto_fetchers: List[BaseFetcher] = [BinanceFetcher(), OkxFetcher(), CoinbaseFetcher(), OkxPerpetualFetcher()]
```
（其余 priority 调整逻辑不变；OkxPerpetualFetcher 不在 name_map 中，保留默认 priority 55。）

- [ ] **Step 3d: get_daily_data 路由分支** — `:1211-1219`：
```python
        is_us_index = is_us_index_code(stock_code)
        is_us = is_us_index or is_us_stock_code(stock_code)
        is_hk = (not is_us) and _is_hk_market(stock_code)
        is_perp = (not is_us) and (not is_hk) and is_perp_code(stock_code)
        is_crypto = (not is_us) and (not is_hk) and (not is_perp) and is_crypto_code(stock_code)
        if is_hk:
            fetchers = self._filter_daily_fetchers_for_market(fetchers, "hk")
        elif is_perp:
            fetchers = self._filter_daily_fetchers_for_market(fetchers, "crypto_perp")
        elif is_crypto:
            fetchers = self._filter_daily_fetchers_for_market(fetchers, "crypto")
```
确认 `is_perp_code` 已在 base.py 模块内（同文件定义，无需 import）。

- [ ] **Step 4: 运行确认通过** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_perp_daily_routing.py -q` → 3 passed。`py_compile data_provider/base.py`。

- [ ] **Step 5: 提交**
```bash
git add tests/test_perp_daily_routing.py data_provider/base.py
git commit -m "feat: perp 日线路由（crypto_perp disjoint 池：注册+guard 集+get_daily_data 分支）"
```

---

### Task 4: 市场识别（perp → crypto，24/7）

**Files:**
- Test: `tests/test_perp_market_detect.py`（新）
- Modify: `src/core/trading_calendar.py`（`get_market_for_stock` :122）

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_perp_market_detect.py`：

```python
# -*- coding: utf-8 -*-
"""perp 标的被识别为 crypto 市场 → 24/7 日历。"""
from datetime import date
from src.core.trading_calendar import get_market_for_stock, is_market_open, infer_market_phase, MarketPhase


def test_perp_market_is_crypto():
    assert get_market_for_stock("BTC/USDT:PERP") == "crypto"
    assert get_market_for_stock("BTC/USDT") == "crypto"   # 现货不回归


def test_perp_is_24x7():
    # 周六（非交易日）：crypto 恒开市、恒盘中
    assert is_market_open("crypto", date(2026, 6, 6)) is True
    assert infer_market_phase("crypto") == MarketPhase.INTRADAY
```

- [ ] **Step 2: 运行确认失败** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_perp_market_detect.py -q`（perp→None）。

- [ ] **Step 3: 实现** — `src/core/trading_calendar.py` `get_market_for_stock`（`:122`）：
```python
    from data_provider import is_crypto_code, is_perp_code
    if is_crypto_code(code) or is_perp_code(code):
        return "crypto"
```
确认 `is_perp_code` 由 `data_provider/__init__.py` 导出（见 Task 9 审计；若未导出，在 `__init__.py` 追加导出——`data_provider/__init__.py` 已导出 `is_crypto_code`，同处追加 `is_perp_code, parse_perp_code, is_crypto_like`）。

- [ ] **Step 4: 运行确认通过** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_perp_market_detect.py -q` → 2 passed。

- [ ] **Step 5: 提交**
```bash
git add tests/test_perp_market_detect.py src/core/trading_calendar.py data_provider/__init__.py
git commit -m "feat: perp 标的识别为 crypto 市场（24/7 日历）"
```

---

### Task 5: 实时路由（is_crypto_like + okx_perp 专属分发）

**Files:**
- Test: `tests/test_perp_realtime_routing.py`（新）
- Modify: `data_provider/base.py`（`get_realtime_quote` :1638 优先级 + 分发循环加 okx_perp）

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_perp_realtime_routing.py`：

```python
# -*- coding: utf-8 -*-
"""perp 实时路由：命中 okx_perp -> OkxPerpetualFetcher，不落 A 股源、不命中 spot OkxFetcher。"""
from unittest.mock import MagicMock
from data_provider.base import DataFetcherManager
from data_provider.realtime_types import UnifiedRealtimeQuote, RealtimeSource


def test_perp_realtime_hits_perp_fetcher(monkeypatch):
    m = DataFetcherManager()
    calls = {}
    def fake_get(name, capability=None):
        calls["name"] = name
        f = MagicMock()
        f.name = name
        f.get_realtime_quote.return_value = UnifiedRealtimeQuote(
            code="BTC/USDT:PERP", name="BTC/USDT:PERP", source=RealtimeSource.FALLBACK,
            price=62500.0, change_pct=1.0)
        return f
    monkeypatch.setattr(m, "_get_fetcher_by_name", fake_get)
    q = m.get_realtime_quote("BTC/USDT:PERP")
    assert q is not None and q.price == 62500.0
    assert calls["name"] == "OkxPerpetualFetcher"     # 非 OkxFetcher、非 A 股源
```

> 实现者注：若 `get_realtime_quote` 内对 quote 有 `has_basic_data()`/supplement 逻辑，确保 mock 的 quote 满足（`price+change_pct` 已够 `has_basic_data`）。如测试因 supplement 调用到第二个 source，可让 `fake_get` 对非 perp source 返回 None 并断言 `calls["name"]` 首次为 OkxPerpetualFetcher。

- [ ] **Step 2: 运行确认失败** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_perp_realtime_routing.py -q`（perp 落 A 股优先级，不会命中 OkxPerpetualFetcher）。

- [ ] **Step 3a: 优先级选择放宽** — `data_provider/base.py:1638`：
```python
        if is_crypto_code(stock_code) or is_perp_code(stock_code):
            _priority_str = getattr(config, "crypto_realtime_priority", "binance,okx,coinbase")
        else:
            _priority_str = config.realtime_source_priority
```
紧随其后（`source_priority = [...]` 之前或之后）插入 perp 专属优先级覆盖：
```python
        if is_perp_code(stock_code):
            source_priority = ["okx_perp"]
        else:
            source_priority = [s.strip().lower() for s in _priority_str.split(",") if s.strip()]
```
（替换原有的 `source_priority = [...]` 构造；保持非 perp 行为不变。）

- [ ] **Step 3b: 分发分支** — 在 source 分发循环里 `elif source == "coinbase":`（`:1697`）块之后追加：
```python
                elif source == "okx_perp":
                    fetcher = self._get_fetcher_by_name("OkxPerpetualFetcher", capability="realtime_quote")
                    if fetcher is not None and hasattr(fetcher, 'get_realtime_quote'):
                        quote = self._call_fetcher_method(fetcher, 'get_realtime_quote', stock_code)
```

- [ ] **Step 4: 运行确认通过** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_perp_realtime_routing.py -q` → 1 passed。`py_compile data_provider/base.py`。

- [ ] **Step 5: 提交**
```bash
git add tests/test_perp_realtime_routing.py data_provider/base.py
git commit -m "feat: perp 实时路由（is_crypto_like 优先级 + okx_perp 专属分发命中 OkxPerpetualFetcher）"
```

---

### Task 6: 单位 perp-aware

**Files:**
- Test: `tests/test_perp_units.py`（新）
- Modify: `src/analyzer.py`（`_crypto_volume_amount_units` :159-171）

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_perp_units.py`：

```python
# -*- coding: utf-8 -*-
"""perp 代码的成交量/额单位解析：(BASE, QUOTE)，不被 :PERP 污染。"""
from src.analyzer import _crypto_volume_amount_units


def test_perp_units():
    assert _crypto_volume_amount_units("BTC/USDT:PERP") == ("BTC", "USDT")


def test_spot_units_unchanged():
    assert _crypto_volume_amount_units("ETH/USDT") == ("ETH", "USDT")


def test_stock_units_none():
    assert _crypto_volume_amount_units("600519") == (None, None)
```

- [ ] **Step 2: 运行确认失败** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_perp_units.py -q`（perp → (None,None) 或 quote 含 `:PERP`）。

- [ ] **Step 3: 实现** — `src/analyzer.py` `_crypto_volume_amount_units`（`:159-171`）：
```python
def _crypto_volume_amount_units(code: Any) -> Tuple[Optional[str], Optional[str]]:
    """返回展示用的成交量/成交额单位。

    crypto 现货（BASE/QUOTE）或永续（BASE/QUOTE:PERP）返回 (base, quote)；
    股票（A股/港股/美股）返回 (None, None)，沿用默认的"股/元"口径。
    """
    from data_provider import is_crypto_code, is_perp_code, parse_perp_code

    text = str(code or "")
    if is_perp_code(text):
        return parse_perp_code(text)
    if not is_crypto_code(text):
        return None, None
    base, quote = text.strip().upper().split("/")
    return base, quote
```

- [ ] **Step 4: 运行确认通过** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_perp_units.py tests/test_crypto_format.py -q` → 全 pass（含现货回归）。

- [ ] **Step 5: 提交**
```bash
git add tests/test_perp_units.py src/analyzer.py
git commit -m "feat: 永续标的成交量/额单位 perp-aware（strip :PERP 取 BASE/QUOTE）"
```

---

### Task 7: derivatives 注入支持 perp code

**Files:**
- Test: `tests/test_perp_derivatives_inject.py`（新）
- Modify: `src/services/crypto_derivatives_service.py`（collect gate + 解析）

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_perp_derivatives_inject.py`：

```python
# -*- coding: utf-8 -*-
"""perp 标的也注入 funding/mark/OI（门控放宽 + perp 解析）。"""
import types
import data_provider.crypto_derivatives as cd
from src.services.crypto_derivatives_service import CryptoDerivativesService, attach_crypto_contracts


def _cfg(enabled=True):
    return types.SimpleNamespace(crypto_derivatives_enabled=enabled)


def test_collect_perp_code(monkeypatch):
    seen = {}
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda base, quote: seen.update(base=base, quote=quote) or {"funding_rate": 0.0001, "source": "okx"})
    out = CryptoDerivativesService(config=_cfg()).collect("BTC/USDT:PERP")
    assert (seen["base"], seen["quote"]) == ("BTC", "USDT")     # perp 解析正确
    assert out["funding_rate"] == 0.0001


def test_collect_spot_unchanged(monkeypatch):
    seen = {}
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda base, quote: seen.update(base=base, quote=quote) or {"source": "okx"})
    CryptoDerivativesService(config=_cfg()).collect("ETH/USDT")
    assert (seen["base"], seen["quote"]) == ("ETH", "USDT")


def test_attach_perp(monkeypatch):
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda base, quote: {"mark_price": 62500.0})
    ctx = {"code": "BTC/USDT:PERP"}
    attach_crypto_contracts(ctx, config=_cfg())
    assert ctx["crypto_contracts"]["mark_price"] == 62500.0
```

- [ ] **Step 2: 运行确认失败** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_perp_derivatives_inject.py -q`（perp 被 `is_crypto_code` gate 拒绝 → {}）。

- [ ] **Step 3: 实现** — `src/services/crypto_derivatives_service.py`：
  - import 行 `from data_provider.base import is_crypto_code` 改为 `from data_provider.base import is_crypto_code, is_perp_code, parse_perp_code`。
  - `collect`：
```python
    def collect(self, code: str) -> Dict[str, Any]:
        if not getattr(self.config, "crypto_derivatives_enabled", True):
            return {}
        code = code or ""
        if is_perp_code(code):
            base, quote = parse_perp_code(code)
        elif is_crypto_code(code):
            base, _, quote = code.partition("/")
        else:
            return {}
        return cd.fetch_perp_metrics(base, quote)
```

- [ ] **Step 4: 运行确认通过** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_perp_derivatives_inject.py tests/test_crypto_derivatives_service.py -q` → 全 pass（含现货回归）。

- [ ] **Step 5: 提交**
```bash
git add tests/test_perp_derivatives_inject.py src/services/crypto_derivatives_service.py
git commit -m "feat: derivatives 注入支持 perp code（门控放宽 crypto-like + parse_perp_code）"
```

---

### Task 8: 端到端（perp 分析跑通）

**Files:**
- Test: `tests/test_perp_instrument_e2e.py`（新）

- [ ] **Step 1: 写测试** — 端到端最小验证：seeded perp 日线 + mock 实时/derivatives → `get_daily_data` 与 `get_market_for_stock` 串起来产出可分析数据。先读现有 crypto 端到端测试（如 `tests/test_crypto_backtest.py` 或 `test_crypto_market_review_e2e.py`）确认可用的 DB seed / pipeline 构造范式，再按其风格写。最小断言（不依赖 LLM）：

```python
# -*- coding: utf-8 -*-
"""perp 标的端到端：路由→perp 日线→可技术分析；与现货链一致。"""
from data_provider.base import DataFetcherManager, is_perp_code
from src.core.trading_calendar import get_market_for_stock


def test_perp_daily_data_flows(monkeypatch):
    m = DataFetcherManager()
    fake = {"code": "0", "data": [
        ["1781049600000", "62000", "63000", "61000", "62500", "100", "6200000", "6250000", "1"],
        ["1780963200000", "61000", "62000", "60000", "61500", "120", "7200000", "7380000", "1"],
    ]}
    # 仅 perp fetcher 应被命中；mock 其 _http_get
    perp = m._get_fetcher_by_name("OkxPerpetualFetcher", capability="daily_data")
    assert perp is not None
    monkeypatch.setattr(perp, "_http_get", lambda url, params=None: fake)
    df, source = m.get_daily_data("BTC/USDT:PERP", days=5)
    assert source == "OkxPerpetualFetcher"
    assert get_market_for_stock("BTC/USDT:PERP") == "crypto"
    assert len(df) == 2 and df.iloc[-1]["close"] == 62500.0
    assert is_perp_code("BTC/USDT:PERP")
```

> 实现者注：若 `get_daily_data` 在取数后尝试落库（`save_daily_data`）需要 DB，确认测试环境用内存/临时 DB 或该路径对 manager 直调不触发持久化；如触发，参照现有 fetcher 测试隔离 DB。本测试聚焦"路由+取数+解析"贯通。

- [ ] **Step 2: 运行** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_perp_instrument_e2e.py -q` → pass（先失败再补齐前置任务已完成则直接 pass；若 DB 副作用报错，按实现者注隔离）。

- [ ] **Step 3: 提交**
```bash
git add tests/test_perp_instrument_e2e.py
git commit -m "test: perp 标的端到端（路由→perp 日线→可分析，与现货链一致）"
```

---

### Task 9: 审计 + 文档

**Files:**
- Modify: `docs/crypto-guide.md`、`docs/CHANGELOG.md`
- （审计：只读 grep，不改码；如发现误解析点，纳入对应 Task 修复或本任务补 1 个小 fix + 测试）

- [ ] **Step 1: `:`/`/` 下游审计** — 运行：
```bash
grep -rn "split(':')\|partition(':')\|\.split(\"/\")\|partition(\"/\")" src/ data_provider/ api/ | grep -iv "test" | grep -i "code\|symbol\|stock"
```
逐条确认无对 perp code 串的误解析（已知 `_crypto_volume_amount_units` 已修；`crypto_derivatives_service` 已修）。若发现新点（如某处 `code.split("/")[1]` 取 quote 会得 `USDT:PERP`），就地修 + 补 1 个断言测试，并在交付说明列出。无新点则记录"审计通过"。

- [ ] **Step 2: crypto-guide 新增节** — `docs/crypto-guide.md` 在 §1（代码格式）或末尾合适处新增：

```markdown
## 永续合约标的（perp instrument）

除现货外，可分析 OKX **永续合约**标的，notation 为 `BASE/QUOTE:PERP`（仅 USDT/USDC 线性永续）：

```bash
python main.py --stocks BTC/USDT:PERP
python main.py --stocks BTC/USDT,BTC/USDT:PERP   # 现货 + 永续并列
```

- 数据源：OKX SWAP（`/market/candles` 日线、`/market/ticker` 实时，`instId=BASE-QUOTE-SWAP`）；24/7、走 crypto 指南。
- 自带资金费率/标记价/未平仓量（与现货同样经 `CRYPTO_DERIVATIVES_ENABLED` 注入分析）。
- 永续日线以独立 code（`BTC/USDT:PERP`）入库，与现货 `BTC/USDT` 互不影响。
- 范围：仅 OKX、仅线性、暂不含 perp 回测；CLI 优先（含 `:` 的 API code-in-path 需 URL 编码 `%3A`，本期不专门验证）。
```

- [ ] **Step 3: CHANGELOG** — `docs/CHANGELOG.md` `[Unreleased]` 扁平追加（禁止 `###`）：
```markdown
- [新功能] 支持 OKX 永续合约标的（notation BASE/QUOTE:PERP，如 BTC/USDT:PERP）：独立 perp 日线 + 实时 + 自带资金费率/标记价/未平仓量，走完整 crypto 分析链；OKX-only、线性、暂不含回测
```

- [ ] **Step 4: 核对 + 提交**
```bash
grep -n "BTC/USDT:PERP\|永续合约标的\|crypto_perp" docs/crypto-guide.md docs/CHANGELOG.md
git add docs/crypto-guide.md docs/CHANGELOG.md
git commit -m "docs: 永续合约标的（BASE/QUOTE:PERP）指南 + CHANGELOG"
```

---

### Task 10: 全量回归 + 收尾

**Files:** 无（验证）

- [ ] **Step 1: ci_gate** — `PATH="$PWD/.venv/bin:$PATH" PYTHONPATH="$PWD" ./scripts/ci_gate.sh all` → `backend-gate: all checks passed`，0 失败。（无前端改动，无需 web-gate。）
- [ ] **Step 2: 自检** — perp 识别/路由/实时/单位/注入/24/7 全绿；现货与股票零回归（is_crypto_code 未变）；disjoint 池（perp 不落股票/spot 源）；无新配置；CLI 路径可跑 `--stocks BTC/USDT:PERP`（如环境可联网，手测一次 OKX SWAP 可达；否则记 network-smoke）。
- [ ] **Step 3: 收尾** — 调 `superpowers:finishing-a-development-branch` 呈现选项（默认保持本地）。

---

## Self-Review

**1. Spec coverage：** §2.1 分类→T1；§2.3 fetcher + crypto_base 放宽→T2；§2.2 日线路由（含 :726 guard、注册）→T3、市场识别→T4、实时路由（okx_perp）→T5；§2.4 单位→T6、derivatives 注入→T7、market_context/normalize「无需改动」→不产生任务（spec 已确认）；§5 端到端→T8、审计步→T9 Step1；§6 文档→T9；§4 API 边界→T9 文档声明；§7 回滚→T10。全覆盖。

**2. Placeholder scan：** 无 TBD/TODO；每步含完整代码 + 确切命令/预期。T8/T5 的"实现者注"给出了 DB 隔离 / supplement 的应对，且给了可运行测试骨架。

**3. Type consistency：** `is_perp_code`/`parse_perp_code`/`is_crypto_like` 签名贯穿 T1→T2/3/4/5/6/7；notation `BASE/QUOTE:PERP` 与 instId `BASE-QUOTE-SWAP` 映射在 T2 fetcher 与 T7 解析一致；市场标签 `crypto_perp` 在 T3 SUPPORT/guard/路由一致；实时 source 名 `okx_perp` 在 T5 优先级与分发一致；`data_provider/__init__` 导出（T4 Step3）确保 `from data_provider import is_perp_code` 在 trading_calendar/analyzer 可用。
