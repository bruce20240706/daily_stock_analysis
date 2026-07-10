# Inc 0 — canonical `TradeSignal` 契约定型(设计)

- 日期:2026-07-10
- 战略来源:`docs/strategy-actionable-signal-system.md` §3 / L1(:72)/ Inc 0(:107-112)/ 依赖图(:152)
- 状态:设计已评审,待 writing-plans

---

## 0. 摘要

定义 canonical `TradeSignal` pydantic 契约,落在 `src/schemas/trade_signal.py`;配一层只读纯函数构造器 `src/services/trade_signal_builder.py`,把仓库里**已经存在**的两种信号载荷(规则路径的 `PriceLevels`、LLM 路径的 `SniperPoints`)适配成 canonical 形状。

**零接线**:本增量不接 API、不接报告、不接通知、不落库、不加配置项、不动前端。既有报告载荷逐字节不变。契约的存在性由测试证明,而非由消费方证明。

`TradeSignal` 是 Inc 4(决策收敛层)、Inc 5(仓位/制度可执行性)、Inc 6(生命周期追踪)三个下游增量的共同承重面。战略 :112 逐字:「**everything 挂在此契约上**」。

---

## 1. 目标与非目标

### 1.1 目标

1. 定义 canonical `TradeSignal` schema,含战略 :72 钦定的 8 个字段:`{direction, entry_zone, stop, targets[], position_size, confidence, horizon, invalidation}`。
2. 止损/目标**波动率自适应**(战略 :72 逐字:「VPS 已算 ATR,可直接用」)—— 通过复用既有的 `derive_price_levels`(基于 ATR)实现,不重算 ATR、不另立公式。
3. 提供只读构造器,证明契约**能被今天的数据填满**,而不是白板上的字段名。
4. 定义「信号 vs 分析结论」的呈现边界(战略 :108),并让它可被 CI 检验。
5. 满足 Inc 0 验收(战略 :110 逐字):「schema 通过 pydantic/schema 测试;不改变现有报告载荷(追加而非替换)」。

### 1.2 非目标(明确不做)

- **不产出信号**:不新增任何生产者、不接入任何 pipeline。产出 `TradeSignal` 是 Inc 4 的范围(战略 :134)。
- **不落库**:不新增表、不新增列。Inc 6 的生命周期追踪落地时再定持久化。
- **不加配置项**:`.env.example` / `src/config.py` / `src/core/config_registry.py` / `apps/dsa-web/src/locales/settingsHelp.ts` 一律不动。
- **不重构既有四种价位形状**:`SniperPoints` / `PriceLevels` / `PriceLines` / `key_levels` 一个不动(理由见 §8)。
- **不实现可执行性过滤**:A股 涨跌停 / T+1 / 最小手数 是 Inc 5 的范围,且今天**代码里根本不存在**(§2.6)。契约绝不声称引擎没实现的约束。
- **不新增对外分发路径**:合规 (a) 档硬约束(§3)。

---

## 2. 事实底座(全部已在代码中核实)

### 2.1 greenfield 确认

全仓不存在 `class TradeSignal`;`docs/superpowers/specs/` 下无相关 spec。唯一的顺带引用在 `docs/superpowers/specs/2026-07-03-ggt-southbound-context-design.md:52`,该处把港股通硬 universe 过滤显式 defer 到「TradeSignal 契约落地再升级 gate」—— 一个已登记在案的下游等待方。

### 2.2 波动率自适应止损/目标已经存在

`src/services/volume_price_signals.py`:

| 符号 | 位置 | 语义 |
| --- | --- | --- |
| `PriceLevels` | :352 | 非 frozen dataclass,`entry / stop / target / risk_reward`,四项均 `float \| None` |
| `derive_price_levels` | :382 | `entry = max(MA20, 近20根 swing low)` 且 `<= 现价`;`stop = entry - 1.5×ATR`;`target = entry + 2.0×(entry - stop)`;`risk_reward = (target-entry)/(entry-stop)` |
| `atr` | :305 | 全仓唯一 canonical Wilder ATR |
| `is_invalid_price_level` | :425 | long-setup 风险健全性检查 |
| `_DEFAULT_ATR_MULT / _DEFAULT_RR_TARGET / _PRICE_LEVEL_WINDOW` | :347-349 | `1.5 / 2.0 / 20` |

`derive_price_levels` 的 `_PRICE_LEVEL_WINDOW=20` 与 `atr(period=14)` 是**硬编码**,与 `VPSConfig.breakout_window/atr_period` 解耦:crypto 与 interval 覆盖**不改变** entry/stop/target。这是既有事实,本增量不改。

### 2.3 `risk_reward` 被计算但在 API 边界丢弃

`api/v1/endpoints/stocks.py:54` `build_price_lines(levels)` 只映射 `entry/stop/target` 进 `PriceLines`(`api/v1/schemas/stocks.py:139`),`levels.risk_reward` 静默丢失。今天没有任何消费方看得到这个比值。

### 2.4 LLM 一直在产出一个两端的入场区间,只是没人组装

`src/schemas/report_schema.py:128` `SniperPoints` 有四个字段:`ideal_buy` / `secondary_buy` / `stop_loss` / `take_profit`,类型均为 `Optional[Union[str, int, float]]`。

`secondary_buy` 是入场区间的第二端,但被 `templates/report_wechat.j2:50-54`(只渲染 ideal_buy / stop_loss / take_profit)与紧凑推送直接丢弃。

`src/sniper_parsing.py:13` `parse_sniper_value(value) -> Optional[float]` 是 Inc 3 抽出的**唯一**文本→浮点解析器。其继承行为(全部已实测):

- `"18.50-19.00"` → `19.0`(取最后一个数,区间字符串塌缩到上界);`"18.50~19.00"` 同。
- `"17.80元"` → `17.8`;`"理想买入点：17.80"` → `17.8`;MA 前缀数字被过滤。
- **守卫不对称(关键)**:数值入口有 `v > 0 else None` 守卫(:22-24),**字符串入口没有** —— `float(text)` 直接返回(:32-34)。因此:

| 入参 | 返回 |
| --- | --- |
| `"0"` | `0.0` |
| `"-5"` | `-5.0` |
| `"inf"` / `"1e400"` | `inf` |
| `"nan"` | `nan` |
| `0` / `-5` / `float("nan")`(数值) | `None` |
| `float("inf")`(数值) | `inf` |

即:`parse_sniper_value` 返回非 `None` **不蕴含**返回值为有限正数。任何消费它的构造器必须自行守卫(§6.2)。`src/claim_validation.py` 的 `validate_structure` 亦是如此自守。

### 2.5 空头信号今天没有任何统计路径

`src/services/volume_price_signals.py:1577` `compute_signals_for_all_bars` 内的 `_add`(:1597)在 :1598 处 `if sig.direction != "bullish": return`,丢弃**所有**非 bullish 信号。因此 `signal_stats` 表里永远只有多头 signal_type。

