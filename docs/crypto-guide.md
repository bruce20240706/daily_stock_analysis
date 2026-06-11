# 数字货币（Crypto）分析指南

本系统在 A 股 / 港股 / 美股之外，支持对**数字货币现货**进行只读行情分析，并纳入既有的每日分析流程（抓取数据 → 技术分析 → LLM 分析 → 生成报告 → 推送）。

> **边界**：仅做只读行情分析，**不接入下单、交易执行、提现、私钥/签名鉴权**。本功能不持有任何资金操作能力。

## 1. 代码格式与计价币

- 代码格式为 `BASE/QUOTE`（交易对），例如 `BTC/USDT`、`ETH/USDT`、`BTC/USDC`。
- 计价币（QUOTE）白名单：`USDT`、`USDC`、`USD`、`BUSD`、`BTC`、`ETH`。
- 仅当字符串含且仅含一个 `/`、BASE 非空、QUOTE 属于上述白名单时，才识别为 crypto。法币计价（如 `BTC/CNY`）不受支持，避免跨汇率换算。
- crypto 代码与股票代码（6 位数字 / `hk` 前缀 / 美股字母）零冲突。

使用示例：

```bash
python main.py --stocks BTC/USDT,ETH/USDT
python main.py --stocks 600519,hk00700,AAPL,BTC/USDT   # 与股票混合分析
```

## 永续合约标的（perp instrument）

除现货外，可分析 OKX **永续合约**标的，notation 为 `BASE/QUOTE:PERP`（仅 USDT/USDC 线性永续）：

```bash
python main.py --stocks BTC/USDT:PERP
python main.py --stocks BTC/USDT,BTC/USDT:PERP   # 现货 + 永续并列
```

- 数据源：OKX SWAP（`/market/candles` 日线、`/market/ticker` 实时，`instId=BASE-QUOTE-SWAP`）；24/7、走 crypto 指南。
- 自带资金费率/标记价/未平仓量（与现货同样经 `CRYPTO_DERIVATIVES_ENABLED` 注入分析）。
- 永续日线以独立 code（`BTC/USDT:PERP`）入库，与现货 `BTC/USDT` 互不影响。
- 范围：仅 OKX、仅线性；CLI 优先（含 `:` 的 API code-in-path 需 URL 编码 `%3A`，本期不专门验证）。

## 2. 数据源与地区受限

- 数据源为交易所公共行情（免 API Key）：默认优先级 **Binance → OKX → Coinbase**。单一数据源失败自动降级到下一个。
- 部分地区会被 Binance 限制（HTTP 451）。两种应对方式（可叠加）：
  - `BINANCE_BASE_URL=https://data-api.binance.vision`：改用 Binance 的公共数据镜像域名，规避主站地区限制。
  - `CRYPTO_DATA_PRIORITY=okx,binance,coinbase`：把 OKX 提到最前，减少在受限源上的等待。
- 相关环境变量：

| 变量 | 作用 | 默认 |
|------|------|------|
| `CRYPTO_DATA_PRIORITY` | 日线数据源优先级（逗号分隔） | `binance,okx,coinbase` |
| `CRYPTO_REALTIME_PRIORITY` | 实时行情数据源优先级 | `binance,okx,coinbase` |
| `BINANCE_BASE_URL` | Binance API 基础域名 | `https://api.binance.com` |

> Coinbase 不提供成交额（amount），该字段在报告中显示为 N/A，属正常。

## 3. 交易日历与时区

- crypto 为 **7×24 连续交易**：恒视为开市，不受交易日 / 节假日过滤影响，每日均生成分析。
- 日线 K 线以 **UTC** 自然日为口径；当日（UTC）K 线在 UTC 午夜前持续形成，系统将其标记为“盘中 / 当日 K 线未完结”。
- 与股票（按交易所本地时区与交易日）混合分析时，请注意两者日期口径不同。

## 4. 报告差异（相对股票）

- **无**涨跌停、T+1、盘前盘后概念；**无**市盈率 / 换手率 / 板块等传统基本面指标，相关字段在报告中显示为 N/A 或由 LLM 声明“不适用”，属正常，不代表数据缺失。
- 价格按数量级动态精度显示（避免极小币种如 SHIB 被显示为 0.00）；成交量 / 成交额以交易对的 BASE / QUOTE 为单位（如 `万BTC` / `亿USDT`），而非“股 / 元”。
- LLM 正文中的价格、目标位、止损位统一用计价币（如 USDT）表述。

## 5. 持仓（Portfolio）

- 持仓 / 成交支持 `market=crypto`。未显式指定 `currency` 时，计价币从交易对 QUOTE 推断（`BTC/USDT` → `USDT`），无法解析时回退 `USDT`。

