# 盘中/分钟级回测（crypto MVP）

> **适用版本**：feat/intraday-backtest 分支（合入 main 后生效）  
> **范围**：仅 crypto 现货与永续合约标的；A 股、美股暂不支持；港股不在计划内。

---

## 1. 能力概述

盘中/分钟级回测在现有日线 AI 建议基础上，将目标价/止损价/到期验证窗口映射到分钟价格路径，对每条 AI 操作建议做**前向验证**：

- 评估时间轴从"若干交易日"精细化为"若干分钟 bar"，沿用与日线相同的日历窗口天数（`eval_window_days`）。
- 日线候选集来源不变：Chain A（操作建议/PnL + 永续杠杆情景）。
- 新增结果字段：`bar_interval`（所用粒度）与 `first_hit_bar_index`（首次命中的 bar 序号）。
- 行隔离通过 `engine_version` TAG 实现，不污染既有日线回测记录。

---

## 2. interval 语义与默认值

| interval | 每 bar 分钟数 | 说明 |
|----------|-------------|------|
| `1d`     | —（日线）    | **默认**，走现有日线路径，行为与现状一致 |
| `1m`     | 1           | 分钟级（极高精度，数据量大） |
| `5m`     | 5           | **推荐分钟粒度**，兼顾精度与数据量 |
| `15m`    | 15          | 中等粒度 |
| `1h`     | 60          | 小时级 |

**默认值为 `1d`（日线）**，不配置 / 不传参时行为与上一版本完全一致，不引入任何变化。

若需改变默认 bar 粒度（用于后台调度），设置：

```env
CRYPTO_INTRADAY_BACKTEST_INTERVAL=5m
```

---

## 3. 同日历窗口分钟路径

分钟回测沿用日线的 `eval_window_days` 配置，将天数换算为 bar 数：

```
window_bar_count = eval_window_days × bars_per_day(interval)
```

其中 `bars_per_day` 按 crypto 7×24 计算（1 天 = 1440 分钟）：

| interval | bars_per_day |
|----------|-------------|
| `1m`     | 1440        |
| `5m`     | 288         |
| `15m`    | 96          |
| `1h`     | 24          |

入场价取 `analysis_date` 当日的**日线收盘价**（与日线路径一致，代表 AI 建议成立时点）。分钟窗口起点为 `analysis_date + 1 day` 00:00 UTC（crypto 日线 bar 收盘后第一根分钟 bar），向前延伸 `eval_window_days` 天。

---

## 4. 行隔离（engine_version TAG）与新列

### 4.1 engine_version 标签构建规则

`engine_version` 字段通过以下规则自动生成，保证不同粒度的回测结果**行隔离**，互不覆盖：

```
engine_version = {base}[-{interval}][-x{leverage}]
```

示例：

| 场景 | engine_version |
|------|---------------|
| 日线，无杠杆 | `v1` |
| 5m 分钟，无杠杆 | `v1-5m` |
| 5m 分钟，3× 杠杆 | `v1-5m-x3` |
| 日线，3× 杠杆 | `v1-x3` |

### 4.2 新增数据库列

| 列名 | 类型 | 说明 |
|------|------|------|
| `bar_interval` | TEXT | 本条回测所用粒度，如 `1d` / `5m` |
| `first_hit_bar_index` | INTEGER | 首次命中目标/止损的 bar 序号（仅分钟路径有效；日线路径此列为 NULL，仍使用 `first_hit_trading_days`） |

数据库迁移为 guarded ALTER（若列已存在则跳过），对存量数据无影响。

---

## 5. 成本开关（默认关闭 = 理想化无成本）

默认情况下，分钟回测**不扣减手续费与滑点**（`fee_bps=0, slippage_bps=0`），结果为理想化收益。

如需模拟真实成本，在 `.env` 中配置（基点，1bp = 0.01%）：

```env
# 单边手续费（基点），一进一出计 2 次
CRYPTO_INTRADAY_BACKTEST_FEE_BPS=5

# 单边滑点（基点），一进一出计 2 次
CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS=2
```

成本后处理公式：

```
net_return = gross_return − 2 × (fee_bps + slippage_bps) / 100
```

---

## 6. Opt-in 定时调度开关（默认关闭）

后台定时分钟回测任务**默认关闭**，不影响手动触发。若需自动调度：

```env
# 开启后台定时任务
INTRADAY_BACKTEST_ENABLED=true

# 调度间隔（分钟），默认 60
INTRADAY_BACKTEST_SCHEDULE_MINUTES=60
```

**注意**：`INTRADAY_BACKTEST_ENABLED=false`（默认）时，手动触发（CLI/API）不受影响，仍可正常使用。

---

## 7. 缓存

