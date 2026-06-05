# 设计文档：数字货币（crypto）市场支持

- 日期：2026-06-05
- 状态：已通过设计评审并经两轮对抗式代码核验修订，待写实现计划（writing-plans）
- 方案：A —— crypto 作为第 4 个 `region`，骑现有"按 region 分派"抽象，最小侵入
- 范围基线（已与用户确认）：
  1. **分析方式**：crypto 纳入现有**每日分析流程**，分析时取实时价（不做日内/高频监控，留作阶段二）
  2. **数据源**：交易所公共行情，优先级 **Binance → OKX → Coinbase**（免 API Key）
  3. **标的与计价**：**USDT 计价现货为主**（如 `BTC/USDT`、`ETH/USDT`）
  4. **功能边界**：**只读行情分析**，不接入下单/提现/私钥

## 0. 修订记录

- v2（2026-06-05）：经 6-agent 对抗式代码核验修订。关键更正：
  - **更正核心架构误判**：原 v1 称"在 `detect_market` 加 `/` 规则即可路由"是错的——`detect_market` 只供 LLM 提示词，不参与数据路由。本版把**数据路由层**与**LLM 语境层**拆成两层分别设计（见 §3.1、§3.3）。
  - 补全：实时行情独立路由（§3.3）、展示格式（§3.5）、API/前端 symbol 校验（§3.6）、计价币 allowlist（§3.1）、`CRYPTO_PROFILE` 完整字段（§3.4）、`days→limit` 映射（§3.2）。
  - 去噪：原审查中 `limit_up_count` 崩溃为**误报**（有 `has_stats` 保护，美股已验证）；`MarketAnalyzer` region 限制属**大盘复盘=phase-2**；OKX 100 根上限**够用**（系统最多取 ~60 根）。见 §10 延后项与 §11。
- v2.1（2026-06-05，第二轮核验后微调，3-agent 复核）：A1/B 类 file:line 全部确认准确、C 类去噪全部确认成立。修正 4 处：
  - **§3.3 关键修复**：把 `get_market_for_stock` 改为返回 `"crypto"` 会破坏原 `None` 的 fail-open——`get_open_markets_today` 只产出 ⊆{cn,hk,us}，导致 crypto 被交易日过滤剔除。改为"让 `get_open_markets_today` 恒含 crypto"（或 `_compute_trading_day_filter:416` 显式放行）。
  - **§3.3 实时路由**：源遍历是按源名 `elif` 硬分派（`:1608-1631`），须新增 crypto 源分派分支，不能只改配置。
  - **§3.1**：澄清"/"排除是 defense-in-depth，主防线是"路由只选 crypto fetcher"（去掉"一处改全局自动路由"的过度表述）。
  - **§3.4/§8**：计价币真实位置是 `portfolio_service.py`（`VALID_MARKETS` + `_default_currency_for_market`），非 `storage.py`；仍属 portfolio-only 延后项。

## 1. 背景与目标

本系统基于 `daily_stock_analysis`（DSA）落地，现已覆盖 A股/港股/美股。目标是新增**数字货币现货市场**：把 crypto 标的纳入既有"抓数据 → 技术分析 → LLM 分析 → 报告 → 推送"的每日流程，复用结合成交量与趋势的买卖建议能力，从 Binance/OKX/Coinbase 公共行情获取实时与历史数据。

**非目标（YAGNI / 安全）：** 下单、交易执行、提现、私钥/签名鉴权；日内高频监控与触发式告警；合约/永续/杠杆；以 AICoin 为主数据源；引入 ccxt；crypto 大盘复盘（phase-2）。

## 2. 现状结论（探查 + 核验依据）

> file:line 已二次核验；实现时仍以代码现状为准。

### 2.1 关键认知：DSA 有「两套」与市场相关的判定，必须分别改造

