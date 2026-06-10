# crypto 复盘永续情绪聚合 设计（子项目 B）

> 阶段二「合约/永续维度」子项目 **B**。在加密**大盘复盘**（market-review）中，对配置篮子的主流币聚合永续情绪（OI 加权资金费率、总未平仓量、top-mover 明细），注入复盘 prompt + 结构化 payload + Web。完全镜像已落地的 `market_indicators` 三层范式。独立于子项目 A（A 是单标的分析报告透出；B 是大盘复盘聚合）。

**Goal:** `MARKET_REVIEW_REGION=crypto` 复盘时，对 `crypto_market_review_symbols`（默认 12 只）并发拉取各自 OKX 永续指标，聚合为 OI 加权平均资金费率、总未平仓量(USD)、按 |funding| 排序的 top-mover 明细，presence-only 注入复盘 prompt、`market_review_payload` 与 Web 复盘视图。默认开（复用 `crypto_derivatives_enabled`）、失败优雅降级。

**范围决策（已确认）：**
- 篮子：**复用** `crypto_market_review_symbols`（12 只），不新增篮子配置。
- 指标：**OI 加权平均资金费率 + 总未平仓量(USD) + top 5 per-coin 明细**；**不做** long/short ratio（YAGNI、口径不一）。
- 门控：**复用 `crypto_derivatives_enabled`**（单股注入与复盘聚合同属"永续合约指标"，一个主开关；避免叠加开关与新双向 help-key 契约）。region=crypto 为天然门控。
- 仅大盘复盘聚合；**不**动子项目 A 的单标的注入链路。

---

## 1. 背景与查证（已读码核实，2026-06-10）

- 大盘复盘 crypto 范式（`src/market_analyzer.py`，region 感知）：
  - 收集：`_get_crypto_market_indicators()`（:534，region!=crypto→{}，try/except 降级）调 `CryptoMarketIndicatorService().collect()`。
  - prompt 事实块：`_get_crypto_indicators_prompt_block(indicators, lang)`（:483，region/空守卫，zh/en，presence-only，末尾 `[加密货币专属]…不得编造数据`）。
  - 编排：`_run_daily_review_parts()`（:1491）在 `generate_market_review` 前收集 `indicators`，注入 prompt（:1276/:1342）并传入 `build_market_review_payload(..., market_indicators=indicators)`（:1510-1516）。
  - payload：`build_market_review_payload(..., market_indicators=None)`（:622）→ `if market_indicators: payload["market_indicators"]=market_indicators`（:694）。
- 服务范式：`src/services/crypto_market_indicator_service.py` —— `collect()` 无参，门控 `*_enabled` → {}，调 data_provider 纯抓取源，presence-only 合并。
- 篮子来源：`config.crypto_market_review_symbols`（`config.py:930`，env `CRYPTO_MARKET_REVIEW_SYMBOLS`）；既有解析（`data_provider/base.py:2203`）：`[s.strip().upper() for s in raw.split(",") if s.strip()]` + `is_crypto_code` 过滤。
- 既有永续抓取：`data_provider/crypto_derivatives.py::fetch_perp_metrics(base, quote)`（`ThreadPoolExecutor` 并发 3 路 OKX，presence-only，仅 USDT/USDC 线性永续 → 否则 {}）。本期复用之。
- Web：`MarketReviewReportView.tsx` 已渲染 `marketIndicators`（crypto 区守卫，~525-565）；`types/analysis.ts` `MarketReviewPayload`（:184）含 `marketIndicators?: MarketIndicators`。`apps/dsa-web/src/api/analysis.ts` 经 `toCamelCase(deep)` 自动 snake→camel。

## 2. 架构与组件（全 additive，镜像 market_indicators）

分层：`data_provider/` 纯抓取+纯计算（不 import src.*、不碰 DB）；门控/篮子读取在 src 服务层；编排在 market_analyzer；展示在 Web。

### 2.1 `data_provider/crypto_derivatives.py`（扩展，纯抓取+纯聚合）