## 6. 回测（Backtest）

crypto symbol 走与股票**相同**的回测流程，无需独立配置或日历：

- 回测对象是历史 `AnalysisHistory` 分析记录；crypto 分析记录（code 含 `/`，如 `BTC/USDT`）自动纳入回测候选（无市场过滤）。
- 回测 forward-return 直接按日期序取后续 K 线行数，不假设交易日历，**crypto 天然兼容**；历史日线来自既有 crypto K 线抓取器（Binance/OKX/Coinbase，`interval=1d`），缺数据时由回测自动补取并存入 `StockDaily`。
- 回测引擎对 `AnalysisHistory` 的 `operation_advice` / `stop_loss` / `take_profit`，在分析日之后的前向日线上评估方向正确性、止盈/止损命中与收益，与股票完全一致。
- 语义提示：`eval_window_days` 对 crypto 指**自然日**（每日均有 K 线），对股票指**交易日**；同样窗口天数下 crypto 覆盖的 K 线根数通常多于股票（周末/节假日 crypto 仍有数据）。`first_hit_trading_days` 对 crypto 即「命中所需日历日数」（= 交易日数）。
- 失败降级与股票一致：取不到起始/前向数据时记 `insufficient_data`，不影响其余回测。

### 永续回测（资金费 + 做空，1x）

perp 标的（`BASE/QUOTE:PERP`）回测在现货流程之上叠加两项永续机制（固定 1x，无杠杆/强平）：

- **做空盈亏**：看空建议记 `position="short"`，持有至窗口末，`simulated_return_pct = (入场−出场)/入场×100 + 资金费`（跌则盈）。做空不评估 TP/SL（analysis 价位为多头框架，反向套用会虚构精度）。
- **资金费成本**：按真实持有窗口（入场=起始 bar 收盘 → 出场=末 bar 收盘，≈ `eval_window_days×3` 个 8h 结算）对 OKX `funding-rate-history` 求和；多头 `−资金费`、空头 `+资金费`（正费率＝多头付空头）。
- **门控/降级**：资金费抓取复用 `CRYPTO_DERIVATIVES_ENABLED`（默认开），抓取失败或关闭时资金费记 0、方向盈亏照常；现货/股票回测不受影响。

## 7. API 用法

crypto 代码含 `/`，调用带路径参数的接口时，可直接传原始斜杠或 URL 编码（`%2F`）：

```
GET /api/v1/stocks/BTC/USDT/quote
GET /api/v1/stocks/BTC%2FUSDT/quote        # 等价
GET /api/v1/stocks/BTC%2FUSDT/history?days=30
```

## 8. 大盘复盘（crypto 市场综述）

### 8.1 启用方式

将 `MARKET_REVIEW_REGION` 设为 `crypto`，或以逗号拼接多市场：

```bash
# 仅 crypto 大盘复盘
MARKET_REVIEW_REGION=crypto

# A 股 + crypto 双市场复盘
MARKET_REVIEW_REGION=cn,crypto
```

> **注意**：`both` 仍等于 `cn+hk+us`，**不含** crypto。crypto 须显式 opt-in。

### 8.2 篮子配置（CRYPTO_MARKET_REVIEW_SYMBOLS）

复盘时使用的主流币篮子由 `CRYPTO_MARKET_REVIEW_SYMBOLS` 决定，默认为 12 只主流现货交易对：

```
BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,XRP/USDT,DOGE/USDT,ADA/USDT,AVAX/USDT,LINK/USDT,TRX/USDT,TON/USDT,DOT/USDT
```

可覆盖为任意 Binance / OKX / Coinbase 支持的交易对，逗号分隔：

```bash
# 自定义示例：聚焦 BTC/ETH + 新兴赛道
CRYPTO_MARKET_REVIEW_SYMBOLS=BTC/USDT,ETH/USDT,HYPE/USDT,FIL/USDT,SUI/USDT
```

### 8.3 报告内容

crypto 大盘复盘包含以下三个部分：

| 部分 | 说明 |
|------|------|
| **主流币篮子行情** | 篮子内各币种的实时报价（价格、涨跌幅、成交量等），数据经 Binance → OKX → Coinbase 自动 fallback |
| **市场叙事** | 由 LLM 综合篮子行情生成的市场综述与趋势分析 |
| **新币与上新动态** | LLM 综合检索到的新币/上新相关新闻生成的叙事段，严格依据新闻、不编造上新或价格 |

### 8.4 不含的内容（及原因）

以下指标在 crypto 大盘复盘中**不存在**，属正常，不代表功能缺陷：

