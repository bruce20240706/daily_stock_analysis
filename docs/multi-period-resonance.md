# 多周期 K 线与共振标记

本文档说明 M4-A 引入的两项能力：周/月线本地聚合，以及基于高周期趋势的多周期共振标记。

---

## 概述

- **周/月线**：由日线数据本地重采样得到，无需向数据源额外请求，市场无关、结果确定。
- **多周期共振**：对日线信号（量价规则方向为 `bullish` 或 `bearish`）判断高周期趋势是否同向，结果为三档标记，展示在信号看板行与钻取面板。
- **信号标注仅日视图**：周/月视图不渲染信号 overlay，共振只在最新一根 bar 计算。

---

## 周/月线本地聚合

**实现位置**：`data_provider/resample.py`，函数 `resample_ohlc(df_daily, period)`。

### 聚合口径

| 字段 | 聚合方式 |
|------|----------|
| open | 周期内第一个交易日的开盘价（`first`） |
| high | 周期内最高价（`max`） |
| low | 周期内最低价（`min`） |
| close | 周期内最后一个交易日的收盘价（`last`） |
| volume | 周期内成交量之和（`sum`） |
| amount | 周期内成交额之和（`sum`） |
| pct_chg | 本期 close 对比上期 close 的涨跌幅（重算，首根置 NaN） |
| date | 该周期内**最后一个交易日**的日期（非日历末尾） |

### 日历分组（已知近似）

分组基于 pandas `to_period('W')` / `to_period('M')` 日历边界，而非交易所官方周/月 K 线分组逻辑。这意味着：

- 每周以自然周日历（周一至周日）为边界，每月以自然月为边界。
- 当某周/某月的第一个或最后一个交易日不在日历边界时，bar 的归属与交易所官方 K 线可能出现 1–2 日的差异。
- 此为 v1 已知近似，已在已知局限一节说明，不影响趋势判断的稳定性。

### 均线

均线（MA5/MA10/MA20）在聚合后由 `data_provider/base.py` 的 `attach_ma_indicators` 统一计算（`rolling min_periods=1`），与日线口径一致，不在 history 响应中透出（由 klinecharts 客户端在前端自算）。共振趋势判定内部复用同一个 `attach_ma_indicators`。

### 抓取深度

`src/services/stock_service.py` 中，`get_history_data` 对不同周期的抓取策略（**此表描述 `/history` API 即前端周期切换路径**；共振内部深抓始终走 `period="daily"`，不经过 weekly/monthly warmup 分支，见下文"取数与稳定性"）：

| 周期 | 实际抓取日线天数 | warmup 天数 |
|------|----------------|------------|
| daily | `days`（原逻辑不变） | 0 |
| weekly | `min(days + 200, 3650)` | 200 |
| monthly | `min(days + 800, 3650)` | 800 |

聚合后裁到展示窗口 `ceil(days / 7)`（周）或 `ceil(days / 30)`（月）根。

---

## 多周期共振判定

**实现位置**：`src/services/multi_period_resonance.py`。

### 高周期趋势判定（`period_trend`）

取高周期帧最后一根 bar 的 MA5/MA10/MA20 与收盘价：

| 趋势 | 条件 |
|------|------|
| `bullish` | MA5 > MA10 > MA20 **且** close ≥ MA20 |
| `bearish` | MA5 < MA10 < MA20 **且** close ≤ MA20 |
| `neutral` | 均线纠缠、收盘未确认门、MA 为 NaN / 历史不足 |

无阈值参数。收盘确认门（close vs MA20）是必要条件，防止均线排列已形成但价格仍在 MA20 另一侧时误判趋势。

### 共振档位（`resonance_level`）

输入：日线信号方向（`bullish` / `bearish`）+ 周线趋势 + 月线趋势。

| 条件 | 档位 |
|------|------|
| 信号方向为中性 / `neutral` / `None` | `none` |
| 周线趋势与信号方向不同向 | `none`（**周线门控**） |
| 周线同向、月线也同向 | `weekly_monthly`（周月双共振） |
| 周线同向、月线不同向或中性 | `weekly`（周线共振） |

周线是门控层，月线是加强层。即便月线同向，周线不同向也不输出共振。

### 核心入口（`resonance_from_daily`）

`resonance_from_daily(df_daily, signal_direction) -> str`：接受日线帧和方向，内部调 `resample_ohlc` + `attach_ma_indicators` + `period_trend` + `resonance_level` 完成全流程，返回 `none / weekly / weekly_monthly`。

