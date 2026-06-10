# crypto 复盘永续情绪聚合 Implementation Plan（子项目 B）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** crypto 大盘复盘对配置篮子并发聚合永续情绪（OI 加权资金费率/总未平仓量/top-mover），presence-only 注入复盘 prompt + `market_review_payload` + Web。

**Architecture:** 镜像已落地的 `market_indicators` 三层范式：`data_provider/crypto_derivatives.py` 加纯抓取+聚合 `fetch_perp_market_snapshot`（复用 `fetch_perp_metrics`）；新 `CryptoDerivativesReviewService.collect()` 门控+读篮子；`market_analyzer` 加 `_get_crypto_perp_sentiment` 收集 + `_get_crypto_perp_sentiment_prompt_block` 渲染，接入 `generate_market_review`/`_build_review_prompt`/`_run_daily_review_parts`/`build_market_review_payload`；Web 加 `PerpSentiment` 类型 + `MarketReviewReportView` 卡片。复用 `crypto_derivatives_enabled`，无新配置。

**Tech Stack:** Python（pytest，ThreadPoolExecutor），TypeScript（React/Vitest/@testing-library/react/camelcase-keys）。

**测试运行约定：** Python：`PYTHONPATH="$PWD" .venv/bin/python -m pytest ...`。Web：工作区路径含空格，npm 在 `/tmp/dsaweb` 无空格副本运行（已建好，含完整 node_modules）；改动后先 `rsync -a --exclude node_modules --exclude dist "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/src/" /tmp/dsaweb/src/` 再在 `/tmp/dsaweb` 跑 `npx vitest run <test>` / `npm run lint` / `npm run build`；vitest 全绿 ≠ web-gate，最终须真跑 `npm run lint`。

**关键事实（已读码核实）：**
- 既有抓取 `data_provider/crypto_derivatives.py::fetch_perp_metrics(base, quote)` 返回 presence-only `{funding_rate?, mark_price?, open_interest?, open_interest_usd?, source?}`，非 USDT/USDC → `{}`。
- 篮子 `config.crypto_market_review_symbols`（默认 12 只）；解析范式（`data_provider/base.py:2203`）`[s.strip().upper() for s in raw.split(",") if s.strip()]` + `is_crypto_code` 过滤（`is_crypto_code` 在 `data_provider/base.py`）。
- market_analyzer 范式：`_get_crypto_market_indicators`(:534)、`_get_crypto_indicators_prompt_block`(:483)、`generate_market_review(overview, news, indicators=None)`(:591) 内 `_build_review_prompt(overview, news, indicators)`(:618 调用 / :1166 定义)、prompt 注入点 :1276 与 :1342、`_run_daily_review_parts`(:1491)、`build_market_review_payload(..., market_indicators=None)`(:622, attach :694)。
- 服务范式 `src/services/crypto_market_indicator_service.py`：`collect()` 无参，门控 `*_enabled`→{}。
- 测试构造：`MarketAnalyzer(region="crypto")` 可裸构造（见 `tests/test_crypto_market_indicators_payload.py`/`_prompt.py`）。
- Web：`MarketReviewReportView.tsx` `StructuredMarketData`(:43)、`getStructuredMarketData` 提取(:181-207)、locale zh(:260)/en(:289)、marketIndicators 渲染块(:525-565)；`types/analysis.ts` `MarketReviewPayload`(:184)；`toCamelCase(deep)` 自动 snake→camel。

---

### Task 1: data_provider 聚合 `fetch_perp_market_snapshot`

**Files:**
- Test: `tests/test_crypto_perp_snapshot.py`（新建）
- Modify: `data_provider/crypto_derivatives.py`（在 `fetch_perp_metrics` 之后追加）

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_crypto_perp_snapshot.py`：

```python
# -*- coding: utf-8 -*-
"""crypto 复盘永续情绪聚合：OI 加权资金费率 + 总 OI + top-mover，presence-only。"""
import data_provider.crypto_derivatives as cd


def _fake_metrics(table):
    def _impl(base, quote):
        return table.get(f"{base}/{quote}", {})
    return _impl