`src/services/signal_backtest.py:37` `BASELINE_SIGNAL_TYPE = "__baseline__"` 是保留哨兵 signal_type。

### 2.6 T+1 / 涨跌停 / 最小手数在代码中不存在

- `src/market_context.py:75,79`:A股 涨跌停(±10/20/30%)与 T+1 只作为 **LLM prompt 散文**存在。
- `data_provider/tickflow_fetcher.py:170` `_get_limit_ratio` 是全仓唯一真实的涨跌停 band 数学(已编码 BSE 0.30 / 科创创业 0.20 / ST 0.05 / 其余 0.10),但**只喂大盘宽度聚合计数**(`limit_up_count` / `limit_down_count`),从不挂到任何个股信号上。
- `volume_price_signals.py:247` `is_limit_bar = (high - low <= 0)` 是**退化 bar 启发式**,不是 `prev_close ± band` 计算;它用于跳过信号检测并透出 `is_anomalous`。**不得**当作可执行性 gate。
- 全仓无 T+1 结算、无 lot size / board_lot、无 per-signal 涨跌停 gate。回测**不**强制 T+1、**不**模拟涨跌停无法成交。

### 2.7 词表现状(TradeSignal 必须跨过的沼泽)

**同一个「买/止损/目标」三元组,四种拼写:**

| 形状 | 位置 | 字段 |
| --- | --- | --- |
| `SniperPoints` | `src/schemas/report_schema.py:128` | `ideal_buy / secondary_buy / stop_loss / take_profit` |
| `PriceLevels` | `src/services/volume_price_signals.py:353` | `entry / stop / target / risk_reward` |
| `PriceLines` | `api/v1/schemas/stocks.py:139` | `entry / stop / target` |
| `key_levels` | `src/agent/protocols.py:139` | `support / resistance / stop_loss` |

四者全为标量;战略要求 `entry_zone`(区间)与 `targets[]`(数组)。

**方向/动作有 9 套以上平行词表**,互不可换:`action` 八态(`src/schemas/decision_action.py:16`)、`decision_type` 三态、`action_group` 四态、`direction` bullish/bearish/neutral(`api/v1/schemas/stocks.py:117`)、`position_recommendation` long/cash/short(`src/storage.py:317`)、`direction_expected` up/down/flat/not_down(`src/storage.py:327`)、`operation_advice` 自由文本、`BuySignal` 中文六态、告警家族 above/below + bullish_cross/bearish_cross。

**置信度有三个互不兼容的轴:**

| 轴 | 类型 | 位置 |
| --- | --- | --- |
| LLM 自报 | `float [0,1]`(clamp 而非校准) | `src/agent/protocols.py:137` |
| 定性 | `Literal["high","medium","low"]` | `api/v1/schemas/stocks.py:120` |
| 定性(本地化显示) | `"高"/"中"/"低"` 或 `"High"/…` | `src/analyzer.py:1727` |
| 统计 | `verified: bool` + `ci_low_corrected` + `family_size` | `api/v1/schemas/stocks.py:128` |

另有 `plan_quality: Literal["high","medium","low"]` —— **同 token,完全不同的概念**(计划完整度)。

**市场大小写冲突:**`get_market_for_stock`(`src/core/trading_calendar.py:110`)返回小写 `cn|hk|us|crypto|None`(perp 塌缩为 `crypto`;docstring 漏写 crypto);`signal_stats.market` 亦小写;而看板 `_infer_market`(`src/services/signal_board_service.py:95`)返回大写 `CN|HK|US`,`_annotate_ggt` 精确匹配 `== "HK"`。

`MarketRegion`(`src/schemas/market_light.py:11`)= `Literal["cn","hk","us"]`,**无 crypto 成员**,不可复用。

### 2.8 证据读路径

`src/services/signal_hit_rate.py:97`:

```python
def resolve_marker_hit_fields(signal_type: str, code: str, *, interval: str = "1d") -> dict
```

它内部由 `code` 推出 market、由 `cfg.signal_backtest_horizon_bars` 定 horizon,查 `signal_stats`。

返回 keys:`hit_rate, hit_sample, verified, ci_low, ci_high, baseline_excess, horizon, ci_low_corrected, family_size, risk_metrics, oos`。

**缺桶 / 样本不足**时返回 `_none` 哨兵 dict(:117-120):`hit_sample = None` 且 `verified = False`。
**有桶但未通过超额判定**时:`hit_sample` 非 None、`verified = False`。

二者的判别式是 `hit_sample is not None`,不是 `verified`。

### 2.9 `risk_metrics` 的形状因生产者而异

- 链路A(`src/core/backtest_engine.py`):8 个基础键 + `note`,**无** `excluded/interval/horizon`。
- 链路B(`src/services/signal_backtest.py`):8 个基础键 + `excluded/interval/horizon`,**无** `note`。

同名 JSON blob,两种形状。且其自身文档反复声明:这是**描述性统计**,无 CI、无多重检验校正,**不是**跨格选择判据(只有 `verified` 是)。

### 2.10 pydantic 版本与可用约束(已实测,pydantic 2.13.4)

- `Field(gt=0)` **拦不住** `float("inf")`(`inf > 0` 为真);必须叠加 `allow_inf_nan=False`。
- `Field(min_length=1)` 可拦空 list。
- `ConfigDict(extra="forbid")` 可拦未声明字段。
- `computed_field` 会进入 `model_dump()`,与 `extra="forbid"` 组合会让 `Model(**m.model_dump())` round-trip 抛 `ValidationError`。

### 2.11 `src/schemas/` 房规

- `__init__.py` 只导出 `AnalysisReportSchema` + 7 个 context-pack 符号;`decision_action` 与 `market_light` **均不导出**,消费方按点分路径 import。
- `report_schema.py`:`ConfigDict(extra="allow")`,宽松,面向 LLM 输出解析;且它是**校验专用** —— `src/analyzer.py:3884` 的 `model_validate(data)` 返回值被丢弃,仅用于打 warning。
- `market_light.py`:`from __future__ import annotations`、Literal 密集、字段全必填。最接近「canonical 契约」的气质。
- `analysis_context_pack.py`:`ConfigDict(validate_assignment=True)`,含 ISO-8601 校验器 `_validate_iso8601_timestamp`(:25)。
- `analysis_context_pack.py:28`:该校验器**要求字符串含 `"T"`** —— 裸日期 `"2026-07-10"` 会被拒。

---

## 3. 合规定位与呈现边界

### 3.1 定位(人决策,已拍板)

战略 :59 逐字:「**📌 当前暂定定位(2026-07-03,可升级)**:取 **(a) 自用 / 内部** 为默认档」。

约束(:60-62 逐字):

- 「信号仅用于**自用**;**不新增任何对外信号分发/推送渠道**;既有 notification 属自用推送,不构成对外分发。」
- 「**升级前**所有对外"明确买卖指令"分发路径保持关闭」。

### 3.2 呈现边界在 Inc 0 靠「零接线」满足