分钟 K 线数据使用内存缓存，TTL 由以下配置控制：

```env
# 分钟数据缓存 TTL（秒），默认 900（15 分钟）；0 = 禁用缓存
CRYPTO_INTRADAY_MINUTE_CACHE_TTL_S=900
```

缓存键为 `(stock_code, interval, days)`，缓存进程内有效，重启后清空。

---

## 8. 使用方法

### 8.1 CLI

```bash
# 日线回测（默认，与现状一致）
python main.py --backtest

# 5m 分钟回测
python main.py --backtest --backtest-interval 5m

# 1h 分钟回测
python main.py --backtest --backtest-interval 1h

# 对指定标的跑 15m 回测
python main.py --backtest --backtest-interval 15m --stocks BTCUSDT,ETHUSDT
```

### 8.2 API

三个端点均支持 `interval` 查询参数（默认 `1d`）：

```
# 触发回测
POST /api/v1/backtest/run
  Body: { "interval": "5m" }

# 查询结果列表
GET /api/v1/backtest/results?interval=5m

# 查询汇总绩效
GET /api/v1/backtest/performance?interval=5m
```

响应中包含新字段：

```json
{
  "bar_interval": "5m",
  "first_hit_bar_index": 42,
  ...
}
```

日线路径（`interval=1d`）响应中 `first_hit_bar_index` 为 `null`，`first_hit_trading_days` 保持原有语义。

### 8.3 Web 回测页

回测页顶部筛选区新增 **interval 选择器**（下拉框），可选：`1d（日线）/ 1m / 5m / 15m / 1h`，默认 `1d`。

- 选择 `1d` 时，结果表格与图表与现状一致，不展示 `bar_interval` / `first_hit_bar_index` 列。
- 选择非 `1d` 时，展示新列 `bar_interval` 与 `first_hit_bar_index`（bar 序号）。

---

## 9. 数据深度与限制

### 9.1 数据来源

分钟 K 线数据通过 Binance 公开接口获取（无需 API Key），支持多页翻页拉取：

- 单次请求上限 1000 根 bar；历史窗口过长时自动分页。
- 数据深度：Binance 保留近几个月至数年的 1m/5m/15m 数据，但**极远历史（1m 粒度数年前）可能不可用**。
- 建议 `eval_window_days ≤ 30`（5m 约 8640 根 bar），避免历史数据缺失。

### 9.2 Crypto-only 限制

- **当前仅支持 crypto 标的**（Binance/OKX/Coinbase 现货 + 永续合约）。
- A 股、美股后续考虑接入，港股不在计划内。
- 非 crypto 标的在分钟路径下会被跳过（`skipped_unsupported` 计数），日线路径不受影响。

### 9.3 历史窗口锚点

分钟回测的**入场价**取 `analysis_date` 当日日线收盘价（与日线路径语义一致）。分钟**价格路径**起点为 `analysis_date + 1 day` 00:00 UTC（即 crypto 日线 bar 收盘后的第一根分钟 bar 所在时刻），向未来延伸 `eval_window_days` 天。如果分析日期较早且所需分钟数据不可用，该条记录会被跳过并记录错误日志。

---

## 10. 配置项汇总

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `CRYPTO_INTRADAY_BACKTEST_INTERVAL` | `5m` | 后台调度的默认 bar 粒度 |
| `CRYPTO_INTRADAY_MINUTE_CACHE_TTL_S` | `900` | 分钟数据缓存 TTL（秒），0 禁用 |
| `CRYPTO_INTRADAY_BACKTEST_FEE_BPS` | `0` | 单边手续费（基点，默认 0 理想化） |
| `CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS` | `0` | 单边滑点（基点，默认 0 理想化） |
| `INTRADAY_BACKTEST_ENABLED` | `false` | 是否启用后台定时任务 |
| `INTRADAY_BACKTEST_SCHEDULE_MINUTES` | `60` | 后台调度间隔（分钟） |

所有配置项均有合理默认值，**不配置即可运行**，现有行为不变。

---

## 11. 回滚说明

如需回退到分钟回测前的状态，无需任何代码变更，只需确保：

1. 不传 `--backtest-interval` 参数（CLI 默认 `1d`）
2. API 不传 `interval` 参数（默认 `1d`）
3. Web 回测页保持 interval 选择器为 `1d`（默认）
4. `INTRADAY_BACKTEST_ENABLED=false`（默认关闭定时调度）
5. `CRYPTO_INTRADAY_BACKTEST_FEE_BPS=0` + `CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS=0`（默认无成本）

上述所有设置均为默认值，**无需显式配置**。若有已写入数据库的分钟回测记录，可通过 `engine_version` 过滤区分（非 `v1` 前缀的行），不影响日线回测记录。