def test_aggregates_weighted_funding_and_total_oi(monkeypatch):
    table = {
        "BTC/USDT": {"funding_rate": 0.0001, "open_interest_usd": 1000.0, "source": "okx"},
        "ETH/USDT": {"funding_rate": 0.0003, "open_interest_usd": 3000.0, "source": "okx"},
    }
    monkeypatch.setattr(cd, "fetch_perp_metrics", _fake_metrics(table))
    out = cd.fetch_perp_market_snapshot(["BTC/USDT", "ETH/USDT"])
    # OI 加权：(0.0001*1000 + 0.0003*3000)/(1000+3000) = (0.1+0.9)/4000 = 0.00025
    assert abs(out["avg_funding_rate"] - 0.00025) < 1e-12
    assert out["total_open_interest_usd"] == 4000.0
    assert [c["symbol"] for c in out["coins"]] == ["ETH/USDT", "BTC/USDT"]  # |funding| 降序


def test_top_movers_capped_at_5_and_sorted(monkeypatch):
    table = {f"C{i}/USDT": {"funding_rate": (i + 1) * 0.0001, "open_interest_usd": 100.0} for i in range(7)}
    monkeypatch.setattr(cd, "fetch_perp_metrics", _fake_metrics(table))
    out = cd.fetch_perp_market_snapshot([f"C{i}/USDT" for i in range(7)])
    assert len(out["coins"]) == 5
    assert out["coins"][0]["symbol"] == "C6/USDT"  # 最大 |funding|


def test_presence_only_partial_fields(monkeypatch):
    table = {
        "BTC/USDT": {"funding_rate": 0.0002},                 # 无 oi → 不计入加权/总 OI，但进 coins
        "ETH/USDT": {"open_interest_usd": 5000.0},            # 无 funding → 进总 OI 与 coins
    }
    monkeypatch.setattr(cd, "fetch_perp_metrics", _fake_metrics(table))
    out = cd.fetch_perp_market_snapshot(["BTC/USDT", "ETH/USDT"])
    assert "avg_funding_rate" not in out                       # 无任一币同时有 funding+oi
    assert out["total_open_interest_usd"] == 5000.0
    assert len(out["coins"]) == 2


def test_empty_when_no_data(monkeypatch):
    monkeypatch.setattr(cd, "fetch_perp_metrics", lambda base, quote: {})
    assert cd.fetch_perp_market_snapshot(["BTC/USDT", "ETH/USDT"]) == {}


def test_empty_symbols_returns_empty():
    assert cd.fetch_perp_market_snapshot([]) == {}
```

- [ ] **Step 2: 运行确认失败** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_perp_snapshot.py -q` → FAIL（`fetch_perp_market_snapshot` 未定义）。

- [ ] **Step 3: 实现** — 在 `data_provider/crypto_derivatives.py` 末尾（`fetch_perp_metrics` 之后）追加：

```python
def _perp_row_for_symbol(symbol: str) -> dict:
    """单个 BASE/QUOTE 现货代码 → {symbol, funding_rate?, open_interest_usd?}；无可用字段 → {}。"""
    base, sep, quote = (symbol or "").upper().partition("/")
    if not sep:
        return {}
    metrics = fetch_perp_metrics(base, quote)  # 模块内全局引用，便于测试 monkeypatch
    row: dict = {}
    if metrics.get("funding_rate") is not None:
        row["funding_rate"] = metrics["funding_rate"]
    if metrics.get("open_interest_usd") is not None:
        row["open_interest_usd"] = metrics["open_interest_usd"]
    if row:
        row["symbol"] = symbol
    return row


def fetch_perp_market_snapshot(symbols: list) -> dict:
    """对一篮子现货代码并发取各自 OKX 永续指标，聚合复盘情绪（presence-only）。
    OI 加权平均资金费率 + 总未平仓量(USD) + 按 |funding| 降序 top5 明细。无数据 → {}。"""
    if not symbols:
        return {}
    rows: list = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for row in ex.map(_perp_row_for_symbol, symbols):
            if row:
                rows.append(row)
    if not rows:
        return {}
    out: dict = {}
    weighted_num = 0.0
    weighted_den = 0.0
    total_oi = 0.0
    has_oi = False
    for r in rows:
        fr = r.get("funding_rate")
        oi = r.get("open_interest_usd")
        if oi is not None:
            total_oi += oi
            has_oi = True
            if fr is not None and oi > 0:
                weighted_num += fr * oi
                weighted_den += oi
    if weighted_den > 0:
        out["avg_funding_rate"] = weighted_num / weighted_den
    if has_oi:
        out["total_open_interest_usd"] = total_oi
    coins = sorted(
        rows,
        key=lambda r: abs(r["funding_rate"]) if r.get("funding_rate") is not None else -1.0,
        reverse=True,
    )[:5]
    if coins:
        out["coins"] = coins
    return out
```