| 缺失项 | 原因 |
|--------|------|
| 涨跌家数（breadth） | 无全市场 A 股/港股式统计数据源 |
| 板块涨跌排名 | 加密市场无标准行业板块分类数据源 |
| 市场灯（MarketLightSnapshot） | 依赖 A 股 / 港股 / 美股专用接口，crypto 无对应数据 |

### 8.5 结构化新币上新发现

大盘复盘支持自动扫描交易所新上线的交易对，产出结构化 `new_listings` 字段，并在 Web 上新行情表中展示。

#### 启用与配置

| 环境变量 | 默认值 | 说明 |
|------|------|------|
| `CRYPTO_NEW_LISTING_ENABLED` | `true` | 是否启用新币上新发现 |
| `CRYPTO_NEW_LISTING_WINDOW_DAYS` | `7` | 上新窗口（仅返回最近 N 天内上线的交易对） |
| `CRYPTO_NEW_LISTING_SOURCES` | `okx,coinbase` | 数据来源交易所，逗号分隔；如需 Binance 可追加 |
| `CRYPTO_NEW_LISTING_MAX` | `20` | 单次返回最大条目数 |

#### 三个数据来源

| 来源 | 上新时间字段 | 状态 | 说明 |
|------|------|------|------|
| **OKX** | `listTime`（原生毫秒时间戳） | 默认开启 | 无状态，每次直接查询接口；处处可用 |
| **Coinbase** | `new_at`（原生 ISO 时间戳） | 默认开启 | 无状态，处处可用；因 CDN 缓存，`new_at` 字段可能滞后实际上线数小时 |
| **Binance** | 快照差分（`data-api.binance.vision` exchangeInfo） | **默认关闭** | 依赖持久数据库卷存储历史快照进行差分比对；每日 GitHub Actions 临时环境无持久卷，差分结果为空，故默认不纳入 `CRYPTO_NEW_LISTING_SOURCES` |

> 如需启用 Binance 数据来源，需同时满足：运行环境有**持久化数据库卷**（如 Docker 挂载 volume），并在 `CRYPTO_NEW_LISTING_SOURCES` 中追加 `binance`，例如：
> ```
> CRYPTO_NEW_LISTING_SOURCES=okx,coinbase,binance
> ```
> GitHub Actions 每日任务中不建议追加，否则差分结果永远为空。

> **CoinGecko / CoinMarketCap**：其新币上新接口属付费功能，未采用。

#### 去重逻辑

- 按 **base asset**（如 `BTC`、`ETH`）跨数据源去重：同一 base asset 跨交易所的条目，若上市时间相近（约 72 小时内）则合并为一条（取最早 `listed_at`）；若相差较远（疑似同名不同项目），则各自保留为独立行，不强行合并。
- **同名碰撞限制**：若两个不同的项目恰好使用相同的 ticker（base asset 相同），当前实现无法区分，会被合并处理。此为设计边界，不影响主流币种。

#### 行情富化

- 每条新币上新记录复用 `get_realtime_quote` 接口尝试获取实时行情（价格、涨跌幅等）。
- **presence-only**：若该交易对流动性不足或行情拉取失败，则静默跳过行情字段（omit），不回填 0 或 N/A，避免误导性数据。

#### 与现有"新币与上新动态"叙事段的关系

`new_listings` 结构化字段（本节）与报告中"新币与上新动态"叙事段（见 8.3）**并存、互补**：

| | `new_listings` 结构化字段 | "新币与上新动态"叙事段 |
|---|---|---|
| 来源 | 交易所 API 直接查询 | LLM 综合新闻检索 |
| 内容 | 上新时间、行情等结构化数据 | 市场背景、上下文分析 |
| 可信度 | 事实（交易所公布数据） | 依赖新闻质量，严格不编造 |
| 覆盖范围 | 窗口内交易所上线记录 | 新闻中提及的热点币种 |

结构化表格提供**事实依据**，叙事段提供**市场上下文**；两者结合可更完整地理解近期上新动态。

### 8.6 永续情绪聚合

`CRYPTO_DERIVATIVES_ENABLED=true`（默认开启）时，crypto 大盘复盘对 `CRYPTO_MARKET_REVIEW_SYMBOLS` 篮子并发拉取各币 OKX 永续指标并聚合（presence-only）：

- OI 加权平均资金费率、总未平仓量(USD)、OI 加权多空比（全市场/大户）、按 |资金费率| 降序的 top5 明细（明细含各币多空比）。
- 注入复盘 prompt（"## 加密永续情绪"事实块）+ 结构化 `market_review_payload.perp_sentiment` + Web 复盘视图「加密永续情绪」卡片。
- 复用 `CRYPTO_DERIVATIVES_ENABLED` 与复盘篮子，无新增配置；非 crypto/禁用/无数据 → 不聚合，复盘照常。

## 加密市场宏观指标

