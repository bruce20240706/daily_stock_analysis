# LLM 数值校验守卫(Inc 3 — LLM Claim Validation)

> 设计权威来源:`docs/superpowers/specs/2026-07-09-llm-claim-validation-design.md`。本文档是面向使用者/运维的行为说明,细节以设计文档为准。

## 1. 要解决的问题

LLM 分析链路此前没有任何"生成后"的数值自校验。`fill_price_position_if_needed`(`src/analyzer.py`)只填补 LLM 留下的占位符 —— **LLM 若给出一个错误的具体数字,原样保留、既不覆盖也不标注**。守卫堵的就是这个洞。

守卫只做两类**确凿**校验,不做软性口径比对,也不做推断类判断:

- **转录类(transcription)**:LLM 复述 prompt 里已经给过它的数字(如 `ma5`、`current_price`、`profit_ratio`)却写错。它手上有正确答案,一旦不一致就是确凿的转录幻觉。
- **结构类(structural)**:LLM 自主生成的买卖计划(`sniper_points`:`ideal_buy` / `secondary_buy` / `stop_loss` / `take_profit`)内部不自洽,例如止损价高于入场价。这类计划不可执行。

**明确不做的是推断类校验**,例如 `support_level` / `resistance_level`。这两个值是 LLM 自主推断出来的,系统另有一套独立口径(`StockTrendAnalyzer._analyze_support_resistance`,取贴近现价的 MA5/MA10/MA20 与近 20 日高点)。两者口径本就不同,偏差是常态而非幻觉;若强行比对,只会制造噪声性误报。

## 2. 转录类判据:自校准容差

固定的绝对或相对容差都会在某个量级失效(相对容差对 `ma20=1800.42` 太松、对 `bias_ma5=0.02%` 太紧;绝对容差反之)。判据改为按 **LLM 自己声称的精度** 自动收紧:

```
|claimed − fact| ≤ max(10^(−d), |fact| × 1e-9)
```

- `d` = LLM 陈述值的十进制小数位数,钳制在 `[0, 8]`。
- `|fact| × 1e-9` 是浮点噪声下限,防止 float64 表示误差触发误报。
- `d` 钳到 `[0, 8]` 而非 `[0, 4]`,是为了覆盖 crypto 极小价标的(如 `ma5 = 1.23e-5`);若钳到 4,这类标的的容差会恒为 `1e-4`,比数值本身还大一个数量级,守卫对它们静默失效。

### `d` 必须用 `decimal.Decimal` 求,不能用 `split('.')`

```python
from decimal import Decimal
d = -Decimal(token).as_tuple().exponent
d = max(0, min(8, d))
```

朴素写法 `len(token.split('.')[-1])` 有一处会致命出错:对 `'1800'`(整串无小数点,`split('.')[-1]` 取到的是它自己)会返回 **4**,导致容差被算成 `1e-4` 而不是 `1.0` —— **任何无小数点的整数 claim 都会被误报**。`Decimal` 同时正确处理科学计数法(`'1.23e-05'` → `d=7`)与尾零保留(`'12.30'` → `d=2`)。

claim 若来自 JSON number,`json.loads` 会抹掉尾零(`12.30 → 12.3`),`d` 因此只会偏小 → 容差只会偏大 → **只可能漏报,绝不会误报**。

### 抽取正则必须含指数段与前导点分支

```python
r"[-+]?(?:\d+\.\d+|\.\d+|\d+)(?:[eE][-+]?\d+)?"
```

- **缺指数段**:对 `'1.23e-5'` 只抽出 `1.23`,值错 5 个数量级 —— crypto 极小价在 prompt 里就是这种形态。
- **缺前导点分支**:对 `'-.05%'`(合法的 LLM 字符串 claim)只匹配到 `05`,符号与数量级双双丢失,把正确 claim 判成幻觉。

两处遗漏都会造成**假警报**,比漏报更糟,因此正则同时收纳两种形态。

## 3. 结构类判据:签名即护栏

结构类校验 LLM **自主生成**的买卖计划是否内部自洽。它读 `dashboard.battle_plan.sniper_points` 的四个字段,用共享的 `parse_sniper_value`(与 `DatabaseManager._extract_sniper_points` **同一个函数**)抽数 —— 这保证「守卫判定的数」与「落库的数」永远是同一个。

### 判据

**仅当相关字段都成功抽出数值时才判**(缺失 → 跳过,不判违规)。`sniper_points` 非 dict / 空 → `not_applicable`(`reason="no_sniper_points"`);一条序关系都凑不出 → `not_applicable`(`reason="insufficient_fields"`)。