（`ThreadPoolExecutor` 已在文件顶部导入；无新 import。`_perp_row_for_symbol` 调用模块全局 `fetch_perp_metrics`，monkeypatch 生效。）

- [ ] **Step 4: 运行确认通过** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_perp_snapshot.py -q` → 5 passed。

- [ ] **Step 5: 提交**
```bash
git add tests/test_crypto_perp_snapshot.py data_provider/crypto_derivatives.py
git commit -m "feat: crypto 永续复盘聚合抓取（OI 加权资金费率/总 OI/top-mover，presence-only）"
```

---

### Task 2: 服务 `CryptoDerivativesReviewService`

**Files:**
- Test: `tests/test_crypto_perp_review_service.py`（新建）
- Create: `src/services/crypto_derivatives_review_service.py`

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_crypto_perp_review_service.py`：

```python
# -*- coding: utf-8 -*-
"""crypto 复盘永续情绪服务：门控 + 读篮子 + 过滤非法代码 + 调聚合。"""
import types

import data_provider.crypto_derivatives as cd
from src.services.crypto_derivatives_review_service import CryptoDerivativesReviewService


def _cfg(enabled=True, symbols="BTC/USDT,ETH/USDT,NOTACOIN"):
    return types.SimpleNamespace(
        crypto_derivatives_enabled=enabled,
        crypto_market_review_symbols=symbols,
    )


def test_disabled_returns_empty():
    out = CryptoDerivativesReviewService(config=_cfg(enabled=False)).collect()
    assert out == {}


def test_collect_parses_basket_filters_invalid_and_aggregates(monkeypatch):
    seen = {}
    def fake_snapshot(symbols):
        seen["symbols"] = symbols
        return {"avg_funding_rate": 0.0002, "total_open_interest_usd": 1000.0, "coins": []}
    monkeypatch.setattr(cd, "fetch_perp_market_snapshot", fake_snapshot)
    out = CryptoDerivativesReviewService(config=_cfg()).collect()
    # NOTACOIN 非 is_crypto_code → 过滤；BTC/USDT、ETH/USDT 保留（大写）
    assert seen["symbols"] == ["BTC/USDT", "ETH/USDT"]
    assert out["avg_funding_rate"] == 0.0002


def test_empty_basket_returns_empty(monkeypatch):
    monkeypatch.setattr(cd, "fetch_perp_market_snapshot", lambda symbols: {})
    out = CryptoDerivativesReviewService(config=_cfg(symbols="")).collect()
    assert out == {}
```

- [ ] **Step 2: 运行确认失败** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_perp_review_service.py -q` → FAIL（模块不存在）。

- [ ] **Step 3: 实现** — 新建 `src/services/crypto_derivatives_review_service.py`：

```python
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
```

- [ ] **Step 4: 运行确认通过** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_perp_review_service.py -q` → 3 passed。

- [ ] **Step 5: 提交**
```bash
git add tests/test_crypto_perp_review_service.py src/services/crypto_derivatives_review_service.py
git commit -m "feat: crypto 复盘永续情绪服务（门控+读篮子+过滤，presence-only）"
```

---

### Task 3: market_analyzer 收集 + prompt 事实块

**Files:**
- Test: `tests/test_crypto_perp_review_prompt.py`（新建）
- Modify: `src/market_analyzer.py`（紧邻 `_get_crypto_indicators_prompt_block`/`_get_crypto_market_indicators` 处新增两方法）

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_crypto_perp_review_prompt.py`：

```python
# -*- coding: utf-8 -*-
"""crypto 复盘永续情绪：收集方法 + prompt 事实块（zh/en，presence-only）。"""
import src.services.crypto_derivatives_review_service as svc_mod
from src.market_analyzer import MarketAnalyzer

PERP = {
    "avg_funding_rate": 0.00025,
    "total_open_interest_usd": 4000000000.0,
    "coins": [
        {"symbol": "ETH/USDT", "funding_rate": 0.0003, "open_interest_usd": 3000000000.0},
        {"symbol": "BTC/USDT", "funding_rate": 0.0001, "open_interest_usd": 1000000000.0},
    ],
}