红线要求的是「**不新增**对外渠道」。既有 `src/notification.py` 已被战略 :60 明确判定为「自用推送,不构成对外分发」,因此**禁止它 import TradeSignal 并不是合规红线的要求**。

Inc 0 的真实不变式是**零接线**:`TradeSignal` 不被任何 runtime 路径消费,因此它不可能被呈现为「操作指令」。呈现边界在本增量由「压根不呈现」满足。

`docs/trade-signal-contract.md` 负责规定 Inc 4 接线时的呈现规则:

1. (a) 档下,`TradeSignal` 的任何用户可见渲染必须以「**分析结论**」措辞出现,不得表述为「操作指令」。
2. 不得为 `TradeSignal` 新增任何对外分发/推送渠道。
3. 升级到 (b) 持牌 或 (c) 教育免责档前,以上两条持续生效。

### 3.3 用白名单锁住零接线(而非黑名单)

drift-lock 断言:**全仓非测试 `.py` 中提及 `trade_signal` 的模块集合 ⊆ `{src/services/trade_signal_builder.py}`**。

白名单强于黑名单:它连 `main.py`、`scripts/`、`.github/scripts/` 以及任何没人预先想到的路径一并管住。Inc 4 接线或合规档升级时,**修改这个白名单是一个显式、可 diff、可审计的动作**。

---

## 4. 架构:分层与落位

`src/schemas/` 现无任何模块 import `src/services/`。若构造器直接吃 `PriceLevels`,会引入 `schemas → services` 依赖倒挂。故拆两个文件:

| 文件 | 内容 | 允许的依赖 |
| --- | --- | --- |
| `src/schemas/trade_signal.py` | pydantic 模型、类型别名、方向感知校验器、共享谓词 `trade_levels_invalid` | stdlib、pydantic、同包 `decision_action` / `analysis_context_pack` |
| `src/services/trade_signal_builder.py` | 三个纯函数构造器 | 上表 + `src.services.volume_price_signals` + `src.sniper_parsing` |

战略 :108「schema 放 `src/schemas/`」满足。构造器所需的 `is_invalid_price_level` 与 `parse_sniper_value` 都在 services / src 顶层,不倒挂。

`src/schemas/__init__.py` **不导出** `TradeSignal` —— 与 `decision_action` / `market_light` 的既有惯例一致(§2.11)。

---

## 5. 数据模型

### 5.1 类型别名

```python
SignalMarket    = Literal["cn", "hk", "us", "crypto"]   # get_market_for_stock 值域去掉 None
SignalDirection = Literal["long", "short"]
SignalConfidence= Literal["high", "medium", "low"]      # 与 SignalMarker.confidence 同 token
SignalSource    = Literal["rule", "llm"]                # 与 SignalMarker.source 同 token
SignalInterval  = Literal["1d", "1m", "5m", "15m", "1h"]
```

`SignalMarket` 不复用 `MarketRegion`:后者无 `crypto` 成员(§2.7),且 `market_light` 是市场级 regime 快照,语义不同。

`SignalInterval` 是新拼写,但由 drift-lock 锁定为 `SUPPORTED_INTERVALS`(`src/core/intraday_backtest.py:14`)的同一集合(§10.2)。

### 5.2 子模型

```python
class PriceZone(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    low:  float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)
    # model_validator: low <= high


class SignalEvidence(BaseModel):
    """样本外统计证据。拼写逐字对齐 SignalMarker(api/v1/schemas/stocks.py:126-133)。"""
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    verified:         bool
    hit_rate:         Optional[float] = None
    hit_sample:       Optional[int]   = None
    ci_low:           Optional[float] = None
    ci_high:          Optional[float] = None
    baseline_excess:  Optional[float] = None
    ci_low_corrected: Optional[float] = None
    family_size:      Optional[int]   = None


class Invalidation(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    price:       Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    valid_until: Optional[str]   = None   # 完整 ISO-8601 datetime(必须含 'T')
    note:        Optional[str]   = None

    _v = field_validator("valid_until")(validate_iso8601_timestamp)   # 见 §5.8 / §9;None 直通
    # model_validator: 三者至少一个非 None
```

`TradeSignal.as_of` 用**同一个**校验器(它对 `None` 直通,而 `as_of` 是必填 `str`,故不会漏检)。

`invalidation.price` 的方向语义由 `TradeSignal.direction` 决定:`long` → 收盘价 ≤ `price` 即失效;`short` → 收盘价 ≥ `price` 即失效。

### 5.3 `TradeSignal`

```python
class TradeSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # --- 身份 ---
    code:         str = Field(min_length=1)
    market:       SignalMarket                     # mode="before" 校验器做 .strip().lower()
    signal_type:  str = Field(min_length=1)        # 校验器拒绝 "__baseline__"
    interval:     SignalInterval = "1d"
    horizon_bars: int = Field(gt=0)
    as_of:        str                              # 完整 ISO-8601 datetime
    source:       SignalSource

    # --- 战略 :72 钦定的 8 字段 ---
    direction:     SignalDirection
    entry_zone:    PriceZone
    stop:          float = Field(gt=0, allow_inf_nan=False)
    targets:       list[Level] = Field(min_length=1)     # Level = Annotated[float, Field(gt=0, allow_inf_nan=False)]
    position_size: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    confidence:    SignalConfidence
    invalidation:  Invalidation
    # `horizon` 拆成 interval + horizon_bars

    # --- 投影与证据 ---
    action:   Optional[DecisionAction] = None   # 八态,同包 import
    evidence: Optional[SignalEvidence] = None   # None = 无历史统计路径

    @property
    def risk_reward(self) -> float: ...
```

字段与战略 :72 `{direction, entry_zone, stop, targets[], position_size, confidence, horizon, invalidation}` 逐项对应,`horizon` 展开为 `interval` + `horizon_bars`(理由:二者合起来才是 `signal_stats` 自然键的时间维度,单独一个 `horizon` 无法定位证据桶)。

`targets` 的**元素**约束用 `Annotated`(已实测,pydantic 2.13.4 逐元素拒绝 `0` / 负数 / `inf` / `nan`);`Field(min_length=1)` 只管长度:

```python
Level = Annotated[float, Field(gt=0, allow_inf_nan=False)]
```

### 5.4 方向感知的价位判据

```
long : stop < entry_zone.low <= entry_zone.high < targets[0] < targets[1] < ...
short: stop > entry_zone.high >= entry_zone.low > targets[0] > targets[1] > ...
```

即:`targets` 严格单调、方向与 `direction` 一致,且首个 target 严格越过 entry_zone 的远端;`stop` 严格位于 entry_zone 的近端之外。

判据由**单一纯谓词**实现并被两处调用(§6.4):

```python
def trade_levels_invalid(*, direction, zone_low, zone_high, stop, targets) -> bool
```

它同时判定三件事,与 `is_invalid_price_level`(`volume_price_signals.py:425`)的结构对称,但**方向感知且不含 `current_price` 约束**:

1. 任一值为 `None` / 非有限 / `<= 0`;
2. `zone_low > zone_high`;
3. 按 `direction` 的排序不变式违反。