1. **抽出的值若非有限或 `<= 0` → `violation`**(不是「缺失」)
2. `stop_loss < ideal_buy`(两者皆存在时)
3. `ideal_buy < take_profit`(两者皆存在时)
4. `stop_loss < take_profit`(两者皆存在时)
5. `secondary_buy` 若存在:`stop_loss < secondary_buy` 且 `secondary_buy < take_profit`

`violations` 非空时即 `violation`,即便一条可比较的序关系都凑不出(例如只有 `ideal_buy: "-5"` 一个字段)。

### 判据 1 为什么是 `violation` 而不是静默丢弃

早期实现把非正值**静默丢弃**、当作字段缺失,于是 `{"ideal_buy": "-5", "stop_loss": 1, "take_profit": 2}` 报 `ok` —— 负数买入价被放过。`AGENTS.md` 明列「用 broad fallback、静默降级掩盖不清晰的契约」为低质量特征。

改为 `violation` 之后,判据 1 **恰好只对「会被真正落库的非正数」报警**,这正是本函数的头号约束「与落库口径一致」:

| claim | `parse_sniper_value` | DB 存的 | 判定 |
| --- | --- | --- | --- |
| `-5`(数值) | `None`(上游即滤除) | NULL | 缺失(不报警) |
| `"-5"`(字符串) | `-5.0` | `-5.0` | **violation** |
| `"0元"` | `0.0` | `0.0` | **violation** |
| `"inf"` | `inf` | `inf` | **violation**(非有限) |

数值与字符串之间的不对称由 `parse_sniper_value` 承担(它对落库行为有「逐字等价」保证,不可改);判据 1 只负责把**已经透出来的**非正/非有限值显式报出来。

### 签名即护栏:为什么不复用 `is_invalid_price_level`

```python
def validate_structure(sniper_points: Any) -> Dict[str, Any]: ...
```

**签名只接收 `sniper_points`。** 不传 `current_price`、不传 `result`。

`src/services/volume_price_signals.py` 的 `is_invalid_price_level(*, entry, stop, target, current_price)` 有一条判据是 `entry > current_price → invalid`。那条对**它自己**成立:它校验的是 `derive_price_levels` **反算出的** long-setup entry —— 按构造 entry 就是「MA20 与近 20 日 swing low 中 **≤ 现价** 的较高者」,`entry ≤ current_price` 是它的**构造不变式**。

LLM 的 `ideal_buy` **没有这个不变式**。「突破 12.8 元买入」(现价 12.3)是完全合法的交易计划。照搬那条判据会**系统性误杀所有突破买入计划**。

因此结构判据自写,不调 `is_invalid_price_level`。**主要防线是签名** —— 一个拿不到 `current_price` 的函数,不可能拿它做判据。这比任何测试都强:误用在类型上就无法表达。

### 动作

结构 violation → 追加 action code `sniper_points_unexecutable`,在报告里标注该买卖计划不可执行。

**不封顶置信度**(证据类型不同:数字不可信 ≠ 计划不可执行),**不清空 `sniper_points` 字段** —— `storage._extract_sniper_points` 仍要读它并落库,清空会破坏落库契约。标注即可,让消费方决定。

---

## 4. `dashboard["claim_validation"]` 字段契约

守卫开启且守卫判定发生时,`result.dashboard` 会追加一个顶层键(与 `decision_stability` / `phase_decision` 同级):

```python
{
    "applied": True,
    "transcription": {
        "status": "ok" | "mismatch" | "not_applicable",
        "reason": str | None,          # not_applicable 时说明原因,如 "no_prompt_facts"
        "checked": int,                # 实际参与比对的字段数
        "mismatches": [
            {"field": "price_position.ma5", "claimed": 12.5, "fact": 11.83, "tolerance": 0.1}
        ],
    },
    "structural": {
        "status": "ok" | "violation" | "not_applicable",
        "reason": str | None,          # not_applicable 时,如 "insufficient_fields" / "no_sniper_points"
        "violations": ["stop_loss(13.0) >= ideal_buy(12.5)"],
    },
    "actions": ["confidence_capped_claim_mismatch", "sniper_points_unexecutable"],
}
```

**开关关闭时整个键不写**,`result.dashboard` 字节级不变。

## 5. 动作:标注 + 分级降权

| 触发 | 动作 |
| --- | --- |
| 转录 mismatch ≥ 1 条 **且** 当前置信度为「高」 | `confidence_level` 高→中(本地化文本,复用 `phase_decision_guardrail` 的 `"Medium"`/`"中"`),并追加 action code `confidence_capped_claim_mismatch` |
| 结构 violation ≥ 1 条 | 追加 action code `sniper_points_unexecutable`;**不清空 `sniper_points` 字段**(落库仍要读它) |