def test_perp_prompt_block_zh_contains_values():
    a = MarketAnalyzer(region="crypto")
    block = a._get_crypto_perp_sentiment_prompt_block(PERP, "zh")
    assert "永续情绪" in block
    assert "0.0250%" in block                     # 0.00025*100
    assert "ETH/USDT" in block
    assert "不得编造数据" in block


def test_perp_prompt_block_en_contains_values():
    a = MarketAnalyzer(region="crypto")
    block = a._get_crypto_perp_sentiment_prompt_block(PERP, "en")
    assert "Perpetual Sentiment" in block
    assert "0.0250%" in block
    assert "do not invent data" in block


def test_perp_prompt_block_empty_when_no_data():
    a = MarketAnalyzer(region="crypto")
    assert a._get_crypto_perp_sentiment_prompt_block({}, "zh") == ""


def test_perp_prompt_block_empty_for_non_crypto():
    a = MarketAnalyzer(region="cn")
    assert a._get_crypto_perp_sentiment_prompt_block(PERP, "zh") == ""


def test_non_crypto_get_perp_sentiment_empty():
    a = MarketAnalyzer(region="cn")
    assert a._get_crypto_perp_sentiment() == {}


def test_crypto_get_perp_sentiment_uses_service(monkeypatch):
    monkeypatch.setattr(
        svc_mod.CryptoDerivativesReviewService, "collect",
        lambda self: {"avg_funding_rate": 0.0001},
    )
    a = MarketAnalyzer(region="crypto")
    assert a._get_crypto_perp_sentiment() == {"avg_funding_rate": 0.0001}
```

- [ ] **Step 2: 运行确认失败** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_perp_review_prompt.py -q` → FAIL（方法未定义）。

- [ ] **Step 3: 实现** — 在 `src/market_analyzer.py` 中 `_get_crypto_market_indicators`（:534）之后追加两方法：

```python
    def _get_crypto_perp_sentiment(self) -> Dict[str, Any]:
        """crypto 大盘永续情绪聚合；非 crypto 返回 {}，任何失败优雅降级为 {}。"""
        if self.region != "crypto":
            return {}
        try:
            from src.services.crypto_derivatives_review_service import CryptoDerivativesReviewService
            return CryptoDerivativesReviewService().collect()
        except Exception as e:
            logger.warning("[永续情绪] 收集失败，跳过: %s", e)
            return {}

    def _get_crypto_perp_sentiment_prompt_block(self, perp: Optional[Dict[str, Any]], review_language: str | None = None) -> str:
        """crypto 永续情绪事实块（注入 prompt 供 LLM 引用）；非 crypto 或无数据返回空。"""
        if self.region != "crypto" or not perp:
            return ""
        lang = review_language or self._get_review_language()
        avg = perp.get("avg_funding_rate")
        toi = perp.get("total_open_interest_usd")
        coins = perp.get("coins") if isinstance(perp.get("coins"), list) else []
        parts: List[str] = []
        if lang == "en":
            if avg is not None:
                parts.append(f"- OI-weighted funding rate: {avg * 100:.4f}%")
            if toi is not None:
                parts.append(f"- Total open interest: ${toi:,.0f}")
            for c in coins:
                fr = c.get("funding_rate")
                oi = c.get("open_interest_usd")
                fr_txt = f"funding {fr * 100:.4f}%" if fr is not None else "funding n/a"
                oi_txt = f", OI ${oi:,.0f}" if oi is not None else ""
                parts.append(f"  - {c.get('symbol')}: {fr_txt}{oi_txt}")
            if not parts:
                return ""
            return ("\n## Crypto Perpetual Sentiment\n" + "\n".join(parts)
                    + "\n[Crypto] Read leverage sentiment and squeeze risk from funding and open interest; do not invent data.")
        if avg is not None:
            parts.append(f"- OI 加权资金费率：{avg * 100:.4f}%")
        if toi is not None:
            parts.append(f"- 总未平仓量：${toi:,.0f}")
        for c in coins:
            fr = c.get("funding_rate")
            oi = c.get("open_interest_usd")
            fr_txt = f"资金费率 {fr * 100:.4f}%" if fr is not None else "资金费率 N/A"
            oi_txt = f"，OI ${oi:,.0f}" if oi is not None else ""
            parts.append(f"  - {c.get('symbol')}：{fr_txt}{oi_txt}")
        if not parts:
            return ""
        return ("\n## 加密永续情绪\n" + "\n".join(parts)
                + "\n[加密货币专属] 结合资金费率与持仓判断杠杆情绪与挤压风险，不得编造数据。")
```

