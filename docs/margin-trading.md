# 融资融券（Margin Trading）呈现

M4-B-2 在报告中新增「融资融券」section，确定性呈现 A 股个股最新交易日的融资融券快照。

## 数据来源

- 免 token akshare 沪深明细：`stock_margin_detail_sse(date=YYYYMMDD)`（沪，代码列「标的证券代码」）、`stock_margin_detail_szse(date=YYYYMMDD)`（深，代码列「证券代码」）。两接口按日返回全市场明细。
- 交易所路由（allow-list）：`6xx → SSE`、`0xx/3xx → SZSE`；北交所(4/8/9)、ETF(5/1)、B 股(9/2) → `not_supported`（不落端点）。

## 字段

| 字段 | 含义 | 单位 |
| --- | --- | --- |
| `financing_balance` | 融资余额 | 元 |
| `financing_buy` | 融资买入额 | 元 |
| `short_volume` | 融券余量 | 股 |
| `trade_date` | 实际命中交易日 | YYYYMMDD |
| `exchange` | 交易所 | SSE / SZSE |

全部 Optional / presence-only / None-tolerant。

## 门控与语义

- **仅 A 股**（沪深主板/科创/创业）；港股/美股/crypto/ETF/北交所无该 section。
- **presence-only**：margin 块 status ∈ {ok, partial} 才出现；否则 `data_perspective.margin_trading` 为 None、两条 notification 渲染路径不渲染该块。
- **最新交易日**：smart-start（最近工作日）+ 有界回退 ≤3 个工作日候选取首个非空（非精确交易日历，节假日不建模）；命中更旧日 → status=`partial`，`trade_date` 始终随余额一起显示以暴露陈旧度。
- **fail-open**：抓取异常/空数据不抛、不阻断主分析流程。
- **有界 memo**：按 (exchange, date) 缓存非空全市场明细，LRU 上限 4，仅缓存非空。

## 关键分歧（与资金面 capital_flow 不同，reviewer 必查）

融资融券**仅呈现**：不喂 LLM prompt、不进 `decision_stability`、不产生任何 bias、对决策完全只读。
（capital_flow 则相反：喂 prompt 且驱动 post-LLM 降级。）

## 不动的既有行为

资金面（capital_flow）/龙虎榜、主力资金流 prompt 与降级、decision_stability、换手率/量比/筹码 既有 section 全部零改动。

## v1 已知局限

- 仅最新交易日快照，无趋势/多日序列/占比衍生。
- 有界回退非精确日历；长假/晚发布可能命中数日前快照（以 `trade_date` + `partial` 暴露）。
- 北向 / HSGT 个股数据 2024 起被交易所停更，无源可纳入（永久局限）。
- 无专用 Web 组件（同 capital_flow/chip/volume，经 notification markdown + 报告 payload 呈现）。
- 配置：复用 `FUNDAMENTAL_FETCH_TIMEOUT_SECONDS` + A 股门控，零新增 key。