---

## 取数与稳定性

### 按方向门控的独立深抓

共振计算只在规则方向为 `bullish` 或 `bearish` 时执行（`signal_board_service.py`，常量 `RESONANCE_DAILY_DAYS = 750`）：

```
if rule_dir in ("bullish", "bearish"):
    deep = service.get_history_data(code, period="daily", days=750)
    resonance = resonance_from_daily(pd.DataFrame(deep_rows), rule_dir)
```

- `hold` / 中性 / 信号不可用：直接返回 `none`，零额外网络开销。
- 共振计算异常（抓取失败、数据不足）：降级为 `none`，不影响信号主流程。

### 与 M3 信号路径的关系

M3 信号抓取（日历窗口取数 + `get_daily_data`）与共振深抓（`RESONANCE_DAILY_DAYS=750` 固定深度）是独立请求，不共享同一份日线帧。这是刻意设计：`get_daily_data` 为日历窗口语义，无法通过简单 `tail` 等价替换，共振需要稳定的固定深度以保证 MA 暖机充分。**M3 信号引擎窗口与逻辑零改动**。

### 透传路径

共振在 `build_signals_for_code` 中一次计算，写入 `payload["resonance"]`，由两路共享：

- **`/api/v1/stocks/{code}/signals`**：通过 `SignalsResponse(**bs.signals_payload)` splat 透传，钻取面板头部徽标读取。
- **`/api/v1/signals/board`**：通过 `_entry_from_board_signals` → `payload.get("resonance", "none")` 写入看板行，看板行徽标读取。

---

## 前端展示

### K 线周期切换

`KLineChartPanel` 支持日/周/月三档切换。切换周期时整图 dispose + init 重建（klinecharts 命令式 API 的限制），不支持增量更新。

前端按周期传入的 `days` 参考值：

| 周期 | days |
|------|------|
| daily | 120 |
| weekly | 365 |
| monthly | 1825 |

### 信号 overlay

量价信号标注（买卖点 overlay）**仅在日视图渲染**，周视图与月视图不请求 `/signals`，不显示信号标记。共振徽标同样只对日视图信号有意义。

### 共振徽标

| 档位 | 看板行 / 钻取面板显示 |
|------|----------------------|
| `none` | 不显示 |
| `weekly` | 共振·周 |
| `weekly_monthly` | 共振·周月 |

---

## 配置与兼容性

本功能**无新增运行时配置项**（无新增 `.env` / `config_registry` / Settings 页条目）：

- 共振始终计算，无开关。
- `RESONANCE_DAILY_DAYS = 750` 为代码内部常量（非 env）。共振深抓走 `get_history_data(period="daily", days=750)` —— 因为是 daily 周期，不叠加 warmup；`get_daily_data` 以约 750×2≈1500 日历日窗口取数（约千余交易日），`resonance_from_daily` 再在内部重采样到周/月线。约 1500 日历日 ≈ 约 50 个自然月，远超月线 MA20 所需的 20 根。注意：`/history` 的 weekly/monthly warmup（200/800）是前端图表那条路径所用，与共振的 daily 深抓路径无关。

**兼容性**：

- `GET /api/v1/stocks/{code}/history` 的 `days` 上限由 365 放宽至 1825，纯加宽、向后兼容；日线默认值（120）不变，旧客户端仍正常工作。
- `resonance` 字段为追加字段，旧客户端不读取时无影响。
- M3 信号路径、`signal_stats` 回测管线、现有回测/胜率数据**零改动**。

---

## 已知局限（v1）

| 局限 | 说明 |
|------|------|
| 日历分组近似 | 基于 pandas 自然周/月边界，与交易所官方周/月 K 线可能有 1–2 日边界差异 |
| 月线根数有限 | `days=1825` 上限约对应 60 根月线，历史更长的标的无法展示更早月线 |
| 共振只在最新 bar | 不提供历史逐根共振序列，不支持"历史上哪些时点出现过共振" |
| 周/月线无自身回测 | 共振复用 M3 日线可信度 + 高周期趋势方向，周/月线不产自己的 signal_stats / 胜率 |
| period 切换整图重建 | klinecharts 命令式 API 限制，切换周期时整图 dispose + init，非增量更新 |
| 深抓与 M3 信号帧独立 | 共振深抓（750 天固定深度）与 M3 信号日历窗口取数是两次独立请求，不共享 |
