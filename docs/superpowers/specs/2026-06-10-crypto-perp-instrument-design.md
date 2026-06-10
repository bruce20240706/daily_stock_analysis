# crypto 一等公民永续合约标的 设计（子项目 C+D 合并）

> 阶段二「合约/永续维度」子项目 **C（独立 perp 符号）+ D（perp klines）合并**。让用户直接分析一个 OKX **永续合约**标的（`BTC/USDT:PERP`），走完整日线分析链：符号识别 → 路由 → perp K 线抓取 → 技术分析 + 永续指标 → 报告/Web。C 单独无价值（会用 spot 日线分析 perp，与子项目 A 重叠），故与 D 合并：perp 符号映射到 **真实 perp OHLCV**。perp 回测留给子项目 E。

**Goal:** `python main.py --stocks BTC/USDT:PERP` 时，系统识别其为 crypto 永续标的（24/7、crypto 指南），从 OKX SWAP 拉取**永续日线**（`/market/candles?instId=BTC-USDT-SWAP`）做技术分析，并复用子项目 A 的 funding/mark/OI 注入，产出与现货一致结构的分析报告。永续日线以独立 code 串入库（与现货不撞），失败优雅降级。

**范围决策（已确认）：**
- Notation：`BASE/QUOTE:PERP`（ccxt 风格，如 `BTC/USDT:PERP`）。保留 `BASE/QUOTE` 便于解析；与 spot `BTC/USDT` 天然区分。
- 仅 **OKX 永续、仅 USDT/USDC 线性、仅日线分析链**。
- 复用 `crypto_derivatives_enabled`（derivatives 注入）；perp 取数无新开关（有 perp code 即走 perp 取数）。
- **不**做：perp 回测（子项目 E）、Binance fapi 永续（本环境 451）、多交易所 perp、`UnifiedRealtimeQuote` 加 funding 字段、`OKX_PERPETUAL_PRIORITY` 配置（单一 perp 源，YAGNI）。

---

## 1. 背景与查证（已读码核实，2026-06-10）