**为什么谓词也管 (1) 而不只管排序**:构造器要在**构造模型之前**用它做 fail-closed 判定,那时值还是 `parse_sniper_value` 吐出的裸浮点 —— 而 §2.4 已实测该函数会漏出 `0.0` / `-5.0` / `inf` / `nan`。若谓词只管排序,这些值会流进模型并抛 `ValidationError` 而非返回 `None`,直接违反构造器的 fail-closed 契约。

模型内 `Field(gt=0, allow_inf_nan=False)` 与 `Level` 的元素约束是**纵深防御**(提供更好的错误信息,且 `model_validator(mode="after")` 运行时 (1) 已不可能触发),不是平行实现:谓词是唯一被构造器调用的判据。

### 5.5 `risk_reward`(普通 `@property`,不用 `computed_field`)

取**最差入场**:

- `long`:`(targets[0] - entry_zone.high) / (entry_zone.high - stop)`
- `short`:`(entry_zone.low - targets[0]) / (stop - entry_zone.low)`

分母由 §5.4 不变式保证严格为正,故恒非 `None`、恒有限。

用普通 `@property` 而非 `computed_field`:后者会进入 `model_dump()`,与 `extra="forbid"` 组合会让 `TradeSignal(**s.model_dump())` round-trip 抛 `ValidationError`(§2.10 已实测)。普通 property 不进序列化,round-trip 干净,Python 侧照常可读(Inc 5 的 sizing 要用)。将来需要出到 JSON,改一个装饰器即可。

### 5.6 五条刻意的克制(每条都有理由)

**先讲一条贯穿的判别原则**,否则 (2) 与 (4) 看起来自相矛盾 —— `position_size` 在 Inc 0 恒为 `None`(没有生产者),`tradability` 也恒为 `None`,凭什么留前者、砍后者?

> **战略 :72 钦定的字段必须存在,即使 Inc 0 没有生产者;钦定之外的推测性字段一律不留槽位。**

`position_size` 与 `confidence` / `invalidation` 一样,是战略逐字列出的 8 字段之一,是 Inc 5 的**契约义务**,不是猜测。`action`(八态投影)同理:它把 canonical 与既有展示层对齐,是 §7 映射表的一部分。而 `tradability` 从未被战略列出,是我推断 Inc 5 会需要的东西 —— 推断出来的字段就是死字段。

Inc 5 追加 `tradability: Optional[...] = None` 是零破坏的;而删一个已被三个下游引用的字段不是。不对称的代价决定了不对称的取舍。

**(1) `invalidation.price` 不做跨字段约束**,只要求 finite > 0。

本可加 `long → price < entry_zone.low`,但「跌破 MA20 即失效」这类合法前提完全可能落在区间内部。Inc 0 没有任何真实生产者能验证该约束的合理性,而过度约束会让合法场景**不可表达** —— 那是「契约声称引擎没实现的东西」的镜像错误。

**(2) `position_size` 只有 `gt=0`,不设 `le=1`。**

单位 = 权益比例;允许 > 1 表示杠杆(仓库的 perp `L>1` 回测是真实存在的)。`None` = 尚未定量(Inc 5 填)。

**(3) `signal_type` 是自由 `str`,但校验器拒绝 `RESERVED_SIGNAL_TYPE = "__baseline__"`。**

那是 `src/services/signal_backtest.py:37` 的保留哨兵;一条叫这个名字的 `TradeSignal` 会污染回测基线格子。

schema 层不能 import 回测模块(层级倒挂,§4),故它在 `trade_signal.py` 内是字面量常量,由 §10.4 的 drift-lock 钉死在真源上。

**(4) 不设 `tradability` 槽位。**

`docs/superpowers/specs/2026-07-03-ggt-southbound-context-design.md:52` 确实把硬 universe 过滤 defer 到本契约落地,但:T+1 / 涨跌停 / 最小手数在代码里**根本不存在**(§2.6);`ggt_eligible` 是**标的属性**而非信号属性。留一个 Inc 0 永远填 `None` 的字段,正是仓库在 Inc 1c review 中被逆过一次的「死字段」反模式。Inc 5 追加 `tradability: Optional[...] = None` 是零破坏的。

**(5) `SignalEvidence` 不收 `risk_metrics` / `oos`。**

二者形状因生产者而异(§2.9),契约化会撒谎;且它们是**描述性统计,不是选择判据**(只有 `verified` 是)。若 Inc 5/7 真需要,追加 `Optional[Dict[str, Any]]` 零破坏。

### 5.7 `market` 归一

`TradeSignal.market` 用 `field_validator(mode="before")` 做 `str(v).strip().lower()`。

这样看板的 `"HK"`(`_infer_market`,大写)与引擎/回测的 `"hk"`(小写)喂进来都对。把一个已经咬过人的大小写漂移(`_annotate_ggt` 精确匹配 `== "HK"`)在契约唯一入口处掐掉。

### 5.8 `as_of` / `valid_until` 的时间格式

二者均为**完整 ISO-8601 datetime 字符串,必须含 `"T"`**。裸日期被拒。

日线信号写 `"2026-07-10T00:00:00"` —— 与引擎 `_to_epoch_ms_shanghai`(`volume_price_signals.py:157`)对纯日期坍缩到当日午夜的语义一致。分钟信号保留时分秒。

校验器复用 `analysis_context_pack.py:25` 的既有实现(§9)。

---

## 6. 构造器(只读适配层)

`src/services/trade_signal_builder.py`,三个纯函数:零 I/O、零 config 读取、零 DB 访问、零日志。

`source` **不是**构造器参数:它由构造器自身固定(`build_from_price_levels` → `"rule"`,`build_from_sniper_points` → `"llm"`),因为它描述的正是这条信号从哪条路径来的。调用方无从也无需覆盖。`direction` 同理(两者今天都固定 `"long"`,见 §13.1(1))。

### 6.1 `build_from_price_levels`

```python
def build_from_price_levels(
    levels: PriceLevels,
    *,
    code: str, market: str, signal_type: str, interval: str,
    horizon_bars: int, as_of: str, confidence: str,
    invalidation: Invalidation,
    current_price: float | None = None,
    action: DecisionAction | None = None,
    evidence: SignalEvidence | None = None,
) -> Optional[TradeSignal]
```

规则路径。`direction = "long"` 固定 —— `PriceLevels` 按构造即 long-setup(§2.2)。

fail-closed 判据直接调 `is_invalid_price_level(entry=levels.entry, stop=levels.stop, target=levels.target, current_price=current_price)`:它一次覆盖缺值、非有限、`<= 0`、排序违反、以及 `entry > 现价`。返回 `True` → `return None`。

- `entry_zone = PriceZone(low=levels.entry, high=levels.entry)` —— **退化点区间**(规则路径只有一个入场标量)。
- `targets = [levels.target]`
- `levels.risk_reward` **不**读取:`TradeSignal.risk_reward` 由 §5.5 公式独立算出,单一来源,不双写。

