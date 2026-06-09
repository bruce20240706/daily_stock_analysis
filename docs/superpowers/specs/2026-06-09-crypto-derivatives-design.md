# crypto 永续合约指标 设计（Phase 1：注入分析）

> 阶段二「合约/永续维度」的**首个子项目**。完整 perpetuals 能力太大（独立 perp 符号、perp klines、perp 回测、复盘、全链 schema/API/Web），经子系统映射后拆分；本 Phase 1 只做最小、additive 的一步：为 crypto **现货**分析拉取对应**永续**的资金费率/标记价/未平仓量，注入分析 prompt。

**Goal:** 分析一个 crypto 现货标的（如 `BTC/USDT`）时，并发拉取其对应 OKX 永续（`BTC-USDT-SWAP`）的资金费率、标记价、未平仓量（免费无 key、presence-only），注入 LLM 分析 prompt 的「合约市场指标」块，填补 `market_context` 已提示「关注资金费率」却无数据的缺口。默认开、失败优雅降级。

**范围决策（已确认）：** 仅注入分析 prompt；**不**动符号识别、`UnifiedRealtimeQuote`/API/Web schema、perp klines/回测、复盘；OKX-only（Binance fapi 本环境 451 且无 futures 镜像，作为后续子项目的可选 fallback，本期不做）；默认开 + 并发拉取。

---

## 1. 背景与查证

子系统映射 + 直接读码确认：

- crypto 当前仅现货；`UnifiedRealtimeQuote`（`data_provider/realtime_types.py:108-157`）无 funding/mark/OI 字段。
- `src/market_context.py`（crypto guidelines ~103-118）**已在 prompt 中提示「关注资金费率、杠杆风险」，但从不提供真实数据** —— 本特性填补该数据缺口。
- 每股分析的 `context` dict 来自 `storage.get_analysis_context()`，在 `src/core/pipeline.py:572` 调 `GeminiAnalyzer.analyze(context, ...)`；`GeminiAnalyzer._format_prompt`（`src/analyzer.py:2892`）渲染 prompt，实时行情块在 ~2987-3001 —— 这是注入「合约市场指标」块的自然位置。
- **数据源实测（2026-06-09，免费无 key）：**
  - OKX 全可达：`/api/v5/public/funding-rate?instId=BTC-USDT-SWAP` → `fundingRate` + `nextFundingTime`（无 markPx）；`/api/v5/public/mark-price?instType=SWAP&instId=...` → `markPx`；`/api/v5/public/open-interest?instId=...` → `oi` + `oiCcy` + `oiUsd`。
  - Binance `fapi.binance.com` → **HTTP 451**（地区受限，与现货 `api.binance.com` 同；无 futures 镜像）→ 本期不纳入。
  - 符号映射：现货 `BASE/QUOTE` → OKX 永续 `BASE-QUOTE-SWAP`；仅 USDT/USDC 线性永续，其它 quote → `{}`。

## 2. 架构与组件

分层纪律：`data_provider/` 纯抓取（不 import src.*、不碰 DB）；门控/编排在 src。

### 2.1 `data_provider/crypto_derivatives.py`（新，纯抓取）

```python
OKX_FUNDING_URL = "https://www.okx.com/api/v5/public/funding-rate"
OKX_MARK_URL = "https://www.okx.com/api/v5/public/mark-price"
OKX_OI_URL = "https://www.okx.com/api/v5/public/open-interest"
_LINEAR_QUOTES = {"USDT", "USDC"}   # OKX 线性永续计价

def fetch_perp_metrics(base: str, quote: str) -> dict:
    """spot BASE/QUOTE → OKX 永续 BASE-QUOTE-SWAP；并发拉 3 个公共接口；presence-only；失败/不支持 → {}。"""
    # 返回（均 presence-only）：funding_rate(float), next_funding_time(int ms),
    #   mark_price(float), open_interest(float), open_interest_usd(float), source="okx"
```

- 仅当 `quote ∈ _LINEAR_QUOTES` 才构造 `instId = f"{base}-{quote}-SWAP"`；否则返回 `{}`。
- **并发**：用 `concurrent.futures.ThreadPoolExecutor(max_workers=3)` 同时发起 funding-rate / mark-price / open-interest 三路；单路异常或非 dict → 跳过对应字段（不抛）。三路全空 → `{}`。
- 本地 `_http_get_json` + `_fetch_timeout/_fetch_max_retries`（复用 `CRYPTO_FETCH_TIMEOUT_SECONDS`/`CRYPTO_FETCH_MAX_RETRIES`，4xx 不重试），遵循「零跨层依赖、就地复制」约定。
- 数值用 `float()` 包裹解析、整数时间戳用 `int()`，失败跳字段（presence-only）。

### 2.2 `src/services/crypto_derivatives_service.py`（新，编排）

```python
class CryptoDerivativesService:
    def __init__(self, config=None): ...
    def collect(self, code: str) -> dict:
        # 1. disabled（crypto_derivatives_enabled=False）→ {}
        # 2. 非 crypto（not is_crypto_code）→ {}
        # 3. 解析 base/quote，调 crypto_derivatives.fetch_perp_metrics（经模块引用，便于 mock）→ presence-only dict
```