- 分类：`is_crypto_code`（`data_provider/base.py:46-58`）—— 含且仅含一个 `/`、BASE 非空、QUOTE ∈ `SUPPORTED_QUOTES={USDT,USDC,USD,BUSD,BTC,ETH}`。**spot 取数器靠它拒绝非现货**（见下），故**不可**直接扩展它识别 perp。
- spot 取数器范式：`OkxFetcher(CryptoExchangeBase)`（`data_provider/okx_fetcher.py`）—— `_to_exchange_symbol`(`BTC/USDT→BTC-USDT`)、`_request_klines`(`/api/v5/market/candles?instId=…&bar=1D`)、`_parse_klines`(`[ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]`)、`_request_ticker`(`/market/ticker`)、`_parse_ticker`。
- `CryptoExchangeBase._fetch_raw_data`（`crypto_base.py:97-99`）与 `get_realtime_quote`（`:132-134`）**均 `if not is_crypto_code(code): reject`** —— 需放宽为 `is_crypto_like` 才能让 perp 子类通过。`_normalize_data`（`:116-130`）产出 `[code,date(YYYY-MM-DD),open,high,low,close,volume,amount,pct_chg]`，与现货统一。
- 日线路由：`DataFetcherManager.get_daily_data`（`base.py:1200-1219`）按 `is_crypto_code` → `_filter_daily_fetchers_for_market(fetchers,"crypto")`；`_DAILY_MARKET_FETCHER_SUPPORT`（`base.py:591`）映射 fetcher→市场集合。**关键：`_filter_daily_fetchers_for_market`（`:726`）有 guard `if market not in {"cn","hk","us","crypto"}: return fetchers`（直接原样返回、不过滤）** —— 新增 `crypto_perp` 标签必须同时把它加进该 guard 集，否则 perp 会「全源乱试」。
- 实时路由：`get_realtime_quote`（`base.py:1638`）**不是**市场支持集过滤，而是 `if is_crypto_code: crypto 优先级 else A股优先级` + 硬编码 source 名分发（`elif source=="okx": _get_fetcher_by_name("OkxFetcher")`）。perp 既不进 crypto 优先级（`is_crypto_code` 为 False），即便进了 `okx` 也只命中 **spot** OkxFetcher（对 perp code 产非法 instId）→ 需专属分支（见 §2.2）。
- 默认 fetcher 池：`_init_default_fetchers`（`:1127`）`crypto_fetchers=[Binance,Okx,Coinbase]` → 并入 `self._fetchers`（`:1150`）；perp fetcher 须加入此列表（既供日线过滤命中，也供实时 `_get_fetcher_by_name` 查到）。
- 市场识别：`get_market_for_stock`（`trading_calendar.py:110-134`）`is_crypto_code → "crypto"`；`is_market_open`/`infer_market_phase` 对 `market=="crypto"` 恒开市、恒 INTRADAY（24/7）。
- normalize：`normalize_stock_code`（`base.py:88-158`）首行 `if "/" in code: return code.upper()` —— perp 含 `/`，**既有分支已正确保留** `BTC/USDT:PERP`，**无需改动**。
- 单位：`_crypto_volume_amount_units`（`analyzer.py:159-172`）按 `/` split 取 (BASE,QUOTE)；对 `BTC/USDT:PERP` 会解析出 QUOTE=`USDT:PERP` → **需 perp-aware**（strip `:PERP`）。
- 语境：`market_context.py:28` 为 `if "/" in code: return "crypto"` —— perp 含 `/`，**已判为 crypto**、crypto 指南自动适用，**无需改动**。
- derivatives 注入（子项目 A）：`attach_crypto_contracts`（`crypto_derivatives_service.py:30-42`）门控 `is_crypto_code + crypto_derivatives_enabled`，`collect(code)` 用 `partition("/")` 取 base/quote。需放宽门控并支持 perp 解析。
- 存储：`StockDaily`（`storage.py`）`UniqueConstraint(code,date)`，code 串原样入库 → perp code `BTC/USDT:PERP` 与 spot `BTC/USDT` 不撞，**无 schema 改动**。
- 既有 crypto 报告/prompt/Web 已 crypto-aware（market_context crypto 指南、价格/单位、crypto_contracts 块）—— perp 被识别为 crypto market 后大部分自动复用。

## 2. 架构与组件（全 additive；perp = crypto-like 新实例类型）

分层：classify/normalize/route 在 `data_provider/base.py`；fetcher 在 `data_provider/`；市场/日历在 `src/core/trading_calendar.py`；单位/语境在 `src/analyzer.py`/`src/market_context.py`；注入门控在 `src/services/`。

### 2.1 分类与解析（`data_provider/base.py`）

```python
_LINEAR_PERP_QUOTES = {"USDT", "USDC"}   # OKX 线性永续计价

def is_perp_code(code: str) -> bool:
    """识别 OKX 永续标的：BASE/QUOTE:PERP（QUOTE ∈ USDT/USDC）。"""
    if not code or not code.strip():
        return False
    s = code.strip().upper()
    if not s.endswith(":PERP") or "/" not in s:
        return False
    base, _, quote = s[:-len(":PERP")].partition("/")
    return bool(base) and quote in _LINEAR_PERP_QUOTES

def parse_perp_code(code: str):
    """BTC/USDT:PERP -> ('BTC','USDT')；非 perp -> (None,None)。"""
    if not is_perp_code(code):
        return None, None
    base, _, quote = code.strip().upper()[:-len(":PERP")].partition("/")
    return base, quote

def is_crypto_like(code: str) -> bool:
    """crypto 现货或永续——用于 crypto 区路由（市场/日历/数据/实时）。"""
    return is_crypto_code(code) or is_perp_code(code)
```

- `is_crypto_code` **保持 spot-only 不变**。
- `normalize_stock_code` **无需改动**：其首行 `if "/" in code: return code.upper()` 已正确保留 perp code（perp 含 `/`）。