| 层 | 函数/位置 | 作用 | crypto 改造 |
| --- | --- | --- | --- |
| **数据路由层**（决定用哪个数据源） | `data_provider/base.py`：`get_daily_data:1166-1167`、`get_realtime_quote:1545-1546`、`_filter_daily_fetchers_for_market:698`、`get_market_for_stock`(`src/core/trading_calendar.py:109-129`) | 用 `is_us_stock_code`/`_is_hk_market`/`is_hk_stock_code` 判市并选 fetcher | **必须在这里识别 crypto**，否则误落 A股 |
| **LLM 语境层**（只影响提示词） | `detect_market`(`src/market_context.py:16`，被 `:107/:122` 调用) | 给 LLM 注入市场角色/指引 | 加 crypto 分支，但仅影响 prompt |

- **数据路由的真实判别**：`get_daily_data:1166`：`is_us = is_us_index or is_us_stock_code(code)`；`:1167`：`is_hk = (not is_us) and _is_hk_market(code)`；都为 False 时落 `:1271` 的 A股数据源循环。`get_realtime_quote` 同构（`:1545-1546`）。
- **共享判别函数**：`is_us_stock_code`(`data_provider/us_index_mapping.py:65-94`，正则 `^[A-Z]{1,5}(\.[A-Z])?$`)、`is_hk_stock_code`/`_is_hk_code`(`akshare_fetcher.py:194-206/:117-141`)、`_is_hk_market`(`base.py:149-163`)。这些函数也被各 fetcher 内部二次调用（`baostock_fetcher.py:150/:208`、`tushare_fetcher.py:376/419/464/...`、`yfinance_fetcher.py:114`）。
- **数据源过滤白名单**：`_filter_daily_fetchers_for_market:698`：`if market not in {"cn","hk","us"}: return fetchers`（不含 crypto 会短路、过滤失效）；按市场筛选依据 `_DAILY_MARKET_FETCHER_SUPPORT`(`:566`，消费于 `:704`）。
- **实时行情走另一套**：`get_realtime_quote`(`base.py:1503-1721`) **不读** `_DAILY_MARKET_FETCHER_SUPPORT`，而是按 `config.realtime_source_priority` 遍历源（`:1601-1704`）。
- **符号规范化是安全的**：`normalize_stock_code`(`base.py:68-135`)对含 `/` 的输入不匹配任何股票前后缀分支，**原样返回**（不破坏）；`canonical_stock_code`(`:256-270`)仅做大写。`/` 能存活到所有判别点。
- **交易日历**：`is_market_open(market, check_date)`(`trading_calendar.py:132-157`)对不在 `MARKET_EXCHANGE`(`:39`,`{"cn":"XSHG","hk":"XHKG","us":"XNYS"}`)/`MARKET_TIMEZONE`(`:42`)的市场 **fail-open 返回 True**；`get_effective_trading_date`(`:184-229`)同理。
- **调度 gate**：`main.py:_compute_trading_day_filter`(`:388-427`)用 `get_market_for_stock` 取市场与 `open_markets` 比对；当前 crypto 返回 `None` 走 fail-open（侥幸通过，但语义不明）。
- **市场画像**：`MarketProfile`(`src/core/market_profile.py:14-27`，含字段 `region/mood_index_code/news_queries/has_market_stats/has_sector_rankings/prompt_index_hint`)；`get_profile(region)`(`:70-76`)对未知 region **静默返回 `CN_PROFILE`**（需显式加 crypto 分支）。`US_PROFILE.has_market_stats=False`（`:52`）——证明"无统计市场"路径已被现网验证可用。
- **per-symbol vs 大盘**：per-symbol 分析用 `StockAnalyzer`(`src/stock_analyzer.py:201`，`__init__(self)` **无 region**)；`MarketAnalyzer`(`region` 白名单在 `:138`)仅在**大盘复盘**实例化（`market_review.py:133/152`、`market_light_service.py:35`）。故 `MarketAnalyzer` 的 region/统计字段问题属 **phase-2**。
- **下游容错**：`UnifiedRealtimeQuote`(`realtime_types.py:109-188`)的 `change_pct/amount/volume_ratio/turnover_rate/pe_ratio/pb_ratio` 等均 Optional 默认 None；报告/LLM 对缺失字段已有 N/A 降级。**但展示格式化假设是股票**（见 §3.5）。
- **现状无任何 crypto 代码**，干净起步。

## 3. 总体设计

### 3.1 市场身份：crypto 作为第 4 个 region + 统一判别函数

- `region` 取值集合扩展为 `{cn, hk, us, crypto}`。
- **判别特征（零冲突）**：代码**含 `/` 即 crypto**（用户写 `BASE/QUOTE`，如 `BTC/USDT`）。A股 6 位数字、港股 5 位数字/`hk`、美股字母均不含 `/`。
- **计价币 allowlist**：`SUPPORTED_QUOTES = {"USDT","USDC","USD","BUSD","BTC","ETH"}`（稳定币 + 主流交叉对）。识别规则：`is_crypto_code(code)` = `code` 含 `/` 且 QUOTE ∈ `SUPPORTED_QUOTES`；不支持的法币计价（CNY/JPY/KRW…）按 §6 记录并跳过（不做汇率换算）。
- **新增统一判别**：`data_provider` 暴露 `is_crypto_code(code) -> bool`，作为所有路由点的唯一 crypto 判据。
- **主防线 = 路由只选 crypto fetcher**：crypto 的正确处理**主要靠**在 `get_daily_data`/`get_realtime_quote`/`_filter_daily_fetchers_for_market` 把 crypto 标的**只路由到 crypto fetcher**（见 §3.3），股票 fetcher 根本不会拿到 crypto 代码。
- **次防线（defense-in-depth）= 共享股票判别加排除**：在 `is_us_stock_code`/`is_hk_stock_code`/`_is_hk_market`/`is_us_index_code` 内部统一 `if "/" in code: return False`。注意各 fetcher 内部也二次调用这些函数（`yfinance_fetcher.py:114`、`efinance_fetcher.py:380`、`tushare_fetcher.py:373/376`、`baostock_fetcher.py:150`）；**该排除不能"自动"把 crypto 路由到正确数据源**（这是主防线的职责），它只保证万一某股票 fetcher 收到 crypto 代码时不会把它误当本市场标的处理（会判 False → 由 manager fallback）。两条防线缺一不可。
- 规范化：`normalize_stock_code` 在最前面加 `if "/" in code: return code.upper()`（`BTC/usdt`→`BTC/USDT`），股票分支逻辑不变（回归保护）。

### 3.2 数据层：共享基类 + 三所薄子类

新增（使用现有 `requests`/`curl_cffi`，**不引入新重依赖**）：

- `data_provider/crypto_base.py` —— `CryptoExchangeBase(BaseFetcher)`
  - 职责：`BASE/QUOTE` ↔ 交易所符号互转（子类覆盖 `_to_exchange_symbol`）；**honor `get_daily_data(code, days=N)` 的 `days` 参数**，映射为各所 limit；1d K线 → `STANDARD_COLUMNS`；ticker → `UnifiedRealtimeQuote`（核心字段 code/name/price/change_pct/volume/amount/high/low；PE/PB/换手率/量比/市值保持 None）；声明支持市场 `{"crypto"}`；`priority` 属性决定排序（见 §3.3）。
  - **`days→limit` 映射**：系统当前最多取 ~60 根日线（`pipeline.py:253` `days=30`、`:413` `~60 trading days for MA60`、`:961` `days=min_days`），故 ≤100 根即可满足三所：Binance `limit=min(days+buffer,1000)`、OKX `limit=min(days+buffer,100)`（基础 candles 接口，足够；若未来需 >100 根再用 `history-candles` 分页）、Coinbase 用 `start/end` 时间窗。
- `data_provider/binance_fetcher.py` —— `BinanceFetcher(CryptoExchangeBase, priority=5)`
  - 历史：`GET {BASE}/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=N`；K线 `[openTime,open,high,low,close,volume,closeTime,quoteAssetVolume,...]` → `amount=quoteAssetVolume`、`volume=volume(base)`、`date=openTime(ms)`。
  - 实时：`GET /api/v3/ticker/24hr?symbol=BTCUSDT` → `price=lastPrice`、`change_pct=priceChangePercent`、`volume`、`amount=quoteVolume`、`high/low`。
  - Base URL 可配（`BINANCE_BASE_URL`，默认 `https://api.binance.com`；地区受限可切 `https://data-api.binance.vision`）。
- `data_provider/okx_fetcher.py` —— `OkxFetcher(CryptoExchangeBase, priority=6)`
  - 历史：`GET https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=1D&limit=N(≤100)`；candle `[ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]` → `amount=volCcyQuote`。
  - 实时：`GET /api/v5/market/ticker?instId=BTC-USDT`。
- `data_provider/coinbase_fetcher.py` —— `CoinbaseFetcher(CryptoExchangeBase, priority=7)`
  - 历史：`GET https://api.exchange.coinbase.com/products/{BTC-USD|BTC-USDT}/candles?granularity=86400`；**注意列序 `[time,low,high,open,close,volume]`** 且**无 quote volume**。决定：`amount` 置 **None**（不伪造，下游可降级；契合"不静默返回错值"），并在日志标注来源。
  - 实时：`GET /products/{id}/ticker`。

### 3.3 路由与分派改造点（核心：改"数据路由层"，不是只改 detect_market）

- `data_provider/__init__.py`：导出三个新 fetcher；在 `DataFetcherManager` 初始化注册（`priority` 5/6/7 → 排序得 Binance→OKX→Coinbase，`base.py:1108` 按 priority 排序）；`_DAILY_MARKET_FETCHER_SUPPORT` 为三者登记 `{"crypto"}`。
- `data_provider/base.py`：
  - `_filter_daily_fetchers_for_market:698` 白名单加入 `"crypto"`（否则短路、过滤失效）。
  - `get_daily_data:1166` 前增加 `if is_crypto_code(code): is_crypto=True` 分支，**优先于** `is_us_stock_code/_is_hk_market`；crypto 时 `source_order` 限定 crypto fetcher。
  - `get_realtime_quote:1545`：crypto 走**独立实时路由**。注意源遍历循环（`:1601-1704`）是**按源名 `elif` 硬分派**的（现有分支 `efinance`/`akshare_em`/`akshare_sina`/`tencent`/`tushare`，`:1608-1631`），因此**仅在配置里加 `binance/okx/coinbase` 不够**，必须在该循环中**新增对应的 crypto 源分派分支**；并新增 `CRYPTO_REALTIME_PRIORITY`（默认 `binance,okx,coinbase`），对 crypto 标的只在 crypto 源里挑。
  - `is_us_stock_code/is_hk_stock_code/_is_hk_market/is_us_index_code` 内部加 `/` 排除（见 §3.1）；新增 `is_crypto_code`；`normalize_stock_code` 加 crypto 前置分支。
- `src/core/trading_calendar.py`（**所有读 `MARKET_EXCHANGE`/`MARKET_TIMEZONE` 的函数都要对 crypto 特判**）：
  - `get_market_for_stock` 增 `if is_crypto_code(code): return "crypto"`。
  - `is_market_open` 增显式 `if market == "crypto": return True`（在 exchange-calendars 查询前；不依赖 fail-open）。同样需特判的还有 `:282`、`:463` 等读这两个字典的函数。
  - `get_effective_trading_date` 对 crypto 直接返回当前 UTC 日期（无回看、无节假日）。
  - **`get_open_markets_today`（`:517-519` 遍历 `MARKET_TIMEZONE`）必须把 crypto 视为恒开市**（始终包含 `"crypto"`）。
- **`main.py:_compute_trading_day_filter`（关键，避免回归）**：当前 `:416` 判定为 `if mkt in open_markets or mkt is None: 纳入`。一旦 §上把 `get_market_for_stock` 改为返回 `"crypto"`，而 `get_open_markets_today` 只产出 ⊆`{cn,hk,us}`，则 `"crypto"` 既不在 `open_markets`、也不是 `None` → **crypto 会被错误剔除**（破坏原本 `None` 的 fail-open）。必须二选一并落实：① 让 `get_open_markets_today` 恒含 `"crypto"`（推荐，单点修复，且让 `is_market_open` 语义一致）；或 ② `:416` 条件显式加 `or mkt == "crypto"`。验收（§9-2）须含"仅 crypto 自选股 + 周末"用例。
- `src/market_context.py`（**仅 LLM 语境**）：`detect_market` 加 `if "/" in code: return "crypto"`（置于股票规则前）；新增 `_MARKET_ROLES['crypto']`/`_MARKET_GUIDELINES['crypto']`（无涨跌停/T+1/政策面，强调高波动、24h 连续交易、杠杆/合约风险、流动性与交易所价差）。

### 3.4 市场画像与计价币

- `src/core/market_profile.py`：新增 `CRYPTO_PROFILE = MarketProfile(region='crypto', mood_index_code='BTC', news_queries=['比特币','以太坊','加密货币','crypto',...], prompt_index_hint='以 BTC/ETH 走势衡量加密市场整体情绪', has_market_stats=False, has_sector_rankings=False)`（**字段需与 dataclass 完全对齐，含 `prompt_index_hint`**）；`get_profile` 增 `if region == 'crypto': return CRYPTO_PROFILE`，并建议对未知 region 改为抛错而非静默回退 CN。
- 计价币推断：crypto 的 `currency` 从 symbol 的 QUOTE 推断（`BTC/USDT`→`USDT`），替代默认 `CNY`。真实位置是 **`src/services/portfolio_service.py`** 的 `_default_currency_for_market()`（`:1606-1610`，现仅 hk/us/默认cn）与 `VALID_MARKETS = {"cn","hk","us"}`（`:32`，会**拒绝 crypto 持仓**，`:1586` 校验）——而非 `storage.py`。**仅在持仓/成交（portfolio）语境触发**：自选股（`STOCK_LIST`）的 per-symbol 分析不经 portfolio，不受影响；只读行情分析不依赖此项。故列为可在 phase-1 内或紧随其后实现的低耦合点（见 §10），实现时需同时扩 `VALID_MARKETS` 与 `_default_currency_for_market`。

### 3.5 展示格式（crypto 专项，必须改）

- **价格精度**：`_format_price`(`src/analyzer.py:3359`)现用 `:.2f`，对小币种（如 SHIB `0.00000123`）会显示 `0.00`。新增 `_smart_format_price(price)`：按数量级动态精度（≥1 用 2–4 位，0.0001–1 用 6 位，<0.0001 用 8 位）；crypto 走此函数。
- **成交量单位**：`_format_volume`(`src/analyzer.py:3328-3332`)固定 `股/万股/亿股`，crypto 应为 base 资产单位（`BTC/USDT` → 量单位 `BTC`）；成交额标签带计价币（`成交额(USDT)`）。改 `_format_volume/_format_amount` 接受 `market/symbol` 参数。
- Coinbase 数据源下 `amount=None`，报告/通知显示 N/A 并可加脚注"（该数据源不提供成交额）"。

### 3.6 API / 前端 symbol 校验（用户可见入口，必须改）

- `api/v1/endpoints/stocks.py:77-87` 的 `_STOCK_CODE_RE` 不含 `/`，会把 `BTC/USDT` 判为非法。需在正则增加 crypto 分支：`[A-Z0-9]{1,10}/(USDT|USDC|USD|BUSD|BTC|ETH)`（与 `SUPPORTED_QUOTES` 对齐）。
- 同步前端 `validateStockCode`（`apps/dsa-web/`，注释称二者对齐）；`_validate_and_normalize_stock_code` 增加 crypto 用例测试。
- Web/Desktop 若有市场枚举/展示，按需加 crypto 标识（低优先，见 §10）。

### 3.7 下游（报告 / LLM / 通知）

- **主逻辑无需改**：crypto 缺失的 PE/PB/换手率/量比/筹码字段本就 Optional，已有 N/A 降级；LLM prompt 对缺失字段明确"数据缺失，不得编造"。
- **增量**：注入 crypto 市场指引（经 `detect_market`/`market_context`/`CRYPTO_PROFILE`），让 LLM 按 crypto 语境给买卖建议（结合成交量与趋势，与股票一致的指标口径）；新闻检索复用 `news_queries`（中文 queries 命中率为实现期风险，见 §10）。

## 4. 数据流

```
每日运行
  └─ 自选股含 BTC/USDT
       └─ get_market_for_stock → 'crypto'（is_crypto_code 判定）
            └─ 交易日过滤：crypto 恒纳入（_compute_trading_day_filter 不剔除）
                 └─ DataFetcherManager.get_daily_data(BTC/USDT, days≈60)
                      └─ 数据路由：is_crypto_code → 限定 crypto fetcher
                           └─ Binance(p5) →(熔断/失败)→ OKX(p6) → Coinbase(p7)
                                └─ 1d OHLCV（统一列，honor days→limit）
                                     └─ 技术指标（MA/RSI/量价，源无关，原样复用）
                                          └─ get_realtime_quote（独立 crypto 实时路由）取最新价
                                               └─ 上下文构建（基本面 None + CRYPTO_PROFILE + detect_market 指引）
                                                    └─ LLM 分析 → 报告（_smart_format_price/crypto 单位）→ 推送
```

## 5. 配置（.env）

- `STOCK_LIST` 支持混排：`600519,hk00700,AAPL,BTC/USDT,ETH/USDT`。
- 新增**可选**项（"不配置也能跑、配置增强"）：
  - `CRYPTO_DATA_PRIORITY=binance,okx,coinbase`（日线源顺序，默认即此序）
  - `CRYPTO_REALTIME_PRIORITY=binance,okx,coinbase`（实时源顺序）
  - `BINANCE_BASE_URL=https://api.binance.com`（地区受限切 `https://data-api.binance.vision`）
- **不需要任何 API Key**（只读公共行情）。
- 同步更新 `.env.example`、`NOTES.md`、`docs/CHANGELOG.md`（`[Unreleased]` 扁平条目）。

## 6. 错误处理（契合稳定性护栏）

- 单交易所超时/HTTP/限流(429) → 按 `BaseFetcher` 契约抛出 → manager 熔断该源并 fallback 下一所；**单源失败不拖垮主流程**。
- 某 symbol 在某所不存在 → fallback 下一所；全部失败 → **记录错误并跳过该标的**，主流程继续；不静默返回错值。
- 含 `/` 但 QUOTE ∉ `SUPPORTED_QUOTES` → 记录并跳过，不当成股票误路由。
- Binance 地区受限 → OKX/Coinbase 兜底 + `BINANCE_BASE_URL` 旁路。
- **结构化日志**：跳过标的时输出 `symbol / 已尝试源 / 各源错误`，便于排查。

## 7. 测试策略（CI `-m "not network"` 友好）

- **单元（离线，录制 fixtures，不打真网）：**
  - **路由层（重点，覆盖 A1）**：`is_crypto_code('BTC/USDT')==True`；`is_us_stock_code('BTC/USDT')==False`、`_is_hk_market('BTC/USDT')==False`；`get_daily_data`/`get_realtime_quote` 对 `BTC/USDT` 选中 crypto fetcher 而非 A股源；`_filter_daily_fetchers_for_market('crypto', ...)` 正确过滤；`get_market_for_stock('BTC/USDT')=='crypto'`。
  - A股/港股/美股识别与路由**回归不变**。
  - `normalize_stock_code` 各 crypto 形态 → `BTC/USDT`；三所 symbol 互转；三所 K线/ticker 解析（含 Coinbase 列序、缺 quote volume → amount None）；`days→limit` 映射。
  - `is_market_open('crypto', any_date)==True`；`get_effective_trading_date('crypto')` 返回 UTC 当日；`_compute_trading_day_filter` 对 crypto 恒纳入（含"仅 crypto 自选股 + 周末"用例）。
  - `get_profile('crypto')` 返回 `CRYPTO_PROFILE`（字段齐全可构造）；`_smart_format_price` 大/小币种；`_format_volume` crypto 单位。
  - API `_STOCK_CODE_RE` 接受 `BTC/USDT`、拒绝 `BTC/CNY`。
- **联网（`@pytest.mark.network`）：** 三所真实公共端点 smoke，仅 network job 运行。
- **回归：** 现有 A股/港股/美股 路由与日历测试全绿。

## 8. 文件清单

**新增：**
- `data_provider/crypto_base.py`、`binance_fetcher.py`、`okx_fetcher.py`、`coinbase_fetcher.py`
- `tests/test_crypto_symbol_routing.py`、`tests/test_crypto_fetchers.py`、`tests/test_crypto_calendar.py`、`tests/test_crypto_format.py`（+ fixtures）

**修改：**
- `data_provider/__init__.py`、`data_provider/base.py`（路由层 + 共享判别 + 白名单 + normalize）、`us_index_mapping.py`、`akshare_fetcher.py`（`/` 排除）
- `src/market_context.py`、`src/core/market_profile.py`、`src/core/trading_calendar.py`
- `src/analyzer.py`（`_smart_format_price`/`_format_volume`/`_format_amount`）
- `main.py`（`_compute_trading_day_filter`）
- `api/v1/endpoints/stocks.py`（`_STOCK_CODE_RE`）+ `apps/dsa-web/` 前端 `validateStockCode`
- 计价币推断点 `src/services/portfolio_service.py`（`VALID_MARKETS` + `_default_currency_for_market`，仅 crypto 分支，可紧随 phase-1）
- `src/config.py`（`CRYPTO_DATA_PRIORITY`/`CRYPTO_REALTIME_PRIORITY`/`BINANCE_BASE_URL`）
- `.env.example`、`NOTES.md`、`docs/CHANGELOG.md`

## 9. 验收标准

1. `python main.py --dry-run --stocks BTC/USDT` 走 crypto 数据源（非 A股源）取到 1d K线与实时价（在线）；离线单测覆盖路由与解析。
2. crypto 标的在**任意自然日**（含周末，且当 STOCK_LIST 仅含 crypto 时）都进入每日分析，不被交易日 gate/should_skip 剔除。
3. Binance 不可用时自动 fallback OKX/Coinbase；全失败仅跳过该标的，主流程不崩。
4. 报告/通知对 crypto 缺失字段显示 N/A；小币种价格不显示为 0.00；成交量单位非"股"。
5. 通过 API/Web 添加 `BTC/USDT` 不被校验拒绝；`BTC/CNY` 被拒。
6. 现有 A股/港股/美股 全部回归测试通过，行为不变。
7. 全程不涉及任何下单/私钥；仅读取公共端点。

## 10. 可接受的延后项（不阻断 phase-1）

- **大盘复盘（phase-2）**：`MarketAnalyzer` region 白名单(`market_analyzer.py:138`)、`MARKET_REVIEW_REGION` 合法值(`config.py:1692/2247`)、市场统计字段——crypto 大盘复盘本就延后，phase-1 用 `StockAnalyzer`（无 region）做 per-symbol，不触发这些。
- **回测**：`--backtest`/forward-return 假设交易日历(`backtest_service.py:117-129`)，crypto 兼容性 phase-1 不做，实现时标注必查。
- **计价币推断**：仅在配置 crypto 持仓时触发，与只读分析核心解耦，可 phase-1 内做或紧随。
- **新闻检索**：中文 `news_queries` 能否命中 crypto 来源为实现期风险，非 spec 缺陷；必要时补英文 queries。
- **时区对齐**：UTC(crypto) vs 本地(股票) 日期口径，混合报告可加时区标注，低优先。
- **前端展示**：若 Web/Desktop 需展示市场标识/枚举，按需补，低优先。

## 11. 风险与回滚

- **风险**：交易所地区限制/限流、Coinbase 无 quote volume、币种上新下线、`region` 语义被扩展。均有对应处理（base url 旁路、`amount` 置 None、fallback+跳过、统一判别函数）。
- **回滚**：改动以新增文件 + `/` 判别分支为主；移除三个 fetcher 注册、`is_crypto_code` 分支与 `/` 排除，即恢复纯股票行为。配置项不填则与现状一致。

## 12. 阶段划分

- **阶段一（本设计）**：每日流程纳入 crypto 现货、三所公共行情、只读分析与买卖建议、展示格式、API/前端 symbol 校验。
- **阶段二（未来，需另立设计）**：日内/高频实时监控与告警；crypto 大盘复盘（BTC/ETH 概览 + region 扩展）；回测兼容；可选合约维度。