加密货币大盘复盘在 `CRYPTO_MARKET_INDICATORS_ENABLED=true`（默认开启）时，额外抓取以下宏观指标并写入复盘 payload：

| 指标 | 来源 | payload 字段 |
|---|---|---|
| BTC / ETH 主导率 | CoinGecko `/global` | `btc_dominance` / `eth_dominance` |
| 加密总市值（含 24h 变化） | CoinGecko `/global` | `total_market_cap_usd` / `market_cap_change_24h_pct` |
| 总成交额（24h） | CoinGecko `/global` | `total_volume_usd` |
| 恐贪指数 | alternative.me `/fng` | `fear_greed: {value, classification, timestamp}` |

- 两源均免费、无需 API Key；超时/重试复用 `CRYPTO_FETCH_TIMEOUT_SECONDS` / `CRYPTO_FETCH_MAX_RETRIES`。
- **presence-only**：任一源或字段失败即省略，不塞 0、不编造；两源全失败时 payload 不含 `market_indicators`。
- 指标值同时注入复盘 prompt（"## 加密市场宏观指标"事实块），供 LLM 叙事引用市场情绪与结构。
- 指标条与复盘 prompt 展示 BTC/ETH 主导率、加密总市值（含 24h 变化）与恐贪指数；总成交额（`total_volume_usd`）随 payload 提供，暂未在指标条单独展示。
- 仅 crypto 大盘复盘触发；A股/港股/美股不受影响。

## 加密永续合约指标

`CRYPTO_DERIVATIVES_ENABLED=true`（默认开启）时，分析加密**现货**标的会并发拉取对应 **OKX 永续**（`BASE-QUOTE-SWAP`，仅 USDT/USDC 线性永续）的合约指标，注入分析 prompt 的「合约市场指标」块：

| 指标 | 来源 | 含义 |
|---|---|---|
| 资金费率 | OKX `/public/funding-rate` | 正=多头付费 / 负=空头付费（约 8h 结算） |
| 标记价 | OKX `/public/mark-price` | 与现货价对比看基差 |
| 未平仓量(OI / USD) | OKX `/public/open-interest` | 持仓规模与杠杆活跃度 |
| 多空比(全市场) | OKX `/rubik/stat/contracts/long-short-account-ratio`（`ccy` 维度） | 全市场（散户）账户净多/净空，>1 偏多 |
| 多空比(大户) | OKX `/rubik/stat/contracts/long-short-account-ratio-contract-top-trader`（`instId` 维度） | 大户账户多空比，与全市场对比看分歧 |

- 免费、无需 API Key；五路并发；超时/重试复用 `CRYPTO_FETCH_TIMEOUT_SECONDS` / `CRYPTO_FETCH_MAX_RETRIES`。
- **presence-only**：任一指标失败即省略；全失败 / 非 USDT(USDC) 计价 / 禁用 → 不注入，分析照常。
- 每分析一个加密标的额外拉取一次；注入 LLM 分析 prompt，并结构化透出到报告 API/Web（见下文「报告透出」）。
- 仅加密标的触发；A股/港股/美股不受影响。
- 后续子项目（未做）：Binance fapi 备援（本环境 451）。

### 报告透出

永续指标除注入分析 prompt 外，也会 presence-only 透出到结构化报告：

- API：`AnalysisReport.details.crypto_contracts`（字段 `funding_rate` / `mark_price` / `open_interest` / `open_interest_usd` / `long_short_ratio` / `long_short_ratio_top` / `source`，缺省省略）。复盘 `market_review_payload.perp_sentiment` 同步新增聚合字段 `avg_long_short_ratio` / `avg_long_short_ratio_top` 与 per-coin `long_short_ratio`（presence-only，经透传随 API 返回）。
- Web：分析报告页「合约市场指标」卡片（`ReportCryptoMetrics`）与复盘「加密永续情绪」卡片，均已同步渲染多空比（全市场/大户），逐字段 presence-only，无数据则整卡不渲染。
- 依赖 `SAVE_CONTEXT_SNAPSHOT=true`（默认开）——透出从持久化的分析快照读取；关闭快照持久化时不透出。与 `CRYPTO_DERIVATIVES_ENABLED` 联动：上游关闭则无数据可透出。

## 9. 已知限制（后续阶段）

以下为当前**非目标**，留待后续阶段（需另立设计）：

- **日内 / 高频实时监控与触发式告警**（WebSocket 行情流）。
- **杠杆**（perp 标的分析与回测已支持；杠杆仍不做）。
- 以 AICoin 等聚合站为数据源（当前三所现货已覆盖主流交易量）。
- 结构化新币上新的同名 ticker 碰撞消歧（当前按 base asset 去重，跨项目同名不做区分）。