### 2.2 路由（disjoint fetcher pools）

- `get_market_for_stock`（trading_calendar）：`if is_crypto_code(code) or is_perp_code(code): return "crypto"`（perp 拿到 24/7 日历 + crypto market_context）。
- `get_daily_data`（base.py:~1215）：
  ```python
  is_perp = is_perp_code(stock_code)
  is_crypto = (not is_perp) and is_crypto_code(stock_code)
  if is_perp:   fetchers = self._filter_daily_fetchers_for_market(fetchers, "crypto_perp")
  elif is_crypto: fetchers = self._filter_daily_fetchers_for_market(fetchers, "crypto")
  ```
  配套两处**缺一不可**：
  1. `_DAILY_MARKET_FETCHER_SUPPORT["OkxPerpetualFetcher"] = {"crypto_perp"}`（spot 三源仍 `{"crypto"}`）。
  2. **`_filter_daily_fetchers_for_market`（`:726`）的 guard 集补 `"crypto_perp"`**：`if market not in {"cn","hk","us","crypto","crypto_perp"}: return fetchers` —— 否则该函数对 `crypto_perp` 直接原样返回全部 fetcher（不过滤），perp 会去试股票/spot 源。
  ⇒ 两池不相交：perp→仅 OkxPerpetualFetcher；spot→仅 Binance/Okx/Coinbase。
- `get_realtime_quote`（base.py:~1638，**硬编码 source 名分发，非市场过滤**）：
  1. 优先级选择改 `if is_crypto_code(code) or is_perp_code(code):` 用 `crypto_realtime_priority`（否则 perp 落到 A 股实时源）。
  2. perp 用专属优先级与分发：`if is_perp_code(code): source_priority = ["okx_perp"]`，并在分发循环加分支 `elif source == "okx_perp": fetcher = self._get_fetcher_by_name("OkxPerpetualFetcher", capability="realtime_quote"); quote = self._call_fetcher_method(fetcher, "get_realtime_quote", stock_code)`。
  （不可复用 `okx` 分支——它命中 spot OkxFetcher，对 perp code 产非法 instId。）实时缺失非致命（`pipeline.py:316` `if realtime_quote:` 优雅降级用日线 last close），但「一等公民」须有 live 价，故必修。

### 2.3 新 `OkxPerpetualFetcher`（`data_provider/okx_perpetual_fetcher.py`）

```python
class OkxPerpetualFetcher(OkxFetcher):
    name = "OkxPerpetualFetcher"
    priority = int(os.getenv("OKX_PERPETUAL_PRIORITY", "55"))  # 仅说明用；perp 池只此一员

    def _to_exchange_symbol(self, code: str) -> str:
        base, quote = parse_perp_code(code)          # BTC/USDT:PERP -> ('BTC','USDT')
        return f"{base}-{quote}-SWAP"                 # OKX SWAP instId
```

- 仅覆写 `_to_exchange_symbol`；`_request_klines`/`_parse_klines`/`_request_ticker`/`_parse_ticker`/`_normalize_data` 全继承（OKX SWAP candles 与 ticker 用相同端点与响应结构）。
- `crypto_base._fetch_raw_data`（:98）与 `get_realtime_quote`（:133）的校验 `is_crypto_code` 放宽为 `is_crypto_code(code) or is_perp_code(code)`（=`is_crypto_like`），使 perp 子类通过；spot 子类行为不变（其 `_to_exchange_symbol` 对 perp code 产非法 instId，且路由保证 spot 源不接 perp）。
- 注册：加入 `_init_default_fetchers` 的 `crypto_fetchers` 列表（`base.py:1127`，并入 `self._fetchers`，供日线过滤命中 + 实时 `_get_fetcher_by_name` 查到）+ `_DAILY_MARKET_FETCHER_SUPPORT={"crypto_perp"}` + `get_realtime_quote` 的 `okx_perp` 分发分支（见 §2.2）。