（`Dict`/`Any`/`Optional`/`List` 已在文件导入；`logger` 已存在。）

- [ ] **Step 4: 运行确认通过** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_perp_review_prompt.py -q` → 6 passed。

- [ ] **Step 5: 提交**
```bash
git add tests/test_crypto_perp_review_prompt.py src/market_analyzer.py
git commit -m "feat: market_analyzer 永续情绪收集 + prompt 事实块（zh/en，presence-only）"
```

---

### Task 4: 接入复盘编排 + payload

**Files:**
- Test: `tests/test_crypto_perp_review_payload.py`（新建）
- Modify: `src/market_analyzer.py`（`_build_review_prompt`/`generate_market_review`/`_run_daily_review_parts`/`build_market_review_payload`）

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_crypto_perp_review_payload.py`：

```python
# -*- coding: utf-8 -*-
"""crypto 复盘永续情绪：payload 透出 + prompt 注入。"""
from src.market_analyzer import MarketAnalyzer, MarketOverview

PERP = {"avg_funding_rate": 0.00025, "total_open_interest_usd": 4000000000.0,
        "coins": [{"symbol": "ETH/USDT", "funding_rate": 0.0003, "open_interest_usd": 3000000000.0}]}


def test_payload_includes_perp_sentiment_when_provided():
    a = MarketAnalyzer(region="crypto")
    ov = MarketOverview(date="2026-06-09")
    payload = a.build_market_review_payload(ov, news=[], report="# 复盘", perp_sentiment=PERP)
    assert payload["perp_sentiment"] == PERP


def test_payload_omits_perp_sentiment_when_empty():
    a = MarketAnalyzer(region="crypto")
    ov = MarketOverview(date="2026-06-09")
    payload = a.build_market_review_payload(ov, news=[], report="# 复盘", perp_sentiment={})
    assert "perp_sentiment" not in payload


def test_review_prompt_includes_perp_block():
    a = MarketAnalyzer(region="crypto")
    ov = MarketOverview(date="2026-06-09")
    prompt = a._build_review_prompt(ov, [], None, PERP)
    assert "加密永续情绪" in prompt
```