### 6.2 `build_from_sniper_points`

```python
def build_from_sniper_points(
    sniper: SniperPoints,
    *,
    code: str, market: str, signal_type: str, interval: str,
    horizon_bars: int, as_of: str, confidence: str,
    invalidation: Invalidation,
    action: DecisionAction | None = None,
    evidence: SignalEvidence | None = None,
) -> Optional[TradeSignal]
```

LLM 路径。`direction = "long"` 固定 —— `SniperPoints` 语义即买入点 + 止盈。

四个值一律走 `parse_sniper_value`(`src/sniper_parsing.py:13`),不另写解析。

1. 解析四个值。`ideal_buy` / `stop_loss` / `take_profit` 任一为 `None` → `return None`(源数据不足)。
2. `secondary_buy` 为**有限正数**时 → `zone_low = min(ideal, secondary)`、`zone_high = max(ideal, secondary)`;否则(`None` / 非有限 / `<= 0`)`zone_low = zone_high = ideal`。
   顺序要紧:必须先筛掉 `secondary` 的坏值再取 `min/max`,否则 `min(19.0, -5.0)` 会把 `-5.0` 选成区间下端。
3. **单次 fail-closed 判定**:`trade_levels_invalid(direction="long", zone_low=…, zone_high=…, stop=stop_loss, targets=[take_profit])` 为 `True` → `return None`。

   这一步同时挡住 §2.4 实测出的 `"0"→0.0` / `"-5"→-5.0` / `"inf"→inf` / `"nan"→nan` 泄漏,以及排序违反(如 `stop_loss > ideal_buy`)。**不**用 `try/except ValidationError` 吞异常(§6.4)。
4. 构造 `TradeSignal`,`stop = stop_loss`、`targets = [take_profit]`。

`secondary_buy` 是可选的区间下端而非必需价位,故它的坏值只降级为退化点区间,不使整条信号作废。

继承行为(写入 `docs/trade-signal-contract.md`,不在本增量修改):`parse_sniper_value("18.50-19.00")` 返回 `19.0`,区间字符串塌缩到上界。

### 6.3 `attach_evidence`

```python
def attach_evidence(signal: TradeSignal, hit_fields: Optional[Mapping[str, Any]]) -> TradeSignal
```

把 `resolve_marker_hit_fields(...)` 的输出(§2.8)映成 `SignalEvidence`,`signal.model_copy(update={"evidence": ev})` 返回新对象。

判据(照 §2.8 的真实语义):

1. `hit_fields` 为 `None`,或 `hit_fields.get("hit_sample") is None` → 原样返回,`evidence` 保持 `None`。
   (`hit_sample is None` 即 resolver 的 `_none` 哨兵 = 缺桶/样本不足。**不**用 `verified` 判别:有桶但未通过超额判定时 `verified=False` 而 `hit_sample` 非 None,那是真实证据。)
2. `hit_fields.get("horizon")` 非 `None` 且 `!= signal.horizon_bars` → `raise ValueError`。
   把 5 根窗口的统计附到 10 根窗口的信号上是编程错误,必须响,不静默。
3. 否则构造 `SignalEvidence`,只取 §5.2 声明的 8 个键;`risk_metrics` / `oos` 按 §5.6(5) 丢弃。

### 6.4 价位判据只有一份实现

`trade_levels_invalid(*, direction, zone_low, zone_high, stop, targets) -> bool` 定义在 `src/schemas/trade_signal.py`(§5.4),被两处调用:

1. `TradeSignal` 的 `model_validator(mode="after")`;
2. `build_from_sniper_points` 的 fail-closed 前置检查(§6.2 步骤 3)。

**不**用 `try/except ValidationError` 接价位违反 —— 那是仓库明令禁止的「broad fallback / 静默降级」,且会把「源数据不足」(应返回 `None`)与「调用方传参错误」(应抛异常)混为一谈。

规则路径继续用 `is_invalid_price_level`:它多一条 `entry > current_price` 约束、且写死 long,判据本就不同,不是重复实现(§8)。

---

## 7. canonical ↔ 遗留投影映射表

写入 `docs/trade-signal-contract.md`。`TradeSignal` 是 canonical;其余四种为**遗留投影**,本增量一个不动。

| canonical | `PriceLevels` | `SniperPoints` | `PriceLines` | `key_levels` |
| --- | --- | --- | --- | --- |
| `entry_zone.low` | `entry` | `min(ideal_buy, secondary_buy)` | `entry` | `support` |
| `entry_zone.high` | `entry` | `max(ideal_buy, secondary_buy)` | `entry` | — |
| `stop` | `stop` | `stop_loss` | `stop` | `stop_loss` |
| `targets[0]` | `target` | `take_profit` | `target` | `resistance` |
| `risk_reward` | `risk_reward`(既有,API 处丢弃) | — | — | — |
| `direction` | 隐含 `long` | 隐含 `long` | 隐含 `long` | — |

`key_levels` 一列仅供对照(`src/agent/protocols.py:139`,agent 路径),本增量**不**为它写构造器。

---

## 8. 明确不复用的既有原语(及理由)

| 原语 | 位置 | 不复用理由 |
| --- | --- | --- |
| `is_invalid_price_level` 作为通用排序判据 | `volume_price_signals.py:425` | docstring 写死 `long-setup ordering: stop < entry < target` 且额外要求 `entry <= current_price`。对 `short` 直接是错的。Inc 3 已确立「不复用它」的先例。规则路径(long)仍调它,因其判据恰好适用。 |
| `MarketRegion` | `market_light.py:11` | 无 `crypto` 成员;且是市场级 regime 语义,非个股信号的市场键。 |
| `claim_validation.validate_structure` 作为构造器判据 | `src/claim_validation.py:164` | **最接近的既有原语,必须讲清楚**,见 §8.1 |
| `PriceLevels` 作为契约字段 | `volume_price_signals.py:353` | 非 frozen dataclass、非 pydantic,不符 `src/schemas/` 房规;且只能表达标量 entry/target,表达不了 `entry_zone` 与 `targets[]`。 |
| `report_schema.SniperPoints` 直接内嵌 | `report_schema.py:128` | 值为 `Union[str,int,float]` 未解析;且它所在的树是校验专用(`AnalysisReportSchema` 的 `model_validate` 返回值被丢弃,`analyzer.py:3884`),树顶 `ConfigDict(extra="allow")`(`report_schema.py:181`)—— `SniperPoints` 自身无 `model_config`,继承 pydantic 默认 `extra="ignore"`。 |
| `risk_metrics` / `oos` 进 `SignalEvidence` | §2.9 | 两生产者形状不同;且为描述性统计,非选择判据。 |
| `is_limit_bar` / `is_anomalous` 作为可执行性 gate | `volume_price_signals.py:247` | 退化 bar 启发式,不是涨跌停判定(§2.6)。 |

### 8.1 `trade_levels_invalid` vs `claim_validation.validate_structure`

这是全仓与新谓词**最像**的既有原语,不写清楚必被判为平行实现。