**action code 只在真实 cap 发生时才发。** 若置信度已经是「中」或「低」,即便转录 mismatch 存在,cap 是 no-op,`confidence_capped_claim_mismatch` 不会出现在 `actions` 里 —— 但 mismatch 判定本身仍完整写在 `transcription.status == "mismatch"` 里,不会丢信息。这与仓库既有先例 `phase_decision_guardrail` 的 `confidence_capped_core_data_degraded` 门控方式一致:只在降级真的发生时才 append action code,名字与实际动作严格对应。

**cap 是单调的**:仅当置信度仍为「高」时降到「中」,已是中/低则永不回撤 —— 保证它不会撤销 phase guardrail 已经施加的更严格降级(见 §6)。

**守卫绝不覆盖 LLM 给出的数值、不改变买/卖/观望方向、不触发 LLM 重试。** 覆盖等于系统替 LLM 编答案,且单改一个字段修不好建立在错数之上的整段推理;改写决策方向是从"数字不可信"到"应该观望"的逻辑跳跃,方向判定是 `stabilize_decision_with_structure` 基于结构做的独立判断,证据链不同;触发重试成本高且未必修复转录幻觉,还会与既有 `check_content_integrity` 重试循环耦合。

## 6. 挂载点:两条不变式

守卫拆成两个函数,插在 pipeline 的 Step 7.7 两端:

| 步 | 函数 | 位置 |
| --- | --- | --- |
| A | `extract_llm_claims(result)` | `analyze()` 返回后、**Step 7.5 之前**(即在任何 in-place 回填之前) |
| B | `apply_claim_validation(result, claims, facts, language=...)` | `apply_phase_decision_guardrails` **之后** |

### 不变式 A:提取必须在任何确定性回填之前

pipeline 在 Step 7.5–7.7 之间有一整串 in-place 改写 `result.dashboard` 的确定性回填(`normalize_chip_structure_availability` → `fill_chip_structure_if_needed`、`fill_capital_flow_if_needed`、`fill_margin_if_needed`、`fill_ggt_if_needed`、`fill_price_position_if_needed`)。任何一个跑在 A 之前,都会让 A 采到系统的值而不是 LLM 的 claim,后果分两类:

- **假警报**:`fill_price_position_if_needed` 把 `trend_result` 的重算值填进 `price_position`。重算值本就可能与 prompt 里喂进去的值不同源(`StockDaily.ma5` 是 ingestion 时算的,`StockTrendAnalyzer` 在约 60 日窗口重算),于是系统自己填的值被误判成"LLM 幻觉"。
- **空转校验(tautology)**:`normalize_chip_structure_availability` 用 `_is_value_placeholder` 为门,把 `chip_data` 回填进 `chip_structure`(`profit_ratio` 写成同样的 `f"{pr:.1%}"` 格式)。这与 prompt 里的 `chip` 块同源同格式 —— 若 A 挂在它之后,LLM 一旦省略该字段,守卫就是拿系统的值和同源的 fact 自己跟自己比,恒等式,永远 pass,`checked` 计数虚高,制造虚假覆盖率。

因此 A 必须提到**所有** `fill_*` / `normalize_*` 调用之前 —— 这是一条位置不变式,不依赖"哪个 fill 用了哪个数据源"这种逐字段论证。

### 不变式 B:判定必须在 phase guardrail 之后

`apply_phase_decision_guardrails` 在入口一次性计算 `initially_high_confidence`,随后有两个降级分支消费它:核心数据 degraded 时高→中;保守盘口阶段却出现立即买卖信号时高→低。

若 claim-validation 先把"高"降到"中",`initially_high_confidence` 就会变成 `False`,这两个分支全部静默失效 —— 包括更严厉的高→低安全降级。结果是开启防幻觉守卫反而让阶段护栏变得不保守。

把 B 放在 guardrail 之后即可根除:guardrail 先看到原始"高"并施加自己的降级;claim 的 cap 是单调的(§5),永不回撤 guardrail 已做的降级。

**两条不变式在非-agent 与 agent 两条 pipeline 路径上都要成立**,四个插入点(两路径 × 两步)缺一即会导致行为漂移。

## 7. 校验字段范围

### 转录类覆盖的 9 个字段

`current_price`(值集合,`today['close']` 与 `realtime['price']` 任一命中即算通过)、`ma5`、`ma10`、`ma20`、`bias_ma5`、`volume_ratio`、`turnover_rate`、`profit_ratio`、`avg_cost`。