- [ ] **Step 2: 运行确认失败** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_perp_review_payload.py -q` → FAIL（`build_market_review_payload`/`_build_review_prompt` 无 `perp_sentiment` 形参）。

- [ ] **Step 3a: `_build_review_prompt` 增形参 + 注入** — 在 `src/market_analyzer.py`：
  - 改签名（:1166）`def _build_review_prompt(self, overview: MarketOverview, news: List, indicators: Optional[Dict[str, Any]] = None) -> str:` →
    `def _build_review_prompt(self, overview: MarketOverview, news: List, indicators: Optional[Dict[str, Any]] = None, perp_sentiment: Optional[Dict[str, Any]] = None) -> str:`
  - 在**两处** prompt 模板中（:1276 与 :1342，紧随 `{self._get_crypto_indicators_prompt_block(indicators, review_language)}` 行之后）各加一行：
    ```
    {self._get_crypto_perp_sentiment_prompt_block(perp_sentiment, review_language)}
    ```

- [ ] **Step 3b: `generate_market_review` 增形参 + 透传** — 改签名（:591）增 `perp_sentiment: Optional[Dict[str, Any]] = None`；把内部 `prompt = self._build_review_prompt(overview, news, indicators)`（:618）改为 `prompt = self._build_review_prompt(overview, news, indicators, perp_sentiment)`。

- [ ] **Step 3c: `build_market_review_payload` 增形参 + attach** — 改签名（:622 区块，`market_indicators` 形参之后）增 `perp_sentiment: Optional[Dict[str, Any]] = None`；在 attach 段（:694 `if market_indicators: payload["market_indicators"]=...` 之后）加：
    ```python
        if perp_sentiment:
            payload["perp_sentiment"] = perp_sentiment
    ```

- [ ] **Step 3d: `_run_daily_review_parts` 收集 + 传递** — 在 `_run_daily_review_parts`（:1491）：
  - `indicators = self._get_crypto_market_indicators()`（:1502）之后加：
    ```python
        perp_sentiment = self._get_crypto_perp_sentiment()
    ```
  - `report = self.generate_market_review(overview, news, indicators)`（:1505）改为 `report = self.generate_market_review(overview, news, indicators, perp_sentiment)`。
  - `build_market_review_payload(...)` 调用（:1510-1517）增 `perp_sentiment=perp_sentiment,`（紧随 `market_indicators=indicators,`）。

- [ ] **Step 4: 运行确认通过** — `PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_perp_review_payload.py tests/test_crypto_perp_review_prompt.py -q` → 9 passed。回归 market_indicators 既有用例：`PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_crypto_market_indicators_payload.py tests/test_crypto_market_indicators_prompt.py -q` → 全过。`PYTHONPATH="$PWD" .venv/bin/python -m py_compile src/market_analyzer.py`。

- [ ] **Step 5: 提交**
```bash
git add tests/test_crypto_perp_review_payload.py src/market_analyzer.py
git commit -m "feat: 复盘编排接入永续情绪（prompt 注入 + payload perp_sentiment）"
```

---

### Task 5: Web 类型 `PerpSentiment`

**Files:** Modify `apps/dsa-web/src/types/analysis.ts`

- [ ] **Step 1: 新增接口** — 在 `MarketIndicators` 接口之后（与其同区）追加：

```typescript
export interface PerpSentimentCoin {
  symbol: string;
  fundingRate?: number;
  openInterestUsd?: number;
}

export interface PerpSentiment {
  avgFundingRate?: number;
  totalOpenInterestUsd?: number;
  coins?: PerpSentimentCoin[];
}
```

- [ ] **Step 2: `MarketReviewPayload` 追加字段** — 在 `MarketReviewPayload` 接口（含 `marketIndicators?: MarketIndicators;` 那行，:203）之后加：

```typescript
  perpSentiment?: PerpSentiment;
```

- [ ] **Step 3: 类型校验** —
```bash
rsync -a --exclude node_modules --exclude dist "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/src/" /tmp/dsaweb/src/
cd /tmp/dsaweb && npx tsc --noEmit
```
Expected: 无错误。

- [ ] **Step 4: 提交**
```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
git add apps/dsa-web/src/types/analysis.ts
git commit -m "feat: Web PerpSentiment 类型 + MarketReviewPayload.perpSentiment"
```

---

### Task 6: Web 渲染 MarketReviewReportView 永续情绪卡片

**Files:**
- Test: `apps/dsa-web/src/components/report/__tests__/MarketReviewReportView.test.tsx`（既有，追加用例）
- Modify: `apps/dsa-web/src/components/report/MarketReviewReportView.tsx`

- [ ] **Step 1: 写失败测试** — 在既有 `apps/dsa-web/src/components/report/__tests__/MarketReviewReportView.test.tsx` 末尾追加（先读该文件顶部，复用其既有 `render`/payload 构造范式；下例为独立 `it`）：

```tsx
  it('renders crypto perp sentiment when present', () => {
    const payload = {
      version: 1, kind: 'market_review', region: 'crypto', title: 'Crypto', generatedAt: '2026-06-09',
      indices: [],
      perpSentiment: {
        avgFundingRate: 0.00025,
        totalOpenInterestUsd: 4000000000,
        coins: [{ symbol: 'ETH/USDT', fundingRate: 0.0003, openInterestUsd: 3000000000 }],
      },
    };
    render(<MarketReviewReportView payload={payload as never} />);
    expect(screen.getByText(/永续情绪|Perpetual Sentiment/)).toBeInTheDocument();
    expect(screen.getByText(/0\.0250%/)).toBeInTheDocument();
    expect(screen.getByText(/ETH\/USDT/)).toBeInTheDocument();
  });