`validate_structure(sniper_points)`(`src/claim_validation.py:164`,Inc 3 引入)做的事:用 `parse_sniper_value` 解析 `_SNIPER_FIELDS` 四字段;非有限 / `<= 0` → violation;`None` → 视为字段缺失并**跳过**其参与的判据;然后逐对检查价位顺序(`stop_loss < ideal_buy < take_profit` 等 5 对);返回 `{"status", "reason", "violations"}`。

判据 (1) 的口径与 `trade_levels_invalid` 的 (1) **刻意一致**(非有限 / `<= 0` 即不合法),因为二者面对的是同一个 `parse_sniper_value` 的同一批泄漏值(§2.4)。这是有意的对齐,不是巧合。

四条不能直接复用的理由:

1. **long-only。** 它的 5 组价位对写死了 `stop < entry < target` 的多头顺序,无法表达 short。这与 §8 首行拒绝 `is_invalid_price_level` 是同一个理由。
2. **形状是 sniper dict,不是 canonical。** 它读 `ideal_buy` / `secondary_buy` 等旧拼写,不认识 `direction` / `zone_low` / `zone_high` / `targets[]`;尤其它把 `ideal_buy` 与 `secondary_buy` 当两个独立价位逐对比较,而 canonical 把它们合成一个 `entry_zone`。
3. **缺失即跳过,不即失败。** 它对缺失字段**不报违规**(设计如此:LLM 可以只给部分价位)。构造器必须相反 —— `ideal_buy` / `stop_loss` / `take_profit` 任一缺失即 `return None`。
4. **返回值是报告不是判据。** `{"status": "violation", "violations": [...]}` 服务于 Inc 3 的「标注 + 封顶置信度」,不服务于「构造 / 不构造」的二值决策。

考虑过抽一个共享的 `_is_positive_finite(x)` 给两边用,**放弃**:它是一行谓词,Inc 3 已内联,为它新开一个共享模块的成本高于收益,且会让 `claim_validation.py` 反向依赖 schema 层。

**重构四种价位形状(方案 3)被拒的理由:** 跨 8 层静默丢弃边界、两套渲染引擎、前端 mapper、DB 列、以及 LLM prompt 里的中文字面量(`analyzer.py:1996` 「理想买入点」)。直接违反 Inc 0 验收线「不改变现有报告载荷」,且在 Inc 4 尚未落地、无任何真实消费方时重构消费方,是把风险前置到没有需求的时刻。收敛留给有真实消费方之后。

---

## 9. 轻触既有文件(唯一一处)

`src/schemas/analysis_context_pack.py`:

```python
def validate_iso8601_timestamp(value: Optional[str]) -> Optional[str]:   # 提升为公开名
    ...                                                                  # 函数体一行不改

_validate_iso8601_timestamp = validate_iso8601_timestamp                 # 私有别名,现有引用不动
```

零行为变化。`trade_signal.py` 导入公开名,既不复制一份实现,也不导入私有符号。

其余既有文件**全部零改**:`api/`、`bot/`、`src/notification.py`、`templates/`、`src/storage.py`、`apps/dsa-web/`、`src/config.py`、`src/core/config_registry.py`、`.env.example`。

---

## 10. drift-lock 四条

### 10.1 import 白名单(锁零接线 + 呈现边界)

```python
ALLOWED = {"src/schemas/trade_signal.py", "src/services/trade_signal_builder.py"}
```

遍历仓库内全部 `.py`(排除 `tests/` 整个目录),**读取文件内容**、文本级检索子串 `trade_signal`,把命中的文件路径收成集合 `hits`。断言两条:

1. `hits <= ALLOWED` —— 任何第三个模块提及它,门禁红。
2. `"src/services/trade_signal_builder.py" in hits` —— **阳性对照**。

第 2 条不可省。没有它,当扫描逻辑本身写错(路径拼错、编码异常吞掉全部文件、`rglob` 模式失效)时 `hits` 会是空集,而空集永远 ⊆ `ALLOWED`,这条 lock 就悄无声息地永远绿。阳性对照把「扫描器还活着」变成断言的一部分。

两点必须明确,否则实现者会踩:

- **`src/schemas/trade_signal.py` 自身在白名单内。** 它的模块 docstring 几乎必然含 `trade_signal` 这个词;把它排除在外会让 lock 第一天就红。三个独立审查视角都撞到了这一处。
- **扫的是文件内容,不是文件路径。** 内容级检索能一并抓住 `importlib.import_module("src.schemas.trade_signal")` 这类字符串路径 —— AST-only 抓不到。而路径级检索会把两个允许文件按路径命中,反而失去区分力。

### 10.2 interval 集合(canonical-derived,非 tautology)

```python
assert set(get_args(SignalInterval)) == set(SUPPORTED_INTERVALS)   # src/core/intraday_backtest.py:14
```

两边来源不同:一个是 schema Literal,一个是回测模块的规范元组。集合增长而 schema 未跟上 → 门禁红。

### 10.3 映射表哨兵值(锁字段串位)

用互不相同、互不为倍数的哨兵值喂构造器,精确断言落位:

```python
levels = PriceLevels(entry=11.0, stop=7.0, target=19.0, risk_reward=99.0)
sig = build_from_price_levels(levels, ..., current_price=13.0)
assert sig.entry_zone.low == 11.0 and sig.entry_zone.high == 11.0
assert sig.stop == 7.0
assert sig.targets == [19.0]
assert sig.risk_reward == pytest.approx(2.0)   # (19-11)/(11-7);证明 99.0 被无视
```

兄弟字段串位、漏字段必红。手法沿用 Inc 3。

`risk_reward` 的哨兵取 **99.0 而非真值 2.0**:若取 2.0,则「构造器错误地读了 `levels.risk_reward`」与「按 §5.5 公式重算」两种实现给出同一个 2.0,断言失去区分力。取 99.0 后,只有重算才能通过 —— 这正是 §6.1「`levels.risk_reward` **不**读取,单一来源不双写」那条不变式的锁。

### 10.4 保留哨兵 signal_type(canonical-derived)

```python
assert RESERVED_SIGNAL_TYPE == BASELINE_SIGNAL_TYPE   # src/services/signal_backtest.py:37
```

`src/schemas/trade_signal.py` **不能** import `src.services.signal_backtest`(层级倒挂,§4),故 `"__baseline__"` 在 schema 内是字面量常量 `RESERVED_SIGNAL_TYPE`。这条 lock 把它钉死在回测模块的真源上:哪天回测改了哨兵名而 schema 没跟上,门禁红。与 §10.2 同一手法(两边来源不同,非 tautology)。

---

## 11. 测试面(全离线;无网络、无真实 LLM、无 DB)

### 11.1 `tests/test_trade_signal.py`(schema 与校验器)

