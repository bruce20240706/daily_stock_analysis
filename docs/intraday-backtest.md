# 盘中/分钟级回测（crypto + A股 + 美股）

> **范围**：crypto 现货与永续合约、A 股沪深（北交 best-effort）、美股个股（yfinance 免 key）、港股个股（akshare 东财 + yfinance 兜底，best-effort）。  
> **专章**：A 股见 [§10](#10-a-股盘中分钟级回测沪深为主北交-best-effort)、美股见 [§11](#11-美股盘中分钟级回测)、港股见 [§12](#12-港股盘中分钟级回测)，均复用本文全部引擎/服务/API/Web/CLI，仅做市场化适配。

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

其中 `bars_per_day` **按市场查表**（`MARKET_TRADING_MINUTES`，**ceil 取整**计入会话末尾不足整段的半根）；crypto 7×24（1440 分钟），A 股每日 240 分钟（见 [§10.2](#102-bars_per_day市场化)），美股常规时段 390 分钟（见 [§11.2](#112-bars_per_day与-1h-半根)），港股 330 分钟（09:30–12:00 + 13:00–16:00，午休不计，见 [§12.2](#122-bars_per_day港股)）：

| interval | crypto（1440/日） | A股（240/日） | 美股（390/日） | 港股（330/日） |
|----------|------------------|---------------|----------------|----------------|
| `1m`     | 1440             | 240           | 390（美股不支持，见 §11）| 330（港股 1m fail-closed，见 §12）|
| `5m`     | 288              | 48            | 78             | 66             |
| `15m`    | 96               | 16            | 26             | 22             |
| `1h`     | 24               | 4             | 7（ceil(390/60)，末根半根）| 6（ceil(330/60)）|

市场由标的代码自动判定（crypto/perp → crypto；沪深/北交 → cn；美股个股 → us；港股个股 → hk）。ceil 仅对有余数的 (市场,粒度) 生效——美股 1h（ceil(390/60)=7，末根半根）、港股 1h（ceil(330/60)=6）；crypto/A股各粒度整除，值不变。

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

成本后处理公式（手续费/滑点及港股印花税对称双边，A股印花税卖出单边）：

```
net_return = gross_return
           − 2 × (fee_bps + slippage_bps + hk_stamp_bps) / 100   # 对称双边(含港股印花税)
           − 1 × ashare_stamp_bps / 100                          # A股卖出单边
```

**A股卖出印花税（单边，opt-in）：** A股现行印花税为**卖出单边 5bps（0.05%）**。设 `ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS=5` 后，仅 A股盘中回测的多头(long)出场会额外扣一次卖出税；crypto/美股/cash/日线一律不征。注意 `fee/slippage` 是跨市场共享的对称佣金分量（默认 0），A股真实总成本需**同时**设 `CRYPTO_INTRADAY_BACKTEST_FEE_BPS/SLIPPAGE_BPS`。该 knob 也可在 Web 设置页 Backtest 分类直接调整。

**港股双边印花税（opt-in）：** HK 现行印花税为**买卖双边各 10bps（0.1%）**。设 `HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS=10` 后，仅港股盘中回测的**成交**标的按买卖双边各计一次（×2）；crypto/A股/美股/cash/日线一律不征。区别于 A股单边。佣金/滑点仍走跨市场共享 `CRYPTO_INTRADAY_BACKTEST_FEE_BPS/SLIPPAGE_BPS`（默认 0）。该 knob 也可在 Web 设置页 Backtest 分类调整。

**成本仅对确有成交计征**：cash 仓（无买卖成交）不扣 fee/slippage/印花税；仅 long 与 perp short（确有成交）计 round-trip 成本。

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
python main.py --backtest --backtest-interval 15m --backtest-code BTCUSDT
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

### 9.2 支持市场

- **crypto**（Binance/OKX/Coinbase 现货 + 永续合约）：本节及上文均以 crypto 为例。
- **A 股**（沪深为主，北交 best-effort）：见 [§10](#10-a-股盘中分钟级回测沪深为主北交-best-effort)。
- **美股**（个股，yfinance 免 key）：见 [§11](#11-美股盘中分钟级回测)。
- **港股**（个股，best-effort）：见 [§12](#12-港股盘中分钟级回测)。

### 9.3 历史窗口锚点

分钟回测的**入场价**取 `analysis_date` 当日日线收盘价（与日线路径语义一致）。分钟**价格路径**起点为 `analysis_date + 1 day` 00:00 UTC（即 crypto 日线 bar 收盘后的第一根分钟 bar 所在时刻），向未来延伸 `eval_window_days` 天。如果分析日期较早且所需分钟数据不可用，该条记录会被跳过并记录错误日志。

### 9.4 链路A end_date 右边界核验（只读结论，2026-06-26）

链路A 分钟取数路径（`src/services/backtest_service.py`）已显式计算并下传 `end_date=_window_end_date.isoformat()`（`_window_end_date = _minute_window_start + timedelta(days=_end_offset)`）：

- **A股/美股**：`end_date` 由 `tushare_fetcher.get_intraday_data`、`akshare_fetcher.get_intraday_data`、`yfinance_fetcher.get_intraday_data` 直传源 API，右边界由源端截断，已闭合。
- **crypto**：`binance_fetcher._page_klines` 不接收 `end_date` 参数，但 `limit ≈ days × bars_per_day` 正向翻页封顶右边界——`len(out) >= limit` 即停止翻页，不会越界取到未来数据。
- **引擎二次截断**：`evaluate_single`（链路A 引擎，`src/core/backtest_engine.py`）对传入的 `forward_bars` 执行 `forward_bars[:eval_days]`，再次将评估窗口限制在 `eval_window_days` 根之内。

**结论**：链路A `end_date` 右边界已等价闭合（A股/美股由源 API 截断；crypto 由 `limit` 翻页封顶 + 引擎 `[:eval_days]` 共同受控）。本特性（链路B 窗口正确性）**不改链路A 代码**，以上结论仅作只读核验登记。

---

## 10. A 股盘中/分钟级回测（沪深为主，北交 best-effort）

A 股复用上述全部引擎 / 服务 / API / Web / CLI 能力，仅做市场化适配；**crypto 与日线路径行为不变**。

### 10.1 数据源（Tushare 主源 + akshare 免费兜底）

| 数据源 | 接口 | Token | 说明 |
|--------|------|-------|------|
| Tushare（主源） | `stk_mins`（HTTP Pro） | 需 `TUSHARE_TOKEN` + 积分 | 配置 token 且数据源可用时优先；分钟接口对积分有门槛 |
| akshare（兜底） | `stock_zh_a_hist_min_em`（东财） | 免费、无需 token | Tushare 不可用 / 无 token / 取数失败时自动降级 |

- **不配置 token 也能用**：无 `TUSHARE_TOKEN` 时 Tushare 数据源按可用性探测自动跳过，直接走 akshare 免费兜底。
- interval 词表与 crypto 统一（`1m/5m/15m/1h`），各源内部映射：Tushare `1h→60min`、akshare `1h→'60'`。
- **akshare 不支持历史 `1m`**：东财 `1m`（`period='1'`）走 trends2 接口、仅返回最近约 5 个交易日且忽略 `start/end`，无法锚定回测的历史窗口；故 akshare 对 `1m` 直接抛 `NotImplementedError`（fail-closed，避免静默取回错窗口）。**`1m` A 股回测需配置 Tushare token**；无 token 时 `1m` 不可得（落 `insufficient_data`），`5m/15m/1h` 不受影响。

### 10.2 bars_per_day（市场化）

A 股每个交易日仅两段连续竞价：09:30–11:30 + 13:00–15:00 = **240 分钟**，故 `bars_per_day` 按市场查表（对照表见 [§3](#3-同日历窗口分钟路径)）：`window_bar_count = eval_window_days × bars_per_day(interval, "cn")`，market 由代码自动判定（沪深/北交 → `cn`）。

### 10.3 窗口语义（交易日，分钟流即日历）

- 入场价 = `analysis_date` 当日**日线收盘价**（15:00 收盘，与日线/crypto 路径一致）。
- 分钟窗口起点 = 日线收盘次日（`analysis_date + 1`）；由于分钟流只含交易时段 bar，向前切 `window_bar_count` 根即等价 N 个交易日，**无需交易日历做日期运算**。
- 取数 `end_date` 比 crypto 更宽（`max(N×2, N×3//2 + 14)` 自然日），尽力覆盖周末与长假（春节/国庆约 11 天连续休市），让分钟流切满 N 个交易日。**极端超长停牌窗口仍可能取不满 N 个交易日 → 该条落 `insufficient_data`**（best-effort，非保证）。

### 10.4 用法

与 crypto 完全一致，仅把标的换成 A 股代码：

```bash
# 对贵州茅台跑 5m 分钟回测（回测模式用 --backtest-code 指定单个标的；--stocks 仅用于分析模式）
python main.py --backtest --backtest-interval 5m --backtest-code 600519
```

API / Web 用法同 [§8.2](#82-api) / [§8.3](#83-web-回测页)，`interval` 词表不变。

### 10.5 限制

- **Tushare 分钟接口需积分**：免费账户积分可能不足，此时（除 `1m` 外）自动走 akshare。
- **`1m` 仅 Tushare**：akshare 兜底不支持历史 `1m`（见 [§10.1](#101-数据源tushare-主源--akshare-免费兜底)），无 token 时 `1m` 不可得。
- **akshare 限频/稳定性**：东财免费接口有访问频率限制，长窗口/大批量可能偶发失败（按数据源降级与错误计数处理）。
- **印花税可选建模（默认 0）**：A 股卖出印花税（单边）已由 `ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS` 支持（opt-in，默认 0，仅 cn + long 出场计征；现行 5bps）；佣金等其它分量仍沿用跨市场共享的对称 `fee/slippage`（默认 0）。过户费（沪市）等暂未单独建模。
- **复权基准漂移**：入场价取库内日线收盘（其复权口径以落库时为准），分钟 bar 按 `qfq` 即时拉取，二者复权锚点可能不同步；若窗口内发生除权除息，模拟收益会有偏差。窗口短、无分红配股时影响可忽略。
- **北交所 best-effort**：北交所分钟数据源覆盖不确定，作尽力支持，不保证可得。
- **港股**分钟路径已支持（akshare 东财主 + yfinance 兜底，见 [§12](#12-港股盘中分钟级回测)）；1m fail-closed。

---

## 11. 美股盘中/分钟级回测

美股复用全部引擎 / 服务 / API / Web / CLI 能力，仅做市场化适配；**crypto / A 股 / 日线路径行为不变**。

### 11.1 数据源（yfinance 免 key 单源）

- **yfinance**（Yahoo Finance，免 key、已在仓）是美股分钟唯一源。给 `YfinanceFetcher.get_intraday_data` 经 `yf.download(interval=…, auto_adjust=True)` 取数 → 共享 `normalize_intraday_df`。
- interval 映射：`5m→5m`、`15m→15m`、`1h→60m`。
- 类股（`BRK.B`/`BF.B`）符号自动 `.`→`-`（Yahoo 用连字符 `BRK-B`）。
- yfinance 日线虽支持 A 股/港股，但其**分钟服务美股与港股**；门面已将其在非 (us, hk) 市场排除，港股分钟由 akshare 主 + yfinance 兜底（见 [§12](#12-港股盘中分钟级回测)），A 股分钟仍只用 Tushare/akshare（契约不变）。

### 11.2 bars_per_day 与 1h 半根

美股常规时段 09:30–16:00 ET = **390 分钟**（排除盘前盘后）。`bars_per_day` 用 **ceil**：`5m→78`、`15m→26`、`1h→7`（`ceil(390/60)`，计入末根 15:30–16:00 半根，匹配 yfinance 实际 7 根/日）。

### 11.3 可用 band（关键限制）

yfinance 免费分钟仅近期可得，回测需 `analysis_date` 既够老（forward 窗口走完，受 `min_age_days` 门控）又够新（在 yfinance 窗口内）：

| interval | yfinance 历史窗口 | 实际可用 band | 说明 |
|----------|-------------------|---------------|------|
| `1h`     | 上限 730 天（硬拒，见注）| `[now-725d, now-窗口]` | **推荐**，可用范围最宽 |
| `5m`/`15m` | 上限 60 天（硬拒，见注）| `[now-58d, now-窗口]` | 仅近两月 |

> **注（yfinance 严格 last-N-天边界，真网核验）**：Yahoo Finance 要求请求严格在 last-N-天内；请求恰好 N 天会被硬拒（返回空 → `DataFetchError`）。真网核验：5m 59 天 OK / 60 天 FAIL，1h 729 天 OK / 730 天 FAIL。band 取上限再留 1–4 天余量（5m/15m=58、1h=725），抵消实时 vs 午夜基准偏差与时区漂移。
| `1m`     | ≈ 7 天 + 单请求 ≤8 天 | **不支持** | 与 min_age/窗口缓冲恒冲突 → fail-closed（`NotImplementedError`→`insufficient_data`，不发请求） |

超出窗口的老 `analysis_date` → 取数失败 → 优雅降级 `insufficient_data`（与 A 股取数失败同路径，不加新逻辑）。

### 11.4 用法

```bash
# 对苹果跑 1h 分钟回测(美股推荐 1h,历史最宽)
python main.py --backtest --backtest-interval 1h --backtest-code AAPL
```

API / Web 用法同 [§8.2](#82-api) / [§8.3](#83-web-回测页)，`interval` 词表不变。

### 11.5 限制

- **批量可靠性**：yfinance 抓取 Yahoo，大批量（数百候选）可能限频/瞬断；入口已包 `@retry` 指数退避，仍可能偶发失败 → 该条 `insufficient_data`/error 计数。美股分钟回测建议小批量/手动触发。
- **5m/15m 大窗口受 yfinance band 约束**：取数窗口含周末/节假日缓冲（`max(N*2, N*3//2+14)` 自然日），当 `eval_window_days` 偏大（约 ≥40）时整窗会超出 yfinance 5m/15m 的 58 天可得 band → 整体落 `insufficient_data`。大窗口请改用 `1h`（≈725 天，见 [§11.3](#113-可用-band关键限制)）。
- **成本**：沿用 crypto 对称 `fee/slippage`（默认 0）；美股无印花税（仅极小 SEC/TAF 费），不单独建模。
- **复权基准漂移**：yfinance `auto_adjust=True`，与库内日线收盘入场价的复权锚点可能不同步（同 A 股 §10.5），窗口短/无公司行动时可忽略。
- **仅个股**：美股指数（SPX/DJI 等）无 operation_advice、非回测候选，不支持。

### 11.6 A股/美股分钟数据深度（链路B，2026-06-26）

**行为变更（C2）**：自本特性起，链路B 分钟回测（`signal_backtest_service`）对非 crypto 来源显式下传 `start_date`，将历史窗口锚定到更早起点，解决此前源默认浅窗导致样本量不足的问题。crypto 路径字节级不变。

| 市场 | interval | start_date 锚点 | 说明 |
|------|----------|-----------------|------|
| 美股（us） | 1h | today − `_INTRADAY_MAX_DAYS["us"]["1h"]`（≈725d） | 夹 yfinance band 余量值（上限 730 被硬拒，真网核验） |
| 美股（us） | 5m/15m | today − `_INTRADAY_MAX_DAYS["us"]["5m"]`（≈58d） | 夹 yfinance band 余量值（上限 60 被硬拒，真网核验） |
| A股（cn） | 1h | today − 730d | 见下「核验结果」 |
| A股（cn） | 15m | today − 365d | 见下「核验结果」 |
| A股（cn） | 5m | today − 90d | 见下「核验结果」 |
| A股（cn） | 1m | today − 30d（tushare 独占；无 token 时 akshare fail-closed） | 见下「核验结果」 |
| crypto | 不变 | 字节级不变（仍按 days 估算 limit） | C2 不改 crypto 路径 |

**cn band 在线核验结果（2026-06-29，关沙箱前台 real-network，东财可达，无密钥）**：
- **akshare 路径（无 tushare token 部署的实际 cn 来源）已实测**：东财 `stock_zh_a_hist_min_em` 对深窗**优雅返回可得子集**——`req_start=today−90` 与 `today−365` 返回完全相同的数据（5m 仅东财保留的 ~46 日历天/1511 行，15m 504 行），**绝不报错、不返回错窗**。故 cn band 在 akshare 路径上**安全**（band 偏大无害，东财自身封顶），无需收紧。cn 1m 无 token 时 akshare fail-closed（干净 `DataFetchError`，单股跳过、不拖垮整批）。
- **tushare 路径未验证**：核验环境无 `config.tushare_token`。其 `stk_mins` 单次行数上限风险仍属理论；保守 band（5m=90≈3072 根、1m=30≈5040 根，均在常见 ~8000 行/次上限内）**由构造安全**，留待有 token 环境补验后可放宽。

此变更仅影响链路B 信号可信度批作业（`--signal-backtest-interval`），链路A 盘中回测（`--backtest-interval`）路径不变（见 §9.4）。

---

## 12. 港股盘中/分钟级回测

港股复用全部引擎 / 服务 / API / Web / CLI 能力，仅做市场化适配；**crypto / A 股 / 美股 / 日线路径行为不变**。

### 12.1 数据源（akshare 东财主 + yfinance 兜底）

| 数据源 | 接口 | Token | 说明 |
|--------|------|-------|------|
| akshare（主源） | `stock_hk_hist_min_em`（东财） | 免费、无需 token | 默认主源；5m/15m/1h 可用 |
| yfinance（兜底） | Yahoo Finance（如 `0700.HK`） | 免费、无需 key | akshare 不可用 / 取数失败时自动降级 |

- **不配置密钥即可用**：akshare 与 yfinance 均免 key/token，开箱即用。
- interval 词表与其他市场统一（`1m/5m/15m/1h`），各源内部做 interval 映射。
- **港股 1m fail-closed**：东财 `stock_hk_hist_min_em` 历史窗口受限，无法锚定回测所需历史区间；故 `1m` 直接抛 `NotImplementedError`（fail-closed，避免静默取回错窗口）。**港股 1m 回测不可得**，`5m/15m/1h` 不受影响。
- 路由白名单仅 `[AkshareFetcher, YfinanceFetcher]`；`TushareFetcher` 经路由排除（仅服务 A 股），港股请求不会误走 Tushare。

### 12.2 bars_per_day（港股）

港股每个交易日两段连续竞价：09:30–12:00 + 13:00–16:00 = **330 分钟**（午休 12:00–13:00 及竞价收市不计），`bars_per_day` 按市场查表（对照表见 [§3](#3-同日历窗口分钟路径)）：

| interval | bars_per_day | 备注 |
|----------|-------------|------|
| `5m`     | 66          | |
| `15m`    | 22          | |
| `1h`     | 6           | ceil(330/60)；AM 末根 11:30–12:00 为 30 min 半根，PM 段整时收盘（15:00–16:00） |

### 12.3 可用 band（链路B 信号可信度）

链路B 信号可信度 band（yfinance 要求请求严格在 last-N-天内；请求恰好上限天数被硬拒，真网核验）：

| interval | band | 说明 |
|----------|------|------|
| `5m`/`15m` | 58 天 | yfinance 上限 60 天会被硬拒（真网核验：59 天 OK / 60 天 FAIL），留余量 |
| `1h`     | 725 天 | yfinance 上限 730 天会被硬拒（真网核验：729 天 OK / 730 天 FAIL），留余量（**推荐**，可用范围最宽） |

yfinance 要求请求严格在 last-N-天内（请求恰好上限天数被硬拒，真网核验），故取上限再留 1–2 天余量。

### 12.4 用法

与 crypto/A股/美股 完全一致，把标的换成港股代码（如 `hk00700`）：

```bash
# 对腾讯控股跑 5m 分钟回测（1m 不支持）
python main.py --backtest --backtest-interval 5m --backtest-code hk00700

# 推荐 1h（链路B 可用范围最宽）
python main.py --backtest --backtest-interval 1h --backtest-code hk00700
```

API / Web 用法同 [§8.2](#82-api) / [§8.3](#83-web-回测页)，`interval` 词表不变。

### 12.5 限制

- **1m fail-closed**：港股 1m 数据源限制，不可得（见 [§12.1](#121-数据源akshare-东财主--yfinance-兜底)）。
- **港股双边印花税（opt-in，默认 0）**：港股印花税买卖双边各 10bps，通过 `HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS` 配置（默认 0；设为 10 后按 ×2 双边扣除）；门控 `market==hk` + 确有成交（cash 豁免），cn/us/crypto/日线不征。佣金/滑点仍走跨市场共享 `CRYPTO_INTRADAY_BACKTEST_FEE_BPS/SLIPPAGE_BPS`（默认 0）。`持有/hold` 建议在回测中映射为 long 且按 `entry@start` 全 round-trip 建模，故 both-side 印花税会对 hold 也计买腿——与 A股 knob 同源（A股已对 hold→long 计卖腿），HK 仅幅度翻倍 ×2；此为回测既有约定，非本特性新增。
- **akshare 限频/稳定性**：东财免费接口有访问频率限制，大批量可能偶发失败 → 该条 `insufficient_data`/error 计数，不拖垮整批（best-effort）。
- **仅个股**：港股指数（恒指等）无 operation_advice、非回测候选；ETF / REIT 代码作 best-effort，未单独验证。
- **复权基准漂移**：入场价取库内日线收盘，分钟 bar 按 `qfq` 即时拉取，复权锚点可能不同步（同 §10.5），窗口短/无公司行动时可忽略。
- **band 已通过真网核验（2026-06-30）**：yfinance 5m/15m 上限 60 天被硬拒（59 天 OK），1h 上限 730 天被硬拒（729 天 OK）；band 已收敛为 58/725（留 1–2 天余量），见 [§12.3](#123-可用-band链路b-信号可信度)。

---

## 13. 配置项汇总

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `CRYPTO_INTRADAY_BACKTEST_INTERVAL` | `5m` | 后台调度的默认 bar 粒度 |
| `CRYPTO_INTRADAY_MINUTE_CACHE_TTL_S` | `900` | 分钟数据缓存 TTL（秒），0 禁用 |
| `CRYPTO_INTRADAY_BACKTEST_FEE_BPS` | `0` | 单边手续费（基点，默认 0 理想化） |
| `CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS` | `0` | 单边滑点（基点，默认 0 理想化） |
| `ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS` | `0` | A股卖出单边印花税（基点，默认 0；现行 5；仅 cn+long） |
| `HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS` | `0` | 港股买卖双边印花税（基点，默认 0；现行 10；仅 hk+成交，×2） |
| `INTRADAY_BACKTEST_ENABLED` | `false` | 是否启用后台定时任务 |
| `INTRADAY_BACKTEST_SCHEDULE_MINUTES` | `60` | 后台调度间隔（分钟） |

所有配置项均有合理默认值，**不配置即可运行**，现有行为不变。A 股分钟回测复用 `TUSHARE_TOKEN` 与上述 intraday 配置；A股卖出印花税为可选追加项 `ASHARE_INTRADAY_BACKTEST_STAMP_DUTY_BPS`（默认 0，不配置即字节级现状）；港股双边印花税为可选追加项 `HK_INTRADAY_BACKTEST_STAMP_DUTY_BPS`（opt-in 默认 0，不配置即字节级现状）。

---

## 14. 回滚说明

如需回退到分钟回测前的状态，无需任何代码变更，只需确保：

1. 不传 `--backtest-interval` 参数（CLI 默认 `1d`）
2. API 不传 `interval` 参数（默认 `1d`）
3. Web 回测页保持 interval 选择器为 `1d`（默认）
4. `INTRADAY_BACKTEST_ENABLED=false`（默认关闭定时调度）
5. `CRYPTO_INTRADAY_BACKTEST_FEE_BPS=0` + `CRYPTO_INTRADAY_BACKTEST_SLIPPAGE_BPS=0`（默认无成本）

上述所有设置均为默认值，**无需显式配置**。若有已写入数据库的分钟回测记录，可通过 `engine_version` 过滤区分（非 `v1` 前缀的行），不影响日线回测记录。
