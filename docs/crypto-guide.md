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

- 回测 forward-return 直接按日期序取后续 K 线行数，不假设交易日历，**crypto 天然兼容**。
- 语义提示：`eval_window_days` 对 crypto 指**自然日**（每日均有 K 线），对股票指**交易日**。因此同样的窗口天数，crypto 标的覆盖的 K 线根数通常多于股票（周末/节假日 crypto 仍有数据）。

## 7. API 用法

crypto 代码含 `/`，调用带路径参数的接口时，可直接传原始斜杠或 URL 编码（`%2F`）：

```
GET /api/v1/stocks/BTC/USDT/quote
GET /api/v1/stocks/BTC%2FUSDT/quote        # 等价
GET /api/v1/stocks/BTC%2FUSDT/history?days=30
```

## 8. 已知限制（后续阶段）

以下为当前**非目标**，留待后续阶段（需另立设计）：

- crypto **大盘复盘**（MarketAnalyzer 当前限 A股/港股/美股）。
- **日内 / 高频实时监控与触发式告警**（WebSocket 行情流）。
- **合约 / 永续 / 杠杆**（当前仅现货）。
- crypto 专属大盘指标（BTC 主导率 / 总市值 / 恐贪指数，需引入新数据源）。
- 以 AICoin 等聚合站为数据源（当前三所现货已覆盖主流交易量）。