```python
def fetch_perp_market_snapshot(symbols: list) -> dict:
    """对一篮子 BASE/QUOTE 现货代码并发取各自 OKX 永续指标，聚合为复盘情绪。
    presence-only；无可用数据 → {}。"""
```

- 对每个 `symbol`（`is_crypto_code` 已由调用方过滤）拆 `base/quote`，并发调既有 `fetch_perp_metrics(base, quote)`（`ThreadPoolExecutor`，`max_workers` 限并发，如 8）。仅用其 `funding_rate` 与 `open_interest_usd`（mark_price/open_interest 忽略——复用既有抓取，DRY；额外 mark 调用在离线批处理可接受）。
- per-coin：`{symbol, funding_rate?, open_interest_usd?}`，仅保留 ≥1 字段者。
- 聚合（纯计算，presence-only）：
  - `avg_funding_rate`：对**同时**有 `funding_rate` 且 `open_interest_usd>0` 的币做 **OI 加权**平均 `Σ(fr_i·oi_i)/Σ(oi_i)`；无合格币 → 省略。
  - `total_open_interest_usd`：`Σ open_interest_usd`（有该字段者）；无 → 省略。
  - `coins`：按 `|funding_rate|` 降序（无 funding 的排末）取 **top 5**；无 → 省略。
- 全部缺失 → `{}`。

### 2.2 `src/services/crypto_derivatives_review_service.py`（新，编排/门控）

```python
class CryptoDerivativesReviewService:
    def __init__(self, config=None): ...
    def collect(self) -> dict:
        # 1. disabled（crypto_derivatives_enabled=False）→ {}
        # 2. 读 config.crypto_market_review_symbols → 解析 [s.strip().upper() ...] + is_crypto_code 过滤
        # 3. cd.fetch_perp_market_snapshot(symbols)（经模块引用，便于 mock）→ presence-only dict
```

镜像 `CryptoMarketIndicatorService`（`collect()` 无参、读 config 门控）。经模块引用 `import data_provider.crypto_derivatives as cd` 调用，便于测试 monkeypatch。`is_crypto_code` 从 `data_provider.base` 导入。

### 2.3 `src/market_analyzer.py`（mirror indicators 钩子）

- `_get_crypto_perp_sentiment() -> Dict[str, Any]`（mirror `_get_crypto_market_indicators`:534）：`region!="crypto"→{}`；try/except 降级 `{}`；调 `CryptoDerivativesReviewService().collect()`。
- `_get_crypto_perp_sentiment_prompt_block(perp, review_language=None) -> str`（mirror `_get_crypto_indicators_prompt_block`:483）：`region!="crypto" or not perp → ""`；zh/en 双语：
  - 加权资金费率 `{avg_funding_rate*100:.4f}%`（正=多头付费/负=空头付费）
  - 总未平仓量 `${total_open_interest_usd:,.0f}`
  - top-mover：逐币 `SYMBOL 资金费率 X% / OI $Y`（presence-only）
  - 末尾 `[加密货币专属] 结合资金费率与持仓判断杠杆情绪与挤压风险，不得编造数据。`（en 对应）
  - 任一聚合字段都没有 → 返回 `""`。
- `_run_daily_review_parts()`（:1491）：在 `generate_market_review` 前 `perp = self._get_crypto_perp_sentiment()`；注入 prompt（与 indicators 块同位，:1276/:1342）；`build_market_review_payload(..., perp_sentiment=perp)`。
- `generate_market_review` / `_build_review_prompt` 增形参 `perp_sentiment`（与 `indicators` 并列透传），在两处 prompt 模板插入 `_get_crypto_perp_sentiment_prompt_block(...)`。

### 2.4 `src/market_analyzer.py::build_market_review_payload`（:622）

新增形参 `perp_sentiment: Optional[Dict[str, Any]] = None`；`if perp_sentiment: payload["perp_sentiment"] = perp_sentiment`（presence-only，紧邻 `market_indicators` 处）。

### 2.5 Web

