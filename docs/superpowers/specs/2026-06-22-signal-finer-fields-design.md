# M3.1 细粒度信号字段(horizon / plan_quality / status) 设计

- 日期:2026-06-22
- 状态:设计已评审通过(含设计-vs-代码坐实修正),待写实现计划(writing-plans)
- 基线分支:`main`(当前 head 含 M3/M4-A/M4-B,signals/board/backtest 链路均已在 main)
- 关联:#1390 P0(已交付 action/action_label + price_lines)显式延期了 `horizon/plan_quality/status` 等更细粒度字段(`docs/full-guide.md:1267`);本片即该延期项的独立交付。
- 接地证据:设计前对照真实代码逐点坐实,修正了 4 处与代码不符(计算层错位/看板 horizon 与 hit_rate 同源/consistency 枚举精度/LLM marker 边界),下文锁定项已吸收。

---

## 1. 背景与目标

信号资产(`/signals`、信号看板)当前每条 marker 已带 `hit_rate/hit_sample/verified/ci_low/ci_high/baseline_excess`(M3-A6 回填),以及响应级 `price_lines`(entry/stop/target,#1390 P0 由 `derive_price_levels` 反算)。但用户看不到三个能显著提升可读性/可执行性的细粒度字段:

1. **`horizon`**:这条信号的胜率/CI **到底是在多长的前看窗口上验证出来的**(回测里 `signal_stats` 按 signal_type×market×interval×horizon 存,胜率/Wilson CI/超额都是在某个 horizon 上算的,但该窗口从未露给用户)。
2. **`plan_quality`**:这条信号的**交易计划是否完整、可执行、不自相矛盾**(与"多可信"正交——可信度已由 verified/CI 表达)。
3. **`status`**:这条信号的**生命周期**(刚发 / 仍在验证窗口内 / 窗口已过)。

目标(最小、additive、transient-only、零 DB 迁移):把这三个**确定性派生**字段补进实时信号载荷(`/signals`)、信号看板(BoardEntry),并在 K线 marker tooltip/信号详情 + 看板列呈现。

## 2. 非目标

- **持久化 / DB 迁移 / 历史回填**:明确不做(延续 #1390 P0 延期立场;且 `status` 时间相对,落历史会按"今天"重算而误导)。历史展示不变。
- **盘中/分钟级**:无关(那是另一个独立 epic)。
- **改动既有字段来源/行为**:不改 hit_rate/CI/verified/baseline_excess/price_lines/consistency/resonance 的产出逻辑;不改回测引擎。
- **LLM marker 伪装成规则信号**:LLM 点不赋 horizon/status(见 §6)。
- **新数据源 / 新 token**:不引入。仅复用既有日线数据、signal_stats、price_lines、consistency。
- **新增运行时开关**:不新增配置项;status 回退窗口复用既有 `SIGNAL_BACKTEST_HORIZON_BARS`(默认 10)。

## 3. 关键决策(已锁定)

| # | 决策 | 取值 |
| --- | --- | --- |
| D1 | horizon 语义 | = 该 marker 的 hit_rate/CI 实际被验证的前看窗口(bar 数,取自命中的 signal_stats 行)+ 人读 label;**与 hit_rate 同源** |
| D2 | plan_quality 规则 | price_lines 完整度 + consistency(与可信度正交);确定性三档 |
| D3 | status 语义 | active/aging/expired,纯 timestamp + 窗口推导,无向前扫价 |
| D4 | 持久化 | transient-only,实时载荷 + 看板 compute-on-read;零 DB/迁移/回填;历史不变 |
| D5 | 契约位置 | horizon/status → per-marker(SignalMarker,仅 rule);plan_quality → 响应级(SignalsResponse);看板行三者齐 |
| D6 | 计算层 | **编排层 `build_signals_for_code`**(price_lines 填完后、df 在手处);**非** `build_signals_payload`(那里 price_lines 恒 null、无 df) |
| D7 | 看板聚合 | horizon+status 取**驱动 hit_rate 的同一代表 rule marker**(`_hit_fields_from_markers` 既有选择);plan_quality 取该股响应级值。整行自洽 |
| D8 | 呈现 | API(SignalMarker/SignalsResponse/BoardEntry)+ Web(K线 marker tooltip/信号详情 + 看板列) |

## 4. 架构与模块边界(全 additive / nullable)

### 4.1 契约(`api/v1/schemas/stocks.py`)

`SignalMarker`(:111)追加:
```python
horizon_bars: Optional[int] = Field(None, description="该信号 hit_rate/CI 的验证前看窗口(bar 数);仅 rule、命中 signal_stats 时有值")
horizon_label: Optional[str] = Field(None, description="horizon 人读标签,interval+report_language 感知")
status: Optional[Literal["active", "aging", "expired"]] = Field(None, description="信号生命周期;仅 rule")
```
`SignalsResponse`(:143)追加:
```python
plan_quality: Optional[Literal["high", "medium", "low"]] = Field(None, description="交易计划质量:price_lines 完整度 + consistency;无 price_lines→null")
```
`BoardEntry`(:169)追加:`horizon_bars` / `horizon_label` / `status`(取代表 rule marker)+ `plan_quality`(该股值),字段定义同上。

### 4.2 派生规则(全确定性)

**horizon**(per rule marker):resolver `resolve_marker_hit_fields`(经 `build_signals_payload` 的 `hit_fields_resolver` 注入,`signal_board_service.py:116`)当前返回 hit_rate/hit_sample/verified/ci_low/ci_high/baseline_excess;**追加返回命中 `signal_stats` 行的 `horizon`**(该行即 hit_rate/CI 的来源,horizon 字段已在 `SignalStatRow`/`signal_stats_repo`)。`_marker_from_vpsignal`(`signals_service.py:120`)把 `horizon` 映射为 `horizon_bars`,并由 (bars, interval, report_language) 合成 `horizon_label`(如 zh `10 根日线 bar` / en `10 daily bars`;不做日历换算,避免 per-interval 假设)。无命中 → `horizon_bars/label = None`。

**plan_quality**(响应级):在编排层 `build_signals_for_code` **price_lines 填完之后**(`signal_board_service.py:124` 之后)用真实 price_lines + consistency 算:
- price_lines 全 null(反算不出/degraded)→ `None`
- `entry is None or stop is None`(无法成计划)**或** `consistency == "conflict"` → `low`
- `entry and stop and target` 齐全 **且** `consistency == "consistent"` → `high`
- 其余(如 target 缺、或 consistency ∈ {`divergent`,`unknown`,`stale`})→ `medium`(封顶,不给 high)

**status**(per rule marker):编排层用 df(bar 序列)把每条 rule marker 映射到其 bar 索引(marker 由引擎在 df 上生成,timestamp↔bar 对齐既有);`bars_since = last_index - marker_index`;窗口 `W = horizon_bars`(命中时)否则 `config.signal_backtest_horizon_bars`(默认 10):
- `bars_since == 0` → `active`;`0 < bars_since < W` → `aging`;`bars_since >= W` → `expired`

### 4.3 计算层与数据流(关键:编排层,非 payload 层)

`build_signals_for_code`(`signal_board_service.py:57`)是**单股编排**,`/signals` 单股端点与看板都走它(stocks.py↔board_service 互调,`:64`)。流程内**已有** df、markers(经 resolver 回填 hit 字段)、consistency、并在 `:123-124` 填实 price_lines。在此编排内新增:
1. resolver 返回的 horizon 已随 marker 带出(§4.2)→ rule marker 得 `horizon_bars/label`。
2. price_lines 填完后,算响应级 `plan_quality` 写入 payload。
3. 用 df 算每条 rule marker 的 `status` 写回 markers。
4. `_entry_from_board_signals`(`:186`)构造 BoardEntry 时:**扩展 `_hit_fields_from_markers`(`:171`)使其在返回 hit_rate/CI 的同时,从同一条代表 marker 一并返回 `horizon_bars/horizon_label/status`**——同一 marker、同一 dict,**结构上保证 D7 同源**(无法因后续改动而 horizon 与 hit_rate 取自不同 marker)。plan_quality 取 payload 的响应级值。

**不进** `build_signals_payload`(`signals_service.py:212`):该函数 price_lines 恒 null(`:278`)、无 df,只负责 markers+consistency 组装。horizon 经 resolver/marker builder 进入是唯一在该层的接触点(纯 additive)。

### 4.4 Web 呈现(`apps/dsa-web`)

- 类型(`src/types/kline.ts` 的 SignalMarker/SignalsResponse/BoardEntry)追加可选字段。
- K线 marker tooltip/信号详情:rule marker 显示 `status` 徽标 + `horizon_label`;面板头/响应级显示 `plan_quality`。
- 信号看板列:显示行级 `plan_quality` + 代表信号的 `status`/`horizon_label`。
- 旧字段渲染不动;null 时不渲染对应元素。

### 4.5 关键复用 / 不动

复用 resolver/`_marker_from_vpsignal`/`_hit_fields_from_markers`/`build_price_lines`/`derive_price_levels`/`compute_consistency`/`config.signal_backtest_horizon_bars`。**不动**:hit_rate/CI/verified/baseline_excess/price_lines/consistency/resonance 产出、回测引擎、历史、DB/storage、既有 action/action_label。

## 5. 字段契约

- 三新字段全 Optional/nullable;`status` 仅 `active|aging|expired`;`plan_quality` 仅 `high|medium|low`。
- horizon/status **仅对 `source=="rule"` 的 marker** 赋值;`source=="llm"` 的点 → `horizon_bars/label=None`、`status=None`(见 §6)。
- plan_quality 是**响应级/per-stock**(其输入 price_lines、consistency 均为响应级),不在 per-marker。
- 看板 horizon/status 与该行 hit_rate **同源**(D7),保证 "hit_rate X% 在 horizon Y bar 上" 自洽。
- 不进 DB、不进历史载荷;旧客户端忽略未知字段。

## 6. 错误处理与边界

- 无 signal_stats 命中(冷启/非自选池)→ `horizon_bars/label=None`;status 仍用配置默认窗口(默认 10)算(timestamp 充分)。
- price_lines 全 null(反算不出/degraded)→ `plan_quality=None`。
- LLM marker:非回测规则类型,无 horizon 语义;且恒在 latest_bar → `horizon/status=None`(不伪装成有验证窗口的规则信号)。
- degraded 响应(无历史数据,`signal_board_service.py:72-80`)→ markers 空、price_lines null → 三字段自然 None。
- consistency 五态精确映射(§4.2),不把 `divergent/unknown/stale` 误判为 high。
- bars_since 映射失败(marker 找不到对应 bar,理论不应发生)→ 该 marker `status=None`,不抛、不阻断。

## 7. 兼容性

- 契约纯追加可选字段 → 旧 Web/桌面/历史回放忽略未知字段,不破坏 payload。
- 既有 hit_rate/CI/price_lines/consistency/resonance/action 全部零改动 → 现有行为不变。
- 零新配置/数据源/token;`.env.example` 不改。
- horizon 经 resolver 多带一个字段:resolver 返回 dict 追加 `horizon` 键,旧消费方(`_hit_fields_from_markers` 等)不读则无影响;需确认 `_marker_from_vpsignal` 只取所需键。

## 8. 测试矩阵(pytest 离线确定性 + 前端单测)

- **plan_quality**:high(全线+consistent)/ low(缺 entry 或 stop;或 conflict)/ medium(target 缺;或 divergent/unknown/stale)/ None(price_lines 全 null)。
- **status**:bars_since=0→active;0<·<W→aging;≥W→expired;W 取 horizon_bars vs 配置默认两路径各测。
- **horizon**:命中 signal_stats → horizon_bars=该行 horizon、label 含 bars+interval(zh/en);未命中 → None;label 双语。
- **LLM marker**:source=llm → horizon/status=None。
- **看板同源(D7 回归)**:构造多条不同 signal_type 的 rule marker,断言 BoardEntry 的 horizon/status 与其 hit_rate 来自**同一条代表 marker**(不串成不同 marker)。
- **编排层填充**:`build_signals_for_code` 后 payload.markers 的 rule 项有 horizon/status、payload 有 plan_quality;`build_signals_payload` 单独调用(无 price_lines)→ plan_quality 不在该层、price_lines 仍 null(锁定计算层)。
- **degraded**:无历史 → 三字段 None,不抛。
- **Web**:kline 类型可选字段、tooltip 渲染(status 徽标/horizon_label/plan_quality)、看板列渲染;null 不渲染。

## 9. 验证门禁

- 后端:`./scripts/ci_gate.sh`(flake8 critical + `pytest -m "not network"`)。
- 前端(改了 `apps/dsa-web`):`npm ci && npm run lint && npm run build`(无空格 worktree 跑)。
- API/Schema 联动:契约追加可选字段,交付说明写明前端忽略未知字段、无破坏。
- 文档:CHANGELOG `[Unreleased]` 扁平追加;更新信号资产相关专题(signal 字段说明)。

## 10. 风险与回滚

- 风险:三字段对既有产出只读、纯追加;最大风险是**看板 horizon 与 hit_rate 不同源**(已由 D7 + §8 同源回归锁死)与**计算层错位**(已由 D6 钉死编排层 + §8 锁定测)。status 时间相对仅用于实时,不入历史(D4)规避误导。
- 回滚:撤新增 schema 字段 + 编排层三处计算 + resolver 的 horizon 透出 + Web 渲染;既有链路未动,回滚面小。

## 11. 已知局限(v1)

- 仅实时载荷与看板;历史不显示这三字段(transient-only)。
- horizon/status 仅 A股/港股/美股/crypto 的 rule 信号(signal_stats 有数据的自选池更全);非自选池冷启时 horizon=None、status 用默认窗口。
- plan_quality 是启发式三档(price_lines 完整度 + consistency),非 ML 评分;刻意确定性。
- 看板每行只表达一条代表信号的 horizon/status(非全 marker),与既有 hit_rate 口径一致。
- 看板代表 marker = `_hit_fields_from_markers` 选的**第一条 rule marker**(即窗口内最早那条),故看板 `status` 反映的是该代表信号的生命周期(倾向 aging/expired),**不是"该股是否有任一条刚发的新信号"指标**。这是"整行同源自洽"换来的取舍;若未来要"有无新信号"语义,需另立(且会改 hit_rate 来源,本片不做)。

---

## 实现切片(供 writing-plans 参考)

1. `api/v1/schemas/stocks.py`:SignalMarker(+horizon_bars/horizon_label/status)、SignalsResponse(+plan_quality)、BoardEntry(+四字段)+ schema 向后兼容测试。
2. `src/services/signals_service.py`:resolver `resolve_marker_hit_fields` 透出 horizon;`_marker_from_vpsignal` 映射 horizon_bars/label(双语);单测。
3. `src/services/signal_board_service.py`:`build_signals_for_code` 内 price_lines 填完后算 plan_quality + 用 df 算各 rule marker 的 status;**扩展 `_hit_fields_from_markers` 一并返回代表 marker 的 horizon_bars/horizon_label/status**(结构保证 D7 同源);`_entry_from_board_signals` 用之 + 响应级 plan_quality;单测(含 D7 同源回归 + 计算层锁定 + degraded)。
4. `apps/dsa-web`:kline 类型 + marker tooltip/信号详情 + 看板列渲染 + 前端单测。
5. 文档:CHANGELOG 扁平行 + 信号字段专题说明 + 全量后端门禁 + 前端门禁。

---

## 附录:writing-plans 期对照真实代码的两处精化(2026-06-22,supersede 上文)

写实现计划时把设计对照真实代码逐点坐实,有两处实现期精化(用户已知会),覆盖上文相关措辞:

1. **`horizon_label` 不再是后端字段,改由前端格式化**(覆盖 §4.1/§4.2/§4.4 中"后端 horizon_label"措辞):坐实发现 `_marker_from_vpsignal` 无 `report_language`,且既有 marker `reason` 本就 Chinese-native 不随 report_language 本地化。故后端只出 `horizon_bars`(int);人读标签由前端 `apps/dsa-web/src/utils/credibility.ts` 的 `formatHorizon` 按 UI 语言格式化(更干净的 i18n,且与既有 marker 语言惯例一致)。`SignalMarker`/`BoardEntry` 不含 `horizon_label`。
2. **看板生命周期字段命名为 `signal_status`**(覆盖 §4.1/§5 中 BoardEntry 的 `status` 措辞):`BoardEntry.status` 已占用为 `ok|degraded`,故看板生命周期字段用 `signal_status`(`active|aging|expired`);`SignalMarker` 仍用 `status`(该模型无冲突,与上文一致)。

详见实现计划 `docs/superpowers/plans/2026-06-22-signal-finer-fields.md`。