facts 记录的是"渲染后的语义值"而非原始值:`profit_ratio` 从 `f"{v:.1%}"` 反解(而不是 `v * 100` 直接重算,避免浮点误差如 `72.34000000000001`),`bias_ma5` 从 `f"{v:+.2f}"` 反解。裸值渲染的字段(`ma5` / `close` / `avg_cost` / `volume_ratio`)本就无格式化,fact 即原值。

### 刻意排除的 3 类字段

| 字段 | 排除理由 |
| --- | --- |
| `chip_structure.concentration` | prompt 里同时存在 `concentration_90` 与 `concentration_70` 两个值,LLM schema 只有一个 `concentration` 字段,对应关系有歧义,不猜。 |
| `capital_flow.*` | 根本不是 LLM 的 claim。`fill_capital_flow_if_needed` 在 LLM 输出**之后**才把资金面数据确定性回填进 `data_perspective.capital_flow`(presence-only,对决策只读)。没有 LLM 转录动作,就没有转录锚点。 |
| `price_position.support_level` / `resistance_level` | 推断类,见 §1 的非目标说明。 |

## 8. agent 路径的 `not_applicable`

仓库有两条个股分析路径:非-agent 路径经 `_format_prompt` 生成 prompt 文本;agent 路径(`_analyze_with_agent`)不经过 `_format_prompt`,因此没有 `prompt_facts` 可比对。

两类校验在 agent 路径上的适用面不同:

| 校验 | 依赖 | agent 路径行为 |
| --- | --- | --- |
| 转录类 | 需要 `prompt_facts` | `prompt_facts is None` → `status="not_applicable"`,`reason="no_prompt_facts"` |
| 结构类 | 只读 `dashboard` 自身(`sniper_points`) | 照常运行 |

agent 路径的转录类降级是显式的 `not_applicable`,是 fail-closed 行为:不报错、不阻塞、不假装通过。

## 9. 配置项

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `LLM_CLAIM_VALIDATION_ENABLED` | `false` | 是否启用本守卫。opt-in,默认关闭时 `collect_prompt_facts` 一次也不被调用,`result.prompt_facts` 恒为 `None`,`apply_claim_validation` 整体早退、不写 `claim_validation` 键 —— 报告与通知**字节级不变**。 |

该开关是用户可见能力开关(开启后报告可能多出一段"数值校验"提示、置信度可能被封顶),因此注册进 `config_registry`,Web 设置页 → AI 模型分类下可见可开。

## 10. 两套渲染引擎的 presence-only 行为

仓库有两套报告渲染引擎,由 `config.report_renderer_enabled`(默认 `False`)切换:传统 Python 拼接(`src/notification.py::generate_dashboard_report`,默认生效)与 Jinja(`templates/report_markdown.j2`,`REPORT_RENDERER_ENABLED=true` 时生效)。两侧都已接入 `claim_validation` 提示行渲染,行为一致:

- 转录 `ok` / `not_applicable` 且结构 `ok` / `not_applicable` → **不渲染任何内容**,报告与守卫关闭时逐字节一致(presence-only)。
- 转录 `mismatch` → 渲染一行"检出 N 处与输入数据不一致的数值陈述,已下调本次结论置信度"(中)/ 对应英文文案。
- 结构 `violation` → 渲染一行"买卖计划数值不自洽,该计划不可执行"(中)/ 对应英文文案。
- 双语文案在 `src/report_language.py` 的 `_REPORT_LABELS`(`zh` 与 `en` 两块)中各有一份,渲染时按 `report_language` 选取。

不渲染提示行的面(跟随既有 `margin_trading` / `phase_decision` 的既有边界,当前对这些面全不渲染):`generate_wechat_dashboard`、`generate_single_stock_report`、`report_wechat.j2`、`report_brief.j2`。被封顶后的 `confidence_level` 本身仍会在所有面显形,只是"数值校验"提示行不会。

## 11. 已知边界

- 只覆盖个股分析路径。大盘复盘(`generate_market_review`,纯自由文本、不回读数字)与图片提取(`extract_stock_codes_from_image`,输出无价位数值)无可校验的数值 claim 面。
- agent 路径无转录校验(见 §8),结构类仍覆盖。
- crypto 标的通常没有 `chip` 块,`profit_ratio` / `avg_cost` 对 crypto 恒缺失、不参与校验 —— 这是"缺失即 fact 缺失"的正常工作方式,不是缺陷。
- 区间型 `ideal_buy`(如 `'180-182'`)取最后一个数,与落库口径(`parse_sniper_value`)一致;区间本身无单一小数位数 `d`,不参与转录类校验。