- `apps/dsa-web/src/types/analysis.ts`：
  ```typescript
  export interface PerpSentimentCoin { symbol: string; fundingRate?: number; openInterestUsd?: number; }
  export interface PerpSentiment {
    avgFundingRate?: number;
    totalOpenInterestUsd?: number;
    coins?: PerpSentimentCoin[];
  }
  // MarketReviewPayload 追加：perpSentiment?: PerpSentiment;
  ```
- `MarketReviewReportView.tsx`：crypto 区新增「永续情绪」卡片（mirror `marketIndicators` 渲染块 ~525-565）：加权资金费率(%)、总 OI、top-mover 列表；presence-only（逐字段/整块缺省不渲染）。

### 2.6 配置

复用 `crypto_derivatives_enabled`，**无新配置项**、无 `.env.example`/registry/locale 改动。

## 3. 数据流

```
复盘(region=crypto) → _run_daily_review_parts
  → _get_crypto_perp_sentiment → CryptoDerivativesReviewService.collect()
     → 读 config.crypto_market_review_symbols（解析+is_crypto_code 过滤）
     → cd.fetch_perp_market_snapshot(symbols)（并发 fetch_perp_metrics/币 → 聚合）
  → 注入复盘 prompt（_get_crypto_perp_sentiment_prompt_block）
  → build_market_review_payload(..., perp_sentiment) → payload["perp_sentiment"]
  → Web toCamelCase(deep) → perpSentiment → MarketReviewReportView 卡片
```

## 4. 错误处理 / 兼容

- presence-only 全程：单币失败（`fetch_perp_metrics`→{}）跳过该币；全篮子无数据 → `{}` → 无 payload/prompt 块。
- `disabled` / 非 crypto / 异常 → 无 `perp_sentiment`，复盘照常（cn/hk/us 与无数据 crypto 复盘零影响）。
- payload 追加可选字段，对既有复盘消费者（Web/历史/推送）向后兼容。
- 复用 `CRYPTO_FETCH_*` 超时/重试；`ThreadPoolExecutor` 限并发，避免 12 币×3 路打爆。

## 5. 测试（离线，无网络、无 LLM）

| 层 | 用例 |
|---|---|
| data_provider 聚合 | mock `fetch_perp_metrics`：多币 → OI 加权均值正确、总 OI 求和、coins 按 |funding| 降序 top5、presence-only（部分币缺字段）、单币 {} 跳过、全空→{} |
| service | `collect`：disabled→{}；读篮子并过滤非法代码；调聚合（monkeypatch cd）→ presence-only |
| prompt block | `_get_crypto_perp_sentiment_prompt_block`：zh/en 含加权费率/总 OI/top-mover；非 crypto/空→""；presence-only |
| payload | `build_market_review_payload(..., perp_sentiment=...)` → `payload["perp_sentiment"]`；缺省不含键 |
| Web | vitest：`perpSentiment` 齐全→渲染；逐字段/整块缺省不渲染；非 crypto 区不渲染 |

真实在线可达性走 `network-smoke`（OKX 永续接口已手测）。

## 6. 文档

- `docs/crypto-guide.md` 复盘节补「永续情绪聚合」小节（聚合口径、篮子、门控）。
- `docs/CHANGELOG.md` `[Unreleased]` 扁平：`- [新功能] crypto 大盘复盘聚合永续情绪（OI 加权资金费率/总未平仓量/top-mover；复用篮子与 crypto_derivatives_enabled，presence-only，默认开）`。

## 7. 分支与回滚

- 分支：`feat/crypto-perp-review`，叠在 `feat/crypto-contracts-report` 上。
- 回滚：纯新增（1 抓取聚合函数 + 1 服务 + market_analyzer 钩子/payload 形参 + Web 类型/卡片）；`git revert`/丢弃分支即恢复；运行时 `CRYPTO_DERIVATIVES_ENABLED=false` 即停。

## 8. 范围边界（YAGNI / 留后续子项目）

不做：long/short ratio、独立篮子配置、单独 review 开关、单标的注入链路改动（子项目 A 已完成）、perp 符号/klines/回测（子项目 C/D/E）。本期仅把篮子永续情绪聚合 presence-only 注入大盘复盘 prompt/payload/Web。