1. 合法 `long` / 合法 `short` 各构造成功。
2. `long` 排序违反四例,各自独立:`stop >= zone.low`;`zone.low > zone.high`;`targets[0] <= zone.high`;`targets` 非严格递增。
3. `short` 排序违反四例(镜像)。
4. `inf` / `NaN` / `<= 0` 在 `stop` / `targets[i]`(逐元素,含「第二个元素才是 `inf`」一例)/ `zone.low` / `zone.high` / `position_size` / `invalidation.price` 上各被拒。
   (`gt=0` 拦不住 `inf`,专测 `allow_inf_nan=False` 的作用面。)
5. `extra="forbid"`:`TradeSignal(..., stop_loss=1.0)` 被拒 —— 旧拼写污染新契约**不可表达**。
6. `signal_type="__baseline__"` 被拒。
7. `Invalidation()` 三者全空被拒;任一非空则通过(三例)。
8. `market="HK"` / `" Hk "` 归一为 `"hk"`;`market="xx"` 被拒。
9. ISO-8601 校验,`as_of` 与 `invalidation.valid_until` **两处都测**:`"2026-07-10"`(无 `T`)被拒;`"2026-07-10T00:00:00"` 通过;`"2026-07-10T00:00:00Z"` 通过;`valid_until=None` 通过(校验器对 `None` 直通)。
   (只测 `as_of` 会漏掉 `Invalidation` 上那条 `field_validator` 是否真的挂上去了。)
10. `risk_reward` 取最差入场:`long` 用 `zone.high`、`short` 用 `zone.low`,各一条数值断言。样本必须 `zone.low != zone.high`,否则该断言对「取哪一端」无区分力(退化区间下两端相等,测不出串位)。
11. `TradeSignal(**s.model_dump())` round-trip 成功(锁住「不用 `computed_field`」这个决定)。
12. `trade_levels_invalid` 直接单测:两方向 × {合法、`zone_low > zone_high`、排序违反、`None`、`0`、负数、`inf`、`nan`}。
    (第 (1) 类判据在模型内不可达 —— 字段约束先拦下 —— 故必须在此处直测,否则该分支零覆盖。)

### 11.2 `tests/test_trade_signal_builder.py`(构造器)

13. **规则路径用真货**:合成 ≥20 根 OHLCV DataFrame → 真实 `derive_price_levels(df)` → `build_from_price_levels(...)` → 断言字段齐全、`entry_zone` 为退化点区间、`direction == "long"`。
    (不手搓 `PriceLevels`。这是「契约填得满」的证明。)
14. 规则路径 fail-closed:窗口不足(<20 根)→ `levels` 全 `None` → `build_* is None`;`entry > current_price` → `None`。
15. **LLM 路径用真货**:`SniperPoints(ideal_buy="19.00", secondary_buy="17.80元", stop_loss="16.00", take_profit="25.00")` → 真实 `parse_sniper_value` → `entry_zone == (17.8, 19.0)`。
16. LLM 路径退化区间。`ideal_buy="19.00"` 固定,逐一喂 `secondary_buy`,**四例全部**须构造成功且 `zone.low == zone.high == 19.0`:
    - `None`(缺失);
    - `"-5"` → 解析出 `-5.0`(非正)——**不**得出 `zone.low == -5.0`,锁住 §6.2 步骤 2 的筛选**顺序**;
    - `"inf"` → 解析出 `inf`(非有限);
    - `"nan"` → 解析出 `nan`(非有限)。

    后两例不可省。若实现者把守卫写成 `if secondary is not None and secondary > 0`(漏掉 `math.isfinite`),`inf > 0` 为真 → `zone_high = max(19, inf) = inf` → `trade_levels_invalid` 拒绝 → 构造器返回 `None`。可 §6.2 要求它**退化为一条合法信号**。这个变异能逃过其余全部测试。
    (`nan` 一例走另一条路:`nan > 0` 为假,故漏 `isfinite` 时它恰好被 `> 0` 挡下 —— 正因如此,只测 `nan` 抓不到这个变异,必须测 `inf`。)
17. LLM 路径 fail-closed,四例,**均返回 `None` 且不抛异常**:
    - `stop_loss=None`(源数据不足);
    - `stop_loss="20.00"` > `ideal_buy="19.00"`(排序违反);
    - `ideal_buy="0"`(§2.4 的 `"0"→0.0` 泄漏);
    - `take_profit="inf"`(§2.4 的 `"inf"→inf` 泄漏)。
18. characterization,锁住 §2.4 的既有行为,防有人「顺手修」:
    - `parse_sniper_value("18.50-19.00") == 19.0`;
    - `parse_sniper_value("0") == 0.0` 且 `parse_sniper_value(0) is None`(守卫不对称);
    - `parse_sniper_value("inf") == float("inf")`。
19. `attach_evidence`:
    - `hit_fields=None` → `evidence is None`;
    - resolver 的 `_none` 哨兵(`hit_sample=None, verified=False`)→ `evidence is None`;
    - 有桶未验证(`hit_sample=30, verified=False`)→ `evidence` **非** None 且 `verified is False`(锁住「不用 `verified` 判别」这个决定);
    - `horizon` 不匹配 → `raises ValueError`;
    - `risk_metrics` / `oos` 键存在时被丢弃(`extra="forbid"` 之外的显式断言)。
    - **哨兵映射**:8 个证据字段各喂互不相同的值(`hit_rate=0.61, hit_sample=37, ci_low=0.52, ci_high=0.71, baseline_excess=0.09, ci_low_corrected=0.48, family_size=23`),逐字段精确断言。resolver 的键名与 `SignalEvidence` 字段名逐字相同,串位看似不可能 —— 但恰恰因为相同,一个手抖的 `ci_low=hf["ci_high"]` 不会被类型系统发现。三行断言的成本远低于这个 bug 的代价。
20. 构造器纯度。**不**用 `monkeypatch` 打 `get_config`:它根本不在 `trade_signal_builder` 的调用路径上,打了也证明不了什么(打一个永不被调的东西,任何实现都能通过)。
    改为**源码级断言**:`ast.parse` 读 `src/services/trade_signal_builder.py`,收集其全部 `import` / `from ... import`,断言不含 `src.config`、`src.storage`、`get_config`、任何 `*_repo` / `*Repository`。
    这条测的是「构造器的依赖面」这个可判定的事实,而不是一个恰好没被触发的桩。

### 11.3 `tests/test_trade_signal_contract_locks.py`(drift-lock)

21. import 白名单 + 阳性对照(§10.1,两条断言)。
22. interval 集合(§10.2)。
23. 映射表哨兵值,`risk_reward` 哨兵取 99.0(§10.3)。
24. 保留哨兵 `RESERVED_SIGNAL_TYPE == BASELINE_SIGNAL_TYPE`(§10.4)。

### 11.4 集成与门禁

- `./scripts/ci_gate.sh`(`PATH` 前置 `.venv/bin`)。基线 `4113 passed`,预期**只增不减**,现有测试零改。
- 无前端改动 → **免 web-gate**。
- 无 AI 协作资产改动 → 不需 `python scripts/check_ai_assets.py`。

---

## 12. 验收(对照战略 :110)

