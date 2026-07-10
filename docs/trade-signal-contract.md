# TradeSignal 契约

canonical 可执行信号契约。定义在 `src/schemas/trade_signal.py`,只读构造器在 `src/services/trade_signal_builder.py`。

设计文档:`docs/superpowers/specs/2026-07-10-trade-signal-contract-design.md`

## 1. 当前状态:零接线

`TradeSignal` 目前**不被任何 runtime 路径消费**。它不接 API、不接报告、不接通知、不落库、不加配置项、不动前端。

`tests/test_trade_signal_contract_locks.py::test_trade_signal_import_allowlist` 用 import 白名单强制这一点:全仓非测试 `.py` 中提及 `trade_signal` 的模块必须 ⊆

```
src/schemas/trade_signal.py
src/services/trade_signal_builder.py
```

**接线前先读第 2 节。** 修改白名单是一个显式、可 diff、可审计的动作。

## 2. 呈现边界(合规约束)

当前合规定位是 **(a) 自用 / 内部**(`docs/strategy-actionable-signal-system.md` §3,2026-07-03 拍板,可升级)。在此档下:

1. `TradeSignal` 的任何用户可见渲染必须以「**分析结论**」措辞出现,**不得**表述为「操作指令」。
2. **不得**为 `TradeSignal` 新增任何对外信号分发 / 推送渠道。既有 `src/notification.py` 已被战略 §3 判定为「自用推送,不构成对外分发」,不在此限。
3. 升级到 (b) 持牌 或 (c) 教育免责档之前,以上两条持续生效。

Inc 0 靠「压根不呈现」满足这条边界。Inc 4 接线时,本节即为呈现规则。

## 3. 字段

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `code` | `str` | 标的代码 |
| `market` | `"cn" \| "hk" \| "us" \| "crypto"` | 小写归一(边界处 `.strip().lower()`) |
| `signal_type` | `str` | 自由字符串;拒绝保留哨兵 `"__baseline__"` |
| `interval` | `"1d" \| "1m" \| "5m" \| "15m" \| "1h"` | 与 `SUPPORTED_INTERVALS` 同集合 |
| `horizon_bars` | `int > 0` | 与 `signal_stats.horizon` 同量纲;`evidence` 靠它定位统计桶 |
| `as_of` | `str` | 完整 ISO-8601 datetime(必须含 `T`);日线写 `T00:00:00` |
| `source` | `"rule" \| "llm"` | 由构造器固定 |
| `direction` | `"long" \| "short"` | 持仓方向。观望 / 无信号 → 不产生对象 |
| `entry_zone` | `PriceZone{low, high}` | 入场区间;规则路径退化为一点 |
| `stop` | `float > 0` | 已入场后的止损 |
| `targets` | `list[float]`,非空、严格单调 | 多个止盈目标 |
| `position_size` | `Optional[float] > 0` | 权益比例;允许 > 1 表示杠杆。`None` = 尚未定量 |
| `confidence` | `"high" \| "medium" \| "low"` | 定性判据;与 `SignalMarker.confidence` 同 token |
| `invalidation` | `Invalidation{price, valid_until, note}` | 信号本身作废的条件,至少一项非空 |
| `action` | `Optional[DecisionAction]` | 八态投影,不新增词表 |
| `evidence` | `Optional[SignalEvidence]` | 样本外统计。`None` = 无历史统计路径 |
| `risk_reward` | `float`(只读 `@property`) | 取最差入场:`long` 用 `zone.high`、`short` 用 `zone.low` |

方向感知的排序不变式:

```
long : stop < entry_zone.low <= entry_zone.high < targets[0] < targets[1] < ...
short: stop > entry_zone.high >= entry_zone.low > targets[0] > targets[1] > ...
```

`invalidation.price` 的方向语义由 `direction` 决定:`long` → 收盘价 ≤ `price` 即失效;`short` → 收盘价 ≥ `price` 即失效。

## 4. canonical ↔ 遗留投影映射表

`TradeSignal` 是 canonical。以下四种是**遗留投影**,本增量一个不动;构造器是唯一的适配入口。

| canonical | `PriceLevels` | `SniperPoints` | `PriceLines` | `key_levels` |
| --- | --- | --- | --- | --- |
| `entry_zone.low` | `entry` | `min(ideal_buy, secondary_buy)` | `entry` | `support` |
| `entry_zone.high` | `entry` | `max(ideal_buy, secondary_buy)` | `entry` | — |
| `stop` | `stop` | `stop_loss` | `stop` | `stop_loss` |
| `targets[0]` | `target` | `take_profit` | `target` | `resistance` |
| `risk_reward` | `risk_reward`(既有,`build_price_lines` 处丢弃) | — | — | — |
| `direction` | 隐含 `long` | 隐含 `long` | 隐含 `long` | — |

位置:`PriceLevels` = `src/services/volume_price_signals.py:353`;`SniperPoints` = `src/schemas/report_schema.py:128`;`PriceLines` = `api/v1/schemas/stocks.py:139`;`key_levels` = `src/agent/protocols.py:139`。

`key_levels` 仅供对照(agent 路径),不为它写构造器。

## 5. 继承来的解析行为

LLM 路径的四个价位一律走 `parse_sniper_value`(`src/sniper_parsing.py:13`)。它的既有行为原样继承,**不在本增量修改**:

- `"18.50-19.00"` → `19.0`。区间字符串塌缩到**上界**(取最后一个数)。
- 守卫不对称:数值入口有 `> 0` 守卫,**字符串入口没有**。`"0"` → `0.0`,`"-5"` → `-5.0`,`"inf"` → `inf`,`"nan"` → `nan`。

因此 `parse_sniper_value` 返回非 `None` **不蕴含**返回值为有限正数。构造器用 `trade_levels_invalid` 自守。

## 6. 已知缺口

1. **`short` 有 schema、有方向感知校验器、有测试,但无构造器、无 evidence 路径。** 今天没有任何生产者产出空头价位;且回测在 `volume_price_signals.py:1598` 丢弃所有非 bullish 信号,`signal_stats` 里永远只有多头。填它是 Inc 4 的事。
2. **规则路径的 `entry_zone` 是退化点区间**(`low == high`)。真正的两端区间只有 LLM 路径(靠 `secondary_buy`)才有。
3. **`invalidation` 是构造器的必填入参。** Inc 0 不发明失效语义:规则路径今天不产出任何可判定的失效条件。
4. **无 `tradability` 槽位。** A股 涨跌停 / T+1 / 最小手数在代码里**不存在**(只活在 `src/market_context.py` 的 LLM prompt 散文里);`ggt_eligible` 是标的属性而非信号属性。契约绝不声称引擎没实现的约束。Inc 5 追加。
5. **`derive_price_levels` 的 20 / 14 窗口硬编码**,不受 `VPSConfig` 的 crypto / interval 覆盖影响。`TradeSignal` 原样继承。

## 7. 扩展点

| 增量 | 追加内容 | 破坏性 |
| --- | --- | --- |
| Inc 4 决策收敛层 | `TradeSignal` 的真实生产者(含 short) | 无(新增调用方) |
| Inc 5 仓位 / 制度可执行性 | 填 `position_size`;追加 `tradability: Optional[...] = None` | 无(追加可空字段) |
| Inc 6 生命周期追踪 | 持久化 + 状态机 + 失效告警 | 需新建表 |

字段命名判别原则:**战略 `docs/strategy-actionable-signal-system.md:72` 钦定的字段必须存在,即使当前没有生产者;钦定之外的推测性字段一律不留槽位。**