通过模块引用调 `data_provider.crypto_derivatives`，便于测试 monkeypatch。

### 2.3 `src/core/pipeline.py`（注入点，~572 analyze 前）

在调用 `self.analyzer.analyze(context, ...)` 之前，对 crypto 标的补充 context：

```python
if is_crypto_code(code) and getattr(config, "crypto_derivatives_enabled", True):
    try:
        metrics = CryptoDerivativesService(config=config).collect(code)
        if metrics:
            context["crypto_contracts"] = metrics
    except Exception as e:
        logger.warning("[合约指标] 收集失败，跳过: %s", e)
```

region/开关门控 + try/except 隔离：任何失败不影响分析主流程。

### 2.4 `src/analyzer.py::_format_prompt`（渲染）

`context.get("crypto_contracts")` 存在时，在实时行情块（~3001）之后插入 presence-only 块（与周边 ZH prompt 脚手架一致）：

```
### 合约市场指标（永续，来源 OKX）
| 指标 | 数值 | 含义 |
|------|------|------|
| 资金费率 | {funding_rate*100:.4f}% | 正=多头付费 / 负=空头付费（约 8h 结算） |
| 标记价 | {mark_price} | 永续标记价（与现货价对比看基差） |
| 未平仓量(OI) | {open_interest} 张 / ${open_interest_usd:,.0f} | 持仓规模与杠杆活跃度 |
[加密货币专属] 结合资金费率与持仓判断杠杆情绪与挤压风险，不得编造数据。
```

逐字段 presence-only（仅渲染存在的行）；无任何字段则整块不出现。

### 2.5 配置

- `src/config.py`：`crypto_derivatives_enabled: bool = True`（env `CRYPTO_DERIVATIVES_ENABLED`，沿用 bool 解析）。
- `src/core/config_registry.py`：新增条目，`help_key = "settings.data_source.crypto_derivatives"`。
- `apps/dsa-web/src/locales/settingsHelp.ts`：新增 `settings.data_source.crypto_derivatives`（zh + en）——**双向 help-key 契约**（registry ↔ locale 必须一致，否则 `test_config_registry.py` 回归）。
- 复用 `CRYPTO_FETCH_*`；`.env.example` 新增 `CRYPTO_DERIVATIVES_ENABLED=true`。

## 3. 数据流

```
pipeline 取 code → is_crypto_code 且 enabled
  → CryptoDerivativesService.collect(code)
    → fetch_perp_metrics(base, quote)（ThreadPoolExecutor 并发 3 个 OKX 接口）
  → context["crypto_contracts"] = {...}（presence-only）
  → analyzer.analyze(context) → _format_prompt 渲染「合约市场指标」块 → LLM 引用
```

## 4. 错误处理（全程降级，不拖垮分析）

- 并发单路（funding/mark/oi）失败 → 跳该字段；三路全失败 → `{}`。
- `disabled` / 非 crypto / `collect` 抛异常 → 无 `crypto_contracts`、prompt 无块、分析照常。
- 4xx 不重试；超时/重试复用 `CRYPTO_FETCH_*`。pipeline 注入点 try/except 隔离。
- presence-only：缺字段省略，不塞 0、不编造。cn/hk/us 与 crypto 现货分析其余部分零影响。

## 5. 测试（离线，无网络、无 LLM）

| 层 | 用例 |
|---|---|
| data_provider | mock 三接口 → 合并解析、presence-only、非线性 quote→{}、单路失败跳字段、全失败→{}、并发不抛 |
| service | `collect` 合并 presence-only、`disabled→{}`、非 crypto→{} |
| analyzer 渲染 | `_format_prompt` 给含 `crypto_contracts` 的 context → prompt 含「合约市场指标」+ funding/mark/OI 值；无则不含 |
| config | `crypto_derivatives_enabled` 默认/env + 双向 help-key 契约（`test_config_registry.py`） |
| pipeline 注入 | 聚焦测试或集成覆盖：crypto 标的注入 `context["crypto_contracts"]`，非 crypto 不注入 |

真实在线可达性走 `network-smoke`（OKX 三接口已手测 200）。

## 6. 分支与回滚

- 分支：`feat/crypto-derivatives`，叠在 `feat/crypto-backtest` 之上；栈：`main ← market-review ← new-listings ← market-indicators ← backtest ← derivatives`。
- 回滚：改动为新增文件 + 1 个注入点 + 1 个 prompt 渲染块 + 1 配置项；`git revert`/丢弃分支即恢复；`CRYPTO_DERIVATIVES_ENABLED=false` 运行时关闭。

## 7. 范围边界（YAGNI / 留后续子项目）

不做：Binance fapi fallback（本环境 451、无镜像）、独立 perp 符号识别（`is_crypto_code` 不变）、`UnifiedRealtimeQuote`/API/Web schema 字段、perp klines、perp 回测、复盘永续、做空/杠杆建模。Phase 1 仅把永续情绪指标注入 crypto 分析 prompt。