```

> 实现者注：读该测试文件顶部，确认 `MarketReviewReportView` 的 props 名（`payload` 或其它）、import、以及既有用例如何构造 payload；按既有范式对齐（prop 名/render 包装）。若组件 props 与示例不符，以既有用例为准。

- [ ] **Step 2: 运行确认失败** —
```bash
rsync -a --exclude node_modules --exclude dist "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/src/" /tmp/dsaweb/src/
cd /tmp/dsaweb && npx vitest run src/components/report/__tests__/MarketReviewReportView.test.tsx
```
Expected: 新用例 FAIL（无永续情绪渲染）。

- [ ] **Step 3a: `StructuredMarketData` 增字段 + 提取** — 在 `MarketReviewReportView.tsx`：
  - import 段（:7 区，含 `MarketIndicators,`）追加 `PerpSentiment,`（来自 `'../../types/analysis'`）。
  - `type StructuredMarketData`（:43）在 `marketIndicators?: MarketIndicators;`（:50）后加 `perpSentiment?: PerpSentiment;`。
  - `getStructuredMarketData` 的**两处**返回对象（:184-192 的 markets 分支、:199-207 的单 payload 分支）各在 `marketIndicators: ...,` 后加 `perpSentiment: marketPayload.perpSentiment,`（markets 分支）/ `perpSentiment: payload.perpSentiment,`（单分支）。

- [ ] **Step 3b: locale 文案** — 在 zh `marketReviewText`（:260 区，含 `marketIndicators: '加密市场指标',`）加：
  ```typescript
    perpSentiment: '加密永续情绪',
    avgFundingRate: 'OI 加权资金费率',
    totalOpenInterest: '总未平仓量',
  ```
  在 en 区（:289 区）加：
  ```typescript
    perpSentiment: 'Perpetual Sentiment',
    avgFundingRate: 'OI-weighted Funding',
    totalOpenInterest: 'Total Open Interest',
  ```
  并在该 text 的类型声明（:231 区，含 `marketIndicators: string;` 等）加：
  ```typescript
    perpSentiment: string;
    avgFundingRate: string;
    totalOpenInterest: string;
  ```

- [ ] **Step 3c: 渲染块** — 在 `marketIndicators` 渲染块（:525-565）之后插入永续情绪块（presence-only）：

```tsx
                {marketData.region === 'crypto' && marketData.perpSentiment &&
                  (marketData.perpSentiment.avgFundingRate !== undefined ||
                   marketData.perpSentiment.totalOpenInterestUsd !== undefined ||
                   (marketData.perpSentiment.coins && marketData.perpSentiment.coins.length > 0)) ? (
                  <div>
                    <h4 className="mb-2 text-sm font-semibold text-foreground">{marketReviewText.perpSentiment}</h4>
                    <div className="grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
                      {marketData.perpSentiment.avgFundingRate !== undefined ? (
                        <div className="rounded-lg border border-subtle p-3">
                          <p className="label-uppercase">{marketReviewText.avgFundingRate}</p>
                          <p className="mt-1 font-semibold text-foreground">{(marketData.perpSentiment.avgFundingRate * 100).toFixed(4)}%</p>
                        </div>
                      ) : null}
                      {marketData.perpSentiment.totalOpenInterestUsd !== undefined ? (
                        <div className="rounded-lg border border-subtle p-3">
                          <p className="label-uppercase">{marketReviewText.totalOpenInterest}</p>
                          <p className="mt-1 font-semibold text-foreground">${marketData.perpSentiment.totalOpenInterestUsd.toLocaleString()}</p>
                        </div>
                      ) : null}
                    </div>
                    {marketData.perpSentiment.coins && marketData.perpSentiment.coins.length > 0 ? (
                      <ul className="mt-2 space-y-1 text-sm text-secondary-text">
                        {marketData.perpSentiment.coins.map((c) => (
                          <li key={c.symbol}>
                            {c.symbol}
                            {c.fundingRate !== undefined ? ` · ${(c.fundingRate * 100).toFixed(4)}%` : ''}
                            {c.openInterestUsd !== undefined ? ` · $${c.openInterestUsd.toLocaleString()}` : ''}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                ) : null}
```

- [ ] **Step 4: 验证** —
```bash
rsync -a --exclude node_modules --exclude dist "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/src/" /tmp/dsaweb/src/
cd /tmp/dsaweb && npx vitest run src/components/report/__tests__/MarketReviewReportView.test.tsx && npx tsc --noEmit && npx eslint src/components/report/MarketReviewReportView.tsx
```
Expected: 测试全过；tsc clean；eslint clean。

- [ ] **Step 5: 提交**
```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
git add apps/dsa-web/src/components/report/MarketReviewReportView.tsx apps/dsa-web/src/components/report/__tests__/MarketReviewReportView.test.tsx
git commit -m "feat: 复盘 Web 永续情绪卡片（presence-only，crypto 区）"
```

---

### Task 7: 文档

**Files:** Modify `docs/crypto-guide.md`、`docs/CHANGELOG.md`

- [ ] **Step 1: crypto-guide 复盘节追加** — 在 `docs/crypto-guide.md` 大盘复盘相关节追加小节：

```markdown
### 永续情绪聚合（复盘）

`MARKET_REVIEW_REGION=crypto` 复盘时，对 `CRYPTO_MARKET_REVIEW_SYMBOLS` 篮子并发拉取各币 OKX 永续指标并聚合（presence-only）：

- OI 加权平均资金费率、总未平仓量(USD)、按 |funding| 降序 top5 明细。
- 注入复盘 prompt + 结构化 `market_review_payload.perp_sentiment` + Web 复盘视图「加密永续情绪」卡片。
- 门控复用 `CRYPTO_DERIVATIVES_ENABLED`（默认开）；非 crypto/禁用/无数据 → 不聚合，复盘照常。
```

- [ ] **Step 2: CHANGELOG 扁平条目** — 在 `docs/CHANGELOG.md` `[Unreleased]` 段末尾加一行（禁止 `###` 类目）：

```markdown
- [新功能] crypto 大盘复盘聚合永续情绪（OI 加权资金费率/总未平仓量/top-mover；复用篮子与 `CRYPTO_DERIVATIVES_ENABLED`，presence-only，默认开）
```

- [ ] **Step 3: 核对** — `grep -n "永续情绪\|perp_sentiment\|perpSentiment" docs/crypto-guide.md docs/CHANGELOG.md` → 命中；字段/开关名与代码一致。

- [ ] **Step 4: 提交**
```bash
git add docs/crypto-guide.md docs/CHANGELOG.md
git commit -m "docs: crypto 复盘永续情绪聚合说明 + CHANGELOG"
```

---

### Task 8: 全量回归 + 收尾

**Files:** 无（验证）

- [ ] **Step 1: 后端 ci_gate** — `PATH="$PWD/.venv/bin:$PATH" PYTHONPATH="$PWD" ./scripts/ci_gate.sh all` → `backend-gate: all checks passed`，0 失败。
- [ ] **Step 2: 前端 lint+build** — `/tmp/dsaweb` 同步后 `npm run lint && npm run build` → eslint 0 error、build 成功。
- [ ] **Step 3: 自检** — presence-only（缺省不渲染/payload 无键）；cn/hk/us 复盘零变化；无新配置；中英双语；篮子复用；文档与代码一致。
- [ ] **Step 4: 收尾** — 调 `superpowers:finishing-a-development-branch` 呈现选项（默认保持本地）。

---

## Self-Review

**1. Spec coverage：** §2.1 聚合→T1；§2.2 服务→T2；§2.3 收集+prompt block→T3，编排接入→T4；§2.4 payload→T4；§2.5 Web 类型→T5、渲染→T6；§2.6 复用开关（无配置改动）→贯穿；§5 测试→T1/2/3/4/6；§6 文档→T7；§7 回滚→T8。全覆盖。

**2. Placeholder scan：** 无 TBD/TODO；每步含完整代码与确切命令/预期。Web 测试/locale 形状标注"以既有用例为准"实现者注，但给出了完整示例代码。

**3. Type consistency：** Python snake：`avg_funding_rate`/`total_open_interest_usd`/`coins`/`symbol`/`funding_rate`/`open_interest_usd`，贯穿聚合↔service↔prompt block↔payload↔测试；Web camel：`avgFundingRate`/`totalOpenInterestUsd`/`coins`/`symbol`/`fundingRate`/`openInterestUsd`，经 `toCamelCase(deep)` 对应。方法名 `_get_crypto_perp_sentiment` / `_get_crypto_perp_sentiment_prompt_block` / `fetch_perp_market_snapshot` / `CryptoDerivativesReviewService.collect` 前后一致。`_build_review_prompt(overview, news, indicators, perp_sentiment)` 形参顺序在定义/调用/测试一致。