### 2.4 复用既有 crypto 链（门控/解析放宽）

- derivatives 注入（A）：`attach_crypto_contracts` 门控 `is_crypto_code(code) or is_perp_code(code)`；`CryptoDerivativesService.collect(code)` 取 base/quote 用 `parse_perp_code` if perp else `partition("/")`。→ perp 分析自带 funding/mark/OI 块（A 的渲染对 `context["crypto_contracts"]` 已就绪）。
- 单位：`_crypto_volume_amount_units`（analyzer.py）对 perp code 用 `parse_perp_code` 取 (BASE,QUOTE)，否则按 `/` split。**这是 analyzer 唯一需要的 perp 改动。**
- 语境：`market_context.py` **无需改动**（`if "/" in code: return "crypto"` 已覆盖 perp）。

### 2.5 存储/报告/Web

- perp 日线以 perp code 串（`BTC/USDT:PERP`）入 `StockDaily`，与现货不撞，**无 schema 改动**。
- 报告/prompt/Web：perp 被识别为 crypto market，复用既有 crypto 渲染（价格精度、单位、crypto_contracts 块、子项目 A 的 ReportCryptoMetrics 卡片）。**无 Web 新组件**（perp 报告即 crypto 报告 + 永续指标块）。

### 2.6 配置

复用 `crypto_derivatives_enabled`；无新配置项、无 `.env.example`/registry/locale 改动（`OKX_PERPETUAL_PRIORITY` 仅作 fetcher 内默认常量，非用户配置面，不入 registry）。

## 3. 数据流

```
--stocks BTC/USDT:PERP
  → get_market_for_stock → "crypto"（24/7 日历、crypto 指南）
  → get_daily_data: is_perp → "crypto_perp" 池 → OkxPerpetualFetcher
       → _to_exchange_symbol → BTC-USDT-SWAP → OKX /market/candles → 标准 OHLCV → StockDaily(code="BTC/USDT:PERP")
  → get_realtime_quote: is_perp → okx_perp 分发 → OkxPerpetualFetcher ticker（perp 最新价；失败优雅降级用日线 last close）
  → trend/technical analysis（消费 OHLCV，与现货一致）
  → attach_crypto_contracts（is_perp 门控放宽）→ context["crypto_contracts"]（funding/mark/OI）
  → analyzer.analyze → crypto 报告 + 永续指标块（+ A 的报告透出）
```

## 4. 错误处理 / 兼容

- perp 取数失败（OKX 不可达/非法标的）→ `DataFetchError`，与现货一致的失败语义；单分析失败不拖垮批量。
- 非 perp 代码零影响（`is_perp_code` 仅匹配 `:PERP`，与现货/股票零冲突）。
- crypto_base 校验放宽为 `is_crypto_like` 后，spot 子类对 perp code 仍因路由不接触而无影响；即便误路由也产非法 instId 干净失败。
- derivatives 注入复用 `crypto_derivatives_enabled`；关闭则 perp 分析无 funding/mark/OI 块，但日线分析照常。
- 存储无 schema 变更，向后兼容；既有现货/股票分析完全不受影响。
- **API/Web 寻址边界（本期 CLI-first）**：perp code 含 `:`。code-in-path 接口（`stocks.py:400/469` quote/history、`history.py:161` by-code）用 `{stock_code:path}` 转换器，能捕获含 `:`/`/` 的 path，故修复实时/日线路由后**技术上可达**，但 `:` 需 URL 编码（`%3A`），**不在本期验证范围**；Web 历史详情按 id/query_id 取，perp 报告可正常查看；Web/桌面端发起 perp 分析（code 走 body）不受 `:` 影响。本期只保证 `--stocks BTC/USDT:PERP` CLI 路径 + 报告查看。

## 5. 测试（离线，无网络、无 LLM）