| 验收线 | 证明方式 |
| --- | --- |
| 「schema 通过 pydantic/schema 测试」 | §11.1(测 1–12)+ §11.2(测 13–20),`ci_gate.sh` 全绿 |
| 「不改变现有报告载荷(追加而非替换)」 | 零接线 + §11.3 的四条 drift-lock(测 21–24);diff 不出现 `api/` / `bot/` / `src/notification.py` / `templates/` / `src/storage.py` / `apps/` |

---

## 13. 风险、边界与未决

### 13.1 已知缺口(必须写进交付说明)

1. **`short` 有 schema、有方向感知校验器、有单元测试,但无构造器、无 evidence 路径。**
   今天没有任何生产者产出空头价位(`PriceLevels` / `SniperPoints` 均 long-setup);且回测在 `volume_price_signals.py:1598` 丢弃所有非 bullish 信号,`signal_stats` 里永远只有多头。契约必须能表达 short,填它是 Inc 4 的事。
2. **规则路径的 `entry_zone` 是退化点区间**(`low == high`)。真正的区间只有 LLM 路径(靠 `secondary_buy`)才有。
3. **`invalidation` 是构造器的必填入参。** Inc 0 不发明失效语义:规则路径今天不产出任何可判定的失效条件。Inc 6 的失效告警需要真正的 `valid_until` / `price` 生产者。
4. **无 `tradability` 槽位**(§5.6(4))。Inc 5 追加。
5. **`derive_price_levels` 的 20/14 窗口硬编码**,不受 `VPSConfig` 的 crypto / interval 覆盖影响(§2.2)。`TradeSignal` 原样继承,不在本增量改。

### 13.2 风险

| 风险 | 缓解 |
| --- | --- |
| 契约定型后 Inc 4/5/6 三个下游都挂上来,字段改名成本高 | `extra="forbid"` + 三条 drift-lock 让漂移必红;且 Inc 0 零接线,回滚即删文件 |
| `TradeSignal` 成为「第 5 种拼写」 | canonical 身份写入 `docs/trade-signal-contract.md` §7 映射表;构造器是唯一入口,映射集中在两个纯函数 |
| `analysis_context_pack.py` 公开化重命名触碰已发布 schema 模块 | 保留私有别名,函数体一行不改,现有引用零改;有测试覆盖 |
| 有人为「方便」把 `risk_reward` 改成 `computed_field` | §11.1 测试 11 的 round-trip 断言会红 |

### 13.3 未决 / deferred

- `PriceLines` 丢弃 `risk_reward`(`api/v1/endpoints/stocks.py:62`)这个既有缺陷**不在本增量修复**(会动 API 契约)。登记为独立候选。
- `key_levels`(`src/agent/protocols.py:139`)→ `TradeSignal` 的构造器不写(agent 路径当前不在 actionable-signal 主线上)。
- 四种价位形状的最终收敛:待 Inc 4 落地、有真实消费方之后再评估。

---

## 14. 决策记录

| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | 交付边界 = schema + 只读构造器 + 测试 + 文档,零接线 | 契约若无生产者验证,字段可空性/单位/枚举都是白板产物;Inc 4 落地时才发现填不出来,改契约要动三个下游 |
| D2 | `direction = Literal["long","short"]`,观望返回 `None` | 与 `build_action_fields` 对模糊文本返回 `{None,None}`、不默认中性值的既有克制一致;short 侧无统计路径这个缺口在类型上可见,不被 `neutral` 掩盖;`position_size` 有确定符号供 Inc 5 |
| D3 | 双轴 confidence:定性 `Literal` + 可空 `SignalEvidence` | Inc 3 的封顶机制与 `is_high_confidence`(`phase_decision_guardrail.py:229`)零改即可作用;统计轴单列,`evidence=None` 是有意义的三态 |
| D4 | `invalidation` 结构化最小集(price / valid_until / note) | 两个可判定字段供 Inc 6,一个逃生口给结构性前提;不引入谓词 DSL |
| D5 | 呈现边界靠 import 白名单 drift-lock,不靠黑名单 | 白名单连没人想到的路径一并管住;且订正了「禁 `notification.py` import」的提法 —— 战略 :60 已判定既有 notification 不构成对外分发,真正锁的是 Inc 0 的零接线不变式 |
| D6 | 方案 1(防腐层)而非方案 3(统一重构) | 唯一同时满足 additive-only 验收线与「从既有字段长出来」的选项 |
| D7 | `risk_reward` 用 `@property` 而非 `computed_field` | 避开 `computed_field` × `extra="forbid"` 的 round-trip 陷阱(§2.10 实测) |
| D8 | `as_of` / `valid_until` 必须是完整 ISO-8601 datetime | 设计中期核实发现 `_validate_iso8601_timestamp`(`analysis_context_pack.py:28`)要求含 `"T"`,裸日期被拒。选择「收紧格式以复用既有校验器」而非「写第二份校验器」;日线写 `T00:00:00` 与引擎 `_to_epoch_ms_shanghai` 对纯日期坍缩到午夜的语义一致 |
| D9 | `attach_evidence` 用 `hit_sample is not None` 判别有无证据 | resolver 的 `_none` 哨兵是 `hit_sample=None` 且 `verified=False`;而「有桶但未通过超额判定」也是 `verified=False` 却带真实样本。用 `verified` 判别会丢掉后者 |
| D10 | `attach_evidence` 在 horizon 不匹配时 `raise` | 把 5 根窗口的统计附到 10 根窗口的信号上是编程错误,不是缺数据;必须响,不静默 |
| D11 | 共享谓词命名为 `trade_levels_invalid` 且兼管「非有限 / `<= 0`」,不只管排序 | 设计中期实测发现 `parse_sniper_value` 的字符串入口无正数/有限守卫(§2.4),会漏出 `0.0 / -5.0 / inf / nan`。若谓词只管排序,这些值会流进模型抛 `ValidationError` 而非 fail-closed 返回 `None`。命名与语义对齐 `is_invalid_price_level`,但方向感知、无 `current_price` 约束 |
| D12 | import 白名单含 `src/schemas/trade_signal.py` 自身,且加一条阳性对照断言 | 对抗式审查中三个互相看不见的视角独立撞到同一处:schema 模块的 docstring 几乎必然含 `trade_signal`,把它排除会让 lock 第一天就红。阳性对照则防止扫描器写坏后「空集 ⊆ 白名单」永远绿 |
| D13 | §10.3 哨兵的 `risk_reward` 取 99.0 而非真值 2.0 | 取 2.0 时「错误地读 `levels.risk_reward`」与「按公式重算」给出同一个数,断言失去区分力。99.0 让 §6.1「不双写」这条不变式真正可测 |
| D14 | 构造器纯度改为 AST 依赖面断言,不 monkeypatch `get_config` | `get_config` 不在 `trade_signal_builder` 的调用路径上,打桩它对任何实现都会通过 —— 是一条恒真测试。改测「该文件 import 了什么」这个可判定事实 |