| 层 | 用例 |
|---|---|
| classify | `is_perp_code`：`BTC/USDT:PERP`→True；`ETH/USDC:PERP`→True；`BTC/USD:PERP`(非线性)→False；`BTC/USDT`(现货)→False；`BTC-USDT-SWAP`→False；空/股票→False。`parse_perp_code` 取 (BASE,QUOTE)。`is_crypto_like` 覆盖现货+永续。 |
| normalize | `normalize_stock_code("btc/usdt:perp")` → `BTC/USDT:PERP`（保留后缀、大写）。 |
| market/calendar | `get_market_for_stock("BTC/USDT:PERP")` → `"crypto"`；`is_market_open("crypto",d)`/`infer_market_phase` 恒开/INTRADAY（既有，验证 perp 经 market="crypto" 命中）。 |
| fetcher | `OkxPerpetualFetcher._to_exchange_symbol("BTC/USDT:PERP")` → `BTC-USDT-SWAP`；mock `_http_get` → `get_daily_data` 标准 OHLCV（复用 _parse/_normalize）；ticker 同理。 |
| daily routing | `get_daily_data` 对 perp code 选 `crypto_perp` 池（只含 OkxPerpetualFetcher）；现货仍选 `crypto` 池（不含 perp 源）；并断言 `_filter_daily_fetchers_for_market(fetchers,"crypto_perp")` **确实过滤**（guard 集已含 crypto_perp，否则原样返回全部）。 |
| realtime routing | `get_realtime_quote` 对 perp code 命中 `okx_perp` 分发 → OkxPerpetualFetcher（mock `_get_fetcher_by_name`），**不**落 A 股实时源、**不**命中 spot OkxFetcher。 |
| derivatives 注入 | `attach_crypto_contracts` 对 perp code 注入（门控放宽 + parse_perp_code）。 |
| units/context | `_crypto_volume_amount_units("BTC/USDT:PERP")` → (BTC,USDT)；market_context 对 perp 经 `/` 走 crypto 指南（无代码改动，行为断言）。 |
| 端到端 | seeded perp 日线 + mock 实时/derivatives → pipeline 产出 completed 分析（crypto 报告结构 + 永续指标块）。 |

- 真实在线可达性走 `network-smoke`（OKX SWAP candles/ticker 已存在公共接口）。
- **实现期审计步**：`grep` 确认无别处对该 code 串按 `:` 或裸 `/` split 造成误解析（除已识别的 `_crypto_volume_amount_units`）；存储/`get_analysis_context`/日志/缓存键把 perp code 当不透明串。

## 6. 文档

- `docs/crypto-guide.md`：新增「永续合约标的（perp instrument）」节——notation `BASE/QUOTE:PERP`、用法 `--stocks BTC/USDT:PERP`、数据源（OKX SWAP 日线/实时）、与现货的关系（独立 code、自带 funding/mark/OI）、范围（OKX-only/线性/无回测）。
- `docs/CHANGELOG.md` `[Unreleased]` 扁平：`- [新功能] 支持 OKX 永续合约标的（notation BASE/QUOTE:PERP，如 BTC/USDT:PERP）：独立 perp 日线 + 实时 + 自带资金费率/标记价/未平仓量，走完整 crypto 分析链；OKX-only、线性、暂不含回测`。

## 7. 分支与回滚

- 分支：`feat/crypto-perp-instrument`，叠在 `feat/crypto-perp-review` 上。
- 回滚：新增 1 fetcher + 分类/路由/单位/语境/注入若干放宽分支 + 文档；`git revert`/丢弃分支即恢复。`is_perp_code` 仅匹配 `:PERP`，不识别即不路由，运行期天然隔离。

## 8. 范围边界（YAGNI / 留后续）

不做：perp 回测（子项目 E，资金费/杠杆/做空/强平，正确性密集）、Binance fapi 永续（451）、多交易所 perp、反向（inverse）永续、`UnifiedRealtimeQuote` 加 funding 字段、Web 永续专属新组件、`OKX_PERPETUAL_PRIORITY` 用户配置面。本期仅把单个 OKX 线性永续标的接入既有日线分析链。
