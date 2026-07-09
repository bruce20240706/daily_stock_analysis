# Inc 3 — LLM claim-validation 守卫(设计)

> 日期:2026-07-09　类型:设计/spec　战略出处:`docs/strategy-actionable-signal-system.md` §5 Inc 3(L6 防幻觉,P0)
> 借鉴:qrak/LLM_trader(结论 vs 计算值交叉核对)、FailSafeQA(降级即拒答)
> 工程纪律:opt-in / 默认字节级不变 / 追加字段优先 / 离线 fixture 可验

---

## 0. 摘要

LLM 分析链路当前**没有任何生成后的数值自校验**。`fill_price_position_if_needed`(`src/analyzer.py:1052`)只填补 LLM 留下的占位符——**LLM 若给出一个错误的具体数字,原样保留、既不覆盖也不标注**。这是本增量要堵的洞。

守卫做两类**确凿**校验,不做软性口径比对:

1. **转录类(transcription)** —— LLM 复述 prompt 里已经给过它的数字却写错。这是它手上有正确答案的情况,不一致即确凿幻觉。
2. **结构类(structural)** —— LLM 自主生成的买卖计划内部不自洽(如止损价高于入场价)。这类计划不可执行。

不一致时**标注 + 分级降权**:转录不一致 → 封顶置信度(高→中);结构违规 → 标注该买卖计划不可执行。**不覆盖数值、不改决策方向、不触发重试。**

---

## 1. 目标与非目标

### 1.1 目标

- 让 LLM 编造的数值在进入报告与通知之前被**逮住并标注**,并使其携带的高置信度被撤销。
- 全程 opt-in;开关关闭时**字节级不改变**任何既有行为。
- 校验逻辑离线可验,不依赖网络与真实 LLM。

### 1.2 非目标(明确不做)

| 不做 | 理由 |
| --- | --- |
| 校验 `support_level` / `resistance_level` | LLM 自主推断,系统另有一套口径(`StockTrendAnalyzer._analyze_support_resistance` 取贴近的 MA5/MA10/MA20 与近 20 日 high)。两者口径本就不同,偏差是常态而非幻觉。软标注只会制造噪声。 |
| 用计算值覆盖 LLM 的错误数字 | 覆盖等于系统替 LLM 编答案,且单改一个字段修不好建立在错数之上的整段推理。掩盖问题比暴露问题更糟。 |
| 改写 `decision_type` / `operation_advice` | 从「数字不可信」跳到「所以应该观望」是逻辑跳跃。方向判定是 `stabilize_decision_with_structure` 基于**结构**做的独立判断,证据链不同。 |
| 触发 LLM 重试 | 成本是多一次 LLM 调用,而转录幻觉重试未必修复;且会与既有 `check_content_integrity` 重试循环耦合。 |
| 大盘复盘路径(`generate_market_review`) | 输出是纯 Markdown 自由文本,下游 `_split_report_sections` / `_inject_data_into_review` 只做文本切段,**不回读 LLM 报出的数字** → 无可校验锚点。 |
| 图片提取路径(`extract_stock_codes_from_image`) | 输出仅 `(code, name, confidence)`,无价位/指标数值。 |
| Web 前端徽章 | 对齐 `margin_trading` / `ggt_context` 先例(无专用 Web 组件),经报告 payload + 通知 markdown 呈现。 |

---

## 2. 事实底座(全部已在代码中核实)

| 事实 | 锚点 |
| --- | --- |
| LLM 有三条调用路径,只有个股分析产结构化 JSON | `src/analyzer.py:2912` `GeminiAnalyzer.analyze` / `src/market_analyzer.py:697` / `src/services/image_stock_extractor.py:283` |
| `_format_prompt` 全仓**只被 `analyze()` 调用一次** | `src/analyzer.py:2985` → `src/analyzer.py:3125` |
| agent 路径**不经过 `_format_prompt`** | `src/core/pipeline.py:993` `_analyze_with_agent` |
| 但两条路径在 Step 7.7 **汇合**,跑同一串护栏 | 非-agent `pipeline.py:640-651`;agent `pipeline.py:1167-1182` |
| `fill_price_position_if_needed` 只填占位符,不覆盖 LLM 的错数字 | `src/analyzer.py:1052`,判定用 `_is_value_placeholder` |
| 现有护栏全是方向级/存在性级/阶段级,**无数值级** | `stabilize_decision_with_structure:1103` / `compute_consistency`(`signals_service.py:82`)/ `check_content_integrity:213` |
| `AnalysisReportSchema` 只做**校验**,返回值被丢弃;`result.dashboard` 是原始 dict | `src/analyzer.py:3868` `model_validate(...)` 结果未使用;`:3876` `dashboard = data.get('dashboard')` |
| 落库链路无 Pydantic 过滤 | `storage.save_analysis_history:1485` → `_build_raw_result` → `json.dumps(ensure_ascii=False, default=str)` |
| `AnalysisResult.to_dict()` 是**显式枚举**,新增 dataclass 字段不会泄漏进 `raw_result` | `src/analyzer.py:1782-1800` |
| API 层无描述 dashboard 的 Pydantic model | `api/v1/schemas/history.py:253` `raw_result: Optional[Any]` |
| 唯一的 `asdict()` 通用 helper 只服务 alphasift,从不接触 `AnalysisResult` | `api/v1/endpoints/alphasift.py:919` `_to_plain`,调用点全在同文件 |
| `self.analyzer` 是**单例**,在 `ThreadPoolExecutor` 中跨线程共享 | `pipeline.py:140` 构造,`:579` 并发调用 |
| `_sanitize_trend_analysis_for_prompt` 是**纯函数**(`trend_dict = dict(trend)`) | `src/analyzer.py:689-737`,docstring: "on a derived copy" |
| 两个 prompt 分支(legacy / 非 legacy)的趋势表**数值字段完全相同**,仅标签文案不同 | `src/analyzer.py:3407`(legacy)与 `:3437`(非 legacy) |
| **Step 7.5–7.7 有一串 in-place 改写 `result.dashboard` 的确定性回填**,全部早于原 Step A 位置 | Step 7.6 `normalize_chip_structure_availability` → `fill_chip_structure_if_needed`(`analyzer.py:819`,写 `chip_structure`)/ Step 7.6b `fill_capital_flow_if_needed`(`:893`)/ 7.6c `fill_margin_if_needed`(`:944`)/ 7.6d `fill_ggt_if_needed`(`:1000`)/ 7.7 `fill_price_position_if_needed`(`:1052`) |
| `_build_chip_structure_from_data` 回填的 `profit_ratio` 是**字符串** `f"{pr:.1%}"`,与 prompt 的 `{:.1%}` 同源同格式 | `src/analyzer.py`(`_build_chip_structure_from_data`);prompt 侧 `:3379`;`chip_data` 侧 `pipeline.py:349, 776` |
| `_refresh_decision_action_for_final_result` **不消费 `confidence_level`** | `src/core/pipeline.py:1475`,函数体内 `confidence` 零命中 → B 步 cap 置信度不会与它重算的 action 标签冲突 |

---

## 3. 架构

### 3.1 两步拆分 —— 这不是风格问题,是正确性要求

守卫拆成两个函数,插在 Step 7.7 的**两端**:

| 步 | 函数 | 位置 | 性质 |
| --- | --- | --- | --- |
| A | `extract_llm_claims(result)` | **`analyze()` 返回后、任何 `fill_*` / `normalize_*` 之前**(Step 7.5 之前) | 纯读,零副作用,返回 claim 快照 |
| B | `apply_claim_validation(result, claims, facts, *, language)` | **`apply_phase_decision_guardrails` 之后** | 判定、写 dashboard、封顶置信度 |

#### 不变式:**A 必须在任何确定性回填之前**

pipeline 在 Step 7.5–7.7 之间有**一整串** in-place 改写 `result.dashboard` 的确定性回填(见 §2 事实底座)。任何一个跑在 A 之前的回填,都会让 A 采到**系统的值而不是 LLM 的 claim**,后果分两类:

- **假警报**:`fill_price_position_if_needed` 把 `trend_result` 的**重算值**填进 `price_position`。重算值本就可能 ≠ prompt 里喂进去的 DB 值(`StockDaily.ma5` 由 ingestion 时算,`StockTrendAnalyzer` 在 ~60 日窗口重算,`derive_price_levels` 内又是 `close.rolling(20)`)。于是系统自己填的值被判成「LLM 幻觉」。
- **空转校验(tautology)**:`normalize_chip_structure_availability`(Step 7.6)→ `fill_chip_structure_if_needed`(`analyzer.py:819`)用 `_is_value_placeholder` 为门,把 `chip_data` 回填进 `chip_structure`(`profit_ratio` 写成 `f"{pr:.1%}"`)。数据源与 prompt 的 `enhanced['chip']`(`pipeline.py:776`)**同源、同格式**。于是 LLM 一旦省略该字段,守卫就在拿系统的值和同源的 fact 自己跟自己比 —— 恒等式,永远 pass,`checked` 计数虚高,`status="ok"` 制造虚假覆盖率。

把 A 提到**所有回填之前**,这两类问题一并消失,并且不再依赖「哪个 fill 用了哪个数据源」这种逐字段论证 —— 换成一条**位置不变式**。

> 这个坑我在 `capital_flow` 上逮到了(§4.2 排除),在 `chip_structure` 上没逮到。根因是同一个:**把「LLM 的输出」与「系统就地改写过的 dashboard」当成了同一个东西。** §2 因此新增一行事实底座,把所有 in-place 回填点显式列出。

**B 必须在 phase guardrail 之后。** 这一条是本次设计审查逮到的 **Blocker**:

`apply_phase_decision_guardrails` 在入口一次性算 `initially_high_confidence = _is_high_confidence(result.confidence_level)`(`phase_decision_guardrail.py:115`),随后有**两个**降级分支消费它:

- `:116` 核心数据 degraded → 高→**中**(`confidence_capped_core_data_degraded`)
- `:139` 保守盘口阶段却出现立即买卖信号 → 高→**低**(`confidence_capped_non_intraday_action`)

若 claim-validation 先把「高」降到「中」,`initially_high_confidence` 就成了 `False`,**这两个分支全部静默失效** —— 包括那个更严厉的高→低安全降级。结果是:**开启防幻觉守卫反而让阶段护栏变得不保守。**

把 B 放在 guardrail 之后即可根除:guardrail 先看到原始「高」并施加自己的降级;claim 的 cap 是**单调**的(仅当仍为「高」时降到「中」,否则 no-op),永不回撤 guardrail 已做的降级。

### 3.2 `prompt_facts` 采集 —— 并列纯函数,不碰 `_format_prompt`

**不能改 `_format_prompt` 的返回签名。** 约 30 个测试(`tests/test_analyzer_news_prompt.py`、`tests/test_crypto_derivatives_prompt.py`)对它的字符串返回值做断言,另有两处 `patch.object(analyzer, "_format_prompt", return_value="prompt")`(`tests/test_market_analyzer_generate_text.py:641,818`)。改成返回 tuple 会一次打红二十余个测试。

**也不能用实例属性 `self._last_prompt_facts`。** `self.analyzer` 是 `pipeline.py:140` 构造的单例,在 `ThreadPoolExecutor` 里跨线程并发调用 —— 实例属性是竞态。

因此:新增**并列纯函数** `collect_prompt_facts(context) -> Dict[str, Any]`,与 `_format_prompt` 消费**同一个 `context` dict**。`analyze()` 在调 `_format_prompt` 的同处调它一次,把结果挂到 `result.prompt_facts`(新增 dataclass 字段,默认 `None`)。`_format_prompt` **零改动**。

**开关的门控读在 `analyze()` 内**(契约,不可含糊 —— 否则 §11 #18 无法实现):

```python
# GeminiAnalyzer.analyze()，_format_prompt 调用处附近
prompt = self._format_prompt(context, name, ...)
facts = collect_prompt_facts(context) if self.config.llm_claim_validation_enabled else None
...
result.prompt_facts = facts        # 关 → 恒为 None，collect_prompt_facts 一次也不跑
```

关闭时 `collect_prompt_facts` **一次也不被调用**,`result.prompt_facts is None`,B 步整体早退、不写 `claim_validation` 键 → 字节级不变。

代价是两处取值逻辑可能漂移。**漂移由 drift-lock 测试消除**(§11 #3):同一 `context` 分别喂给 `collect_prompt_facts` 与 `_format_prompt`,断言每个 fact 的字面量确实出现在 prompt 文本里。任一侧改动而另一侧未同步,测试立刻红。

> 这一条不可省。facts 一旦对不上 prompt,守卫就会拿错的基准去判 LLM「幻觉」,反而制造假警报 —— 一个抓幻觉的东西自己产生幻觉,比没有它更糟。

`collect_prompt_facts` 必须复刻 `_format_prompt` 的**条件渲染**(`if 'realtime' in context`、`if 'chip' in context`、`if 'trend_analysis' in context`)与**量纲转换**(见 §4.2)。没渲染进 prompt 的字段不进 facts,自然不参与校验。

### 3.3 四个插入点(两条 pipeline 路径 × 两步)

| 路径 | 步 A(`extract_llm_claims`) | 步 B(`apply_claim_validation`) |
| --- | --- | --- |
| 非-agent | `analyze()` 返回后、**Step 7.5 之前**(即在 `normalize_chip_structure_availability` 之前),~L613 | `if adjustments: logger.info(...)` **之后**,~L652 |
| agent | 同锚:该分支**第一个** `fill_*` / `normalize_*` 之前 | 同锚:该分支 `apply_phase_decision_guardrails` 之后 |

**四处都要改,漏一处即 agent / 非-agent 行为漂移**(AGENTS.md §8.1 明列为低质量特征)。

**以函数调用为锚,不以行号为锚。** plan 阶段必须先 grep 出 agent 分支(`_analyze_with_agent`)里第一个 in-place 回填的确切调用点 —— 非-agent 侧是 `normalize_chip_structure_availability`,agent 侧未逐行核实,不得假定同构。

B 落在 `_refresh_decision_action_for_final_result` 之前(它不读 `confidence_level`,§2 已核,故前后皆安全;选之前是为了「所有对 `result` 的护栏改写都发生在最终动作刷新之前」这一既有排布习惯)。

### 3.4 两类校验的适用面

| 校验 | 依赖 | 非-agent | agent |
| --- | --- | --- | --- |
| 转录类 | 需 `prompt_facts` | ✅ 跑 | `prompt_facts is None` → `status="not_applicable"`,`reason="no_prompt_facts"` |
| 结构类 | 只读 `dashboard` 自身 | ✅ 跑 | ✅ 跑 |

agent 路径的转录类降级为 `not_applicable` 是 fail-closed:不报错、不阻塞、不假装通过。

---

## 4. 判据

### 4.1 转录类容差:按 LLM 自己声称的精度判

固定容差与相对/绝对分档都会在某个量级崩掉(相对容差对 `ma20=1800.42` 太松,对 `bias_ma5=0.02%` 太紧;绝对容差反之)。

**判据:`|claimed − fact| ≤ max(10^(−d), |fact| × 1e-9)`**,其中 `d` = **LLM 陈述值的十进制小数位数**,钳制在 `[0, 8]`。

#### `d` 必须用 `decimal.Decimal` 求,不得用 `split('.')`

```python
from decimal import Decimal
d = -Decimal(token).as_tuple().exponent      # token 是抽取到的原始数字文本
d = max(0, min(8, d))                        # 钳制在求值之后
```

朴素的 `len(token.split('.')[-1])` **有三处会崩,且都错向假警报**(实测):

| token | `split('.')` | `Decimal` | 后果 |
| --- | --- | --- | --- |
| `'1800'` | **4**(整串无小数点,`[-1]` 是它自己) | `0` | `tol=1e-4` 而非 `1.0` → **任何无小数点的 claim 都被误报** |
| `'1.23e-05'` | **6** | `7` | crypto 极小价容差错档 |
| `'1e+16'` | **5** | `-16` | 无意义 |

第一行是致命的:`fact=1800.4231`、LLM 写 `1800`,本应「整数 round 合法」,朴素算法却判幻觉。

`Decimal` 一并解决三件事:**保留字符串尾零**(`Decimal("12.30").as_tuple().exponent == -2`)、**正确处理科学计数法**、**给出规范指数**。它也让「字符串 vs JSON number」两条来源合流——`Decimal(repr(12.3))` 与 `Decimal("12.30")` 走同一条路,无需分叉:

- claim 是字符串(`"12.30"` / `"12.34元"` / `"1.23e-5"`)→ 抽出数字文本 token,`Decimal(token)`。
- claim 已是 JSON number → `json.loads` 早把尾零抹掉(`12.30 → 12.3`),`Decimal(repr(value))`。`d` 只会偏小 → 容差只会偏大 → **只可能漏报,绝不会误报**,与「每一次报警都必须确凿」一致。

#### 抽取正则必须接受指数段**与前导点形式**

既有 `_coerce_numeric_value` 的正则 `[-+]?\d+(?:\.\d+)?` 有两处会造成**假警报**:

1. 对 `'1.23e-5'` **只抽出 `1.23`,丢掉指数,值错 5 个数量级**。而 `f"{1.23e-05}"` 正是 `'1.23e-05'` —— **crypto 极小价在 prompt 里就是这个形态**。
2. 对**前导点**形式 `'-.05%'`(LLM 写字符串 claim 时合法,虽非合法 JSON number 字面量),它要求小数点前必须有整数部分,于是 `search` 跳过 `-.` 只匹配到 `05` → **`(5.0, 0)`,符号与数量级双双丢失**。一个正确的 claim 被判成幻觉。

`extract_numeric_claim` 必须用:

```python
r"[-+]?(?:\d+\.\d+|\.\d+|\d+)(?:[eE][-+]?\d+)?"
```

> **2026-07-09 复审订正**:原 spec 写的是 `r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"`,只修了缺陷 1。Task 2 的 review 逮到缺陷 2:`extract_numeric_claim('-.05%')` 返回 `(5.0, 0)`,`claim_matches_fact(5.0, 0, -0.05)` → `False` → **把正确 claim 判成编造**。差分测试证实新正则在既有语料(`1800` / `12.30` / `1.23e-5` / `1e+16` / `12.34元` / `72.3%` / `180-182` …)上逐字同结果,是**严格加宽、零回归**。`Decimal('-.05').as_tuple().exponent == -2` → `d=2`,与「按 LLM 声称的精度自校准」一致。

否则 D12 为 crypto 做的容差硬化会被抽取器直接废掉,并在 crypto 与前导点两处**必然假警报**。

#### 两个边界项

- **`|fact| × 1e-9` 下限**防浮点噪声。float64 对 `12.3` 量级的表示误差约 `1e-15`,远小于该下限。
- **`d` 钳到 `[0, 8]`(而非 `[0, 4]`)** 是为 crypto 极小价标的。若钳到 4,`ma5 = 1.23e-5` 的币种容差恒为 `1e-4` —— **比值本身大一个数量级,任何数都 pass,守卫对这类标的静默失效**。钳到 8 后 `d=7` 的 claim 得到 `1e-7` 容差,仍能逮住编造。上界保留是为了把 `'1e+16'` 这类负指数(`d=-16`)与 `d=19` 这类荒谬精度声明都收进安全区间。

规则自校准 —— LLM 声称得越精确,查得越严:

| fact | LLM 写 | d | 容差 | 判定 |
| --- | --- | --- | --- | --- |
| 1800.4231 | `1800` | 0 | 1.0 | ✅ 整数 round 合法 |
| 1800.4231 | `1795` | 0 | 1.0 | ❌ 编造 |
| 3.4512 | `3.4` | 1 | 0.1 | ✅ 截断 |
| 3.4512 | `3.5` | 1 | 0.1 | ✅ 四舍五入 |
| 3.4512 | `3.3` | 1 | 0.1 | ❌ 编造 |
| 12.34 | `21.34` | 2 | 0.01 | ❌ 转位 |
| 72.3 | `72.34` | 2 | 0.01 | ❌ **凭空多出一位有效数字 = 幻觉** |

最后一行是它最有价值的副作用:LLM 编出 prompt 里根本没给过的精度,自动被逮。

**零旋钮**(YAGNI):规则自校准,不引入容差乘数配置项。若 v1 出现误报,再凭证据加 env-only knob。

### 4.2 fact 表 —— 只收录「渲染为裸数值、语义无歧义」的字段

| LLM claim 路径 | prompt 来源 | prompt 渲染形态 | facts 记录 | 类型 |
| --- | --- | --- | --- | --- |
| `data_perspective.price_position.current_price` | `today['close']` **与** `realtime['price']` | 两处都是裸值 | **值集合**,任一命中即 pass | 价格 |
| `...price_position.ma5` / `ma10` / `ma20` | `today['maN']` | 裸值 | 裸值 | 价格 |
| `...price_position.bias_ma5` | `context['trend_analysis']['bias_ma5']` | `{:+.2f}%` | 百分点(3.45) | 百分点 |
| `...volume_analysis.volume_ratio` | `realtime['volume_ratio']` | `_na()` 裸值 | 裸值 | 比值 |
| `...volume_analysis.turnover_rate` | `realtime['turnover_rate']` | `_na(suffix='%')` | 百分点 | 百分点 |
| `...chip_structure.profit_ratio` | `chip['profit_ratio']` | `{:.1%}` | **百分点**(0.7234 → 72.3) | 百分点 |
| `...chip_structure.avg_cost` | `chip['avg_cost']` | 裸值 | 裸值 | 价格 |

> 「类型」列仅供阅读,**容差规则是统一的**(§4.1),不按类型分档。

`bias_ma5` 直接从 `context['trend_analysis']` 读,不必先过 `_sanitize_trend_analysis_for_prompt`:该函数(`analyzer.py:689`,纯函数,`trend_dict = dict(trend)`)只改写 `signal_reasons` / `risk_factors` / `prompt_consistency_notes` / `prompt_trend_direction`,其余键原样拷贝。若将来它开始改写 `bias_ma5`,drift-lock 测试(§11 #3)会立刻变红。

**facts 记录的是「渲染后的语义值」而非原始值** —— 因为转录保真度校验的是「LLM 复述它**看到的**东西」。`profit_ratio` 在 prompt 里是 `72.3%`,facts 就记 `72.3`。

**且 fact 必须从渲染字符串反解,不得从原始值重算。** `chip['profit_ratio'] * 100` 在 float 下是 `72.34000000000001`,而 prompt 里写的是 `f"{0.7234:.1%}"` = `"72.3%"`。fact 取 `float(f"{v:.1%}".rstrip('%'))`,与 prompt 里那个 token **按构造相等**。这既避免浮点误差直接进入比对基准,也让 drift-lock 的字面量断言(§11 #3)成为恒等式而非近似匹配。同理 `bias_ma5` 取 `float(f"{v:+.2f}")`。裸值渲染的字段(`ma5` / `close` / `avg_cost` / `volume_ratio`)本就无格式化,fact 即原值。

**刻意排除:**

| 字段 | 排除理由 |
| --- | --- |
| `chip_structure.concentration` | prompt 里有 `concentration_90` 与 `concentration_70` **两个**,LLM schema 只有一个 `concentration` 字段 → 对应关系有歧义。不猜。 |
| `capital_flow.*` | **根本不是 LLM 的 claim。** `fill_capital_flow_if_needed`(`analyzer.py:896`)在 **LLM 之后**确定性把资金面回填进 `data_perspective.capital_flow`(docstring:「presence-only + A股-gated;LLM 后、对决策只读」)。没有 LLM 转录动作,就没有转录锚点。(注:prompt 里 `main_net_inflow` 等三项是**裸值**渲染,`analyzer.py:3360-3362`,并未经 `_format_amount`。) |
| `price_position.support_level` / `resistance_level` | 推断类(§1.2)。 |

**`current_price` 是值集合,不是单值。** `today['close']`(行情表)与 `realtime['price']`(实时增强表)在 prompt 里**同时出现**,LLM 引用任一个都合法。按单值比对会系统性误报。

**缺失即 fact 缺失。** `context` 里没有 `realtime` / `chip` / `trend_analysis` 块时,对应字段不进 facts,自然不校验。

**一个刻意的语义推论:** `_format_prompt` 在 `trend.get('bias_ma5', 0)` 缺失时会把 `0` 喂进 prompt。facts 忠实记录 `0`,LLM 复述 `0` → **判为转录正确**。守卫不管「0 是不是假数据」——那是数据质量问题,不是幻觉。这恰好证明 `prompt_facts` 语义自洽。

### 4.3 结构类判据

签名**只接收 `sniper_points` 本身**:

```python
def validate_structure(sniper_points: Any) -> Dict[str, Any]: ...
```

这是有意的:不传 `current_price`、不传 `result`,使 §4.4 描述的那种误用(照搬 `entry ≤ current_price`)**在类型上就无法表达**。签名即护栏,强于任何测试。

对四个字段 `ideal_buy` / `secondary_buy` / `stop_loss` / `take_profit`,用共享的 `parse_sniper_value`(§7)抽数。**仅当相关字段都成功抽出数值时才判**(缺失 → 跳过,不判违规)。`sniper_points` 非 dict → `not_applicable`。

1. **抽出的值若非有限或 `<= 0` → `violation`(不是「缺失」)。**
2. `stop_loss < ideal_buy`(两者皆存在时)
3. `ideal_buy < take_profit`(两者皆存在时)
4. `stop_loss < take_profit`(两者皆存在时)
5. `secondary_buy` 若存在:`stop_loss < secondary_buy` 且 `secondary_buy < take_profit`

> **2026-07-09 复审订正(判据 1 的语义)。** 原实现把非正值**静默丢弃**、当作字段缺失,于是 `{"ideal_buy": "-5", "stop_loss": 1, "take_profit": 2}` 报 `ok` —— 负数买入价被放过。AGENTS.md 明列「用 broad fallback、静默降级掩盖不清晰的契约」为低质量特征。
>
> 改为 `violation` 之后,判据 1 **恰好只对「会被真正落库的非正数」报警**,这正是本函数的头号约束「与落库口径一致」:
>
> | claim | `parse_sniper_value` | DB 存的 | 判定 |
> | --- | --- | --- | --- |
> | `-5`(数值) | `None`(上游即滤除) | NULL | 缺失(一致) |
> | `"-5"`(字符串) | `-5.0` | `-5.0` | **violation** |
> | `"0元"` | `0.0` | `0.0` | **violation** |
> | `"inf"` | `inf` | `inf` | **violation**(非有限) |
>
> `parse_sniper_value` 本身**不动**(Task 1 的「逐字等价」保证不可破),不对称由它承担,判据 1 只负责把「已经透出来的非正/非有限值」显式报出来。
>
> `violations` 非空时即 `violation`,即便一条可比较的序关系都凑不出(例如只有 `ideal_buy: "-5"` 一个字段)。

### 4.4 为什么**不能**照搬 `is_invalid_price_level`

`src/services/volume_price_signals.py:425` 的 `is_invalid_price_level(*, entry, stop, target, current_price)` 有三条判据,其中第三条是 **`entry > current_price` → invalid**。

那一条成立,是因为它校验的对象是 `derive_price_levels` **反算出的** long-setup entry —— 按构造,entry 就是「MA20 与近 20 日 swing low 中 **≤ 现价** 的较高者」,`entry ≤ current_price` 是它的**构造不变式**。

LLM 的 `ideal_buy` **没有这个不变式**:「突破 12.8 元买入」(现价 12.3)是完全合法的交易计划。照搬会**系统性误杀所有突破买入计划**。

因此结构判据自写(§4.3),不调 `is_invalid_price_level`。

**主要防线是签名**(§4.3:`validate_structure` 拿不到 `current_price`),而不是测试 —— 一个拿不到 `current_price` 的函数不可能拿它做判据。§11 #7 的突破买入用例是**文档性回归护栏**:若将来有人给 `validate_structure` 加上 `current_price` 参数并引入该判据,它会变红。诚实地说,它锁不住当前实现的变异(变异不可表达),但它记录了意图。

---

## 5. 动作

| 触发 | 动作 | 实现 |
| --- | --- | --- |
| 转录 mismatch ≥ 1 条 **且** 当前置信度为「高」 | `result.confidence_level` 高→中 | 逐字复用 `phase_decision_guardrail.py:116` 的 `"Medium" if language == "en" else "中"`。**必须写本地化文本**,不是 canonical `"medium"`。 |
| **同上条件**(即 cap 真的发生时) | 追加 action code `confidence_capped_claim_mismatch` | 写入 `dashboard["claim_validation"]["actions"]` |

> **2026-07-09 复审订正(action code 的门控条件)。** 原表把 action code 挂在「mismatch ≥ 1 条」上,于是置信度已是「中/低」时 cap 是 no-op,`actions` 却仍报 `confidence_capped_claim_mismatch` —— 名字断言「已封顶」,实际什么都没发生。
>
> 仓库先例是相反的:`phase_decision_guardrail` 的 `confidence_capped_core_data_degraded` **只在 `core_degraded and initially_high_confidence` 同时成立、cap 真的发生时**才 append。
>
> 订正为:action code 与**真实的 cap 动作**绑定。「mismatch 发生过」这个信息不会丢 —— 它在 `transcription.status == "mismatch"` 里。
| 结构 violation ≥ 1 条 | 标注该买卖计划不可执行 | action code `sniper_points_unexecutable`;**不清空 `sniper_points` 字段** |

**cap 是单调的**:仅当 `_is_high_confidence(result.confidence_level)` 为真时降到「中」;已是中/低则 no-op。这保证它永不回撤 phase guardrail 已施加的更严降级(§3.1)。

**不清空 `sniper_points`** 的理由:`storage._extract_sniper_points`(`storage.py:2275`)仍要读它并落库到 `AnalysisHistory` 的 `stop_loss` / `take_profit` / `ideal_buy` / `secondary_buy` 列。清空会破坏落库契约。标注即可,让消费方决定。

### 5.1 防御与异常安全

- `language` 取值与 `apply_phase_decision_guardrails` 同源:`getattr(result, "report_language", None) or config.report_language`(见 `pipeline.py:645-647`)。
- `result.dashboard` 未必是 dict(既有护栏到处 `isinstance(dashboard, dict)` 防御)。非 dict → 守卫整体跳过,不写键、不抛错。
- **整个 `apply_claim_validation` 包在 `try/except` 里**,异常仅 `logger.warning` 后跳过,不阻塞报告产出 —— 与 `stabilize_decision_with_structure`(`analyzer.py:1277`)的既有做法一致。一个防幻觉守卫自己把主流程搞崩,是最坏的结果。
- `extract_llm_claims` 是纯读,同样不得抛错(内部 `try/except` 返回 `None`)。

---

## 6. 契约

### 6.1 `dashboard["claim_validation"]`(追加键,与 `decision_stability` / `phase_decision` 同级)

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
        "reason": str | None,          # not_applicable 时,如 "insufficient_fields"
        "violations": ["stop_loss(13.0) >= ideal_buy(12.5)"],
    },
    "actions": ["confidence_capped_claim_mismatch", "sniper_points_unexecutable"],
}
```

**开关关闭时整个键不写** → 字节级不变。

### 6.2 `AnalysisResult.prompt_facts`(新增内部 dataclass 字段)

```python
prompt_facts: Optional[Dict[str, Any]] = None  # 内部字段:本次实际渲染进 prompt 的数值快照
```

**不进 `report_schema`、不进报告 payload、不落库。** 已核实 `to_dict()`(`analyzer.py:1782`)是显式枚举,不含该字段;唯一的通用 `asdict()` helper(`alphasift.py:919` `_to_plain`)只服务 alphasift 自己的 dataclass。

### 6.3 additive-safety(已核实,无需改任何 schema)

- `AnalysisReportSchema.model_validate(...)` 的返回值被**丢弃**(`analyzer.py:3868`),真正落到 `result.dashboard` 的是 `data.get('dashboard')` 原始 dict(`:3876`)→ `report_schema.py` 的 `Dashboard` 模型不参与序列化,**不会剥掉新键**。
- 落库:`json.dumps(ensure_ascii=False, default=str)`,无 Pydantic 过滤。
- API:`ReportDetails.raw_result: Optional[Any]`(`api/v1/schemas/history.py:253`),原样回传。
- 渲染:`src/notification.py` 与 `templates/*.j2` 均逐键 `.get()`,**无白名单枚举**。

---

## 7. 共享原语:抽取 `parse_sniper_value`

结构类校验必须与**落库口径一致** —— 否则会出现「守卫判合法,但存进 DB 的是另一个数」。

`DatabaseManager._parse_sniper_value`(`storage.py:2204`,类定义在 `storage.py:831`)是既有的 sniper 抽数器:跳过 `MA5`/`MA10` 前缀数字、区间取**最后一个数**(`'180-182' → 182.0`)、`'N/A'`/`'待补充'` → `None`、数值 `<= 0` → `None`。它是私有 `@staticmethod`。

**不重写**(平行实现违反 AGENTS.md),**不跨模块 import 私有 staticmethod**(层次不清)。

**做法:抽到新模块 `src/sniper_parsing.py` 的模块级函数 `parse_sniper_value(value) -> Optional[float]`,`DatabaseManager._parse_sniper_value` 改为委托。** 这是**机械的剪切-粘贴-委托**移动(函数体不依赖 `self` 或类级常量),`tests/test_storage.py:90-139` 的既有行为夹具应全绿。`src/claim_validation.py` 从该模块导入。

> 该重构是**当前任务直接需要**的(否则结构判据与落库口径必然二选一:要么平行实现,要么跨模块取私有 staticmethod),不属于 AGENTS.md 所禁的「顺手优化」。既有夹具全绿是**必要条件而非充分证明**——移动的等价性由「函数体逐字未改」保证,测试只是回归护栏。

这与 Inc 2b 共享 `_ggt_eligible_state` 防漂移是同一手法。

---

## 8. 明确不复用的既有原语(及理由)

| 原语 | 不复用理由 |
| --- | --- |
| `_coerce_numeric_value`(`analyzer.py:1346`)/ `_first_numeric_value`(`:1365`) | 出口是 `float`,在 `float(...)` 处**丢掉文本小数位数 `d`**。扩展其返回值会改签名,打破 `analyzer.py:1130/1135/1139`、`:1253`、`:1368`、`:1372`、`:1405-1407` 等既有调用点(二者均为 `analyzer.py` 模块私有,`storage.py` 对其零引用)。**新写 `extract_numeric_claim(value) -> Optional[Tuple[float, int]]`。** |
| `_is_value_placeholder`(`analyzer.py:406`) | 委托 `is_chip_placeholder_value`,后者把数值 `0` 判为占位符(`report_language.py:695-696`)。而 `bias_ma5 = 0` 是**合法 claim**(prompt 里就渲染成 `+0.00%`)。**新写 `_is_claim_absent(v)`:`None` / 空串 / `'N/A'` 类文本 → absent;数值 `0` → 有效 claim。** |
| `is_invalid_price_level`(`volume_price_signals.py:425`) | 含 `entry > current_price → invalid`,那是 `derive_price_levels` long-setup 的构造不变式,LLM 的 `ideal_buy` 无此不变式。见 §4.4。 |

注意 `_parse_sniper_value` 取区间**最后一个数**而 `_coerce_numeric_value` 取**第一个数**('180-182' → 182 vs 180)。结构类走前者(与落库一致),转录类的 9 个字段不涉及区间。

---

## 9. 配置五件套

新键 `LLM_CLAIM_VALIDATION_ENABLED`,默认 `false`。它是**用户可见能力开关**(开启后报告多一段、置信度会被封顶),按 `src/config.py:996-999` 成文的判据(运营/算法级 → env-only;面向 Web 设置 UI → registry),**进 `config_registry`**。

| 件 | 位置 | 照抄样板 | 必须注意 |
| --- | --- | --- | --- |
| 1 | `src/config.py` `@dataclass Config`(L622) | `intraday_backtest_enabled`(L917) | 字段 `llm_claim_validation_enabled: bool = False`;env 用新式 `parse_env_bool(os.getenv("LLM_CLAIM_VALIDATION_ENABLED"), False)`(L145-152) |
| 2 | `src/core/config_registry.py` `_FIELD_DEFINITIONS` | `SIGNAL_BACKTEST_ENABLED`(L3252-3277) | **逐键照抄样板的全部 16 个键**(`title` / `description` / `category` / `data_type` / `ui_control` / `is_sensitive` / `is_required` / `is_editable` / `default_value` / `options` / `validation` / `display_order` / `help_key` / `examples` / `docs` / `warning_codes`);`category="ai_model"`;`data_type="boolean"`;`ui_control="switch"`;`default_value="false"`(**字符串**);`validation={}`;`display_order=63`(ai_model 当前最大 62);`help_key="settings.ai_model.LLM_CLAIM_VALIDATION_ENABLED"`;**`examples` 与 `docs` 必须非空** |
| 3 | `apps/dsa-web/src/locales/settingsHelp.ts` | `settings.backtest.SIGNAL_BACKTEST_ENABLED`(zhCN L880-890) | key 必须与 registry `help_key` **字面一致**。门禁只要求出现在 zhCN / enUS **任一张** map(两张 map 的 key 集合被合并去重后比对);样板 `SIGNAL_BACKTEST_ENABLED` 就只有 zhCN。本增量**两张都加**以求 UI 完整,但 enUS 缺失不会变红 |
| 4 | `.env.example` | `# INTRADAY_BACKTEST_ENABLED=false`(L725) | 写成**注释行** `# LLM_CLAIM_VALIDATION_ENABLED=false` |
| 5 | `docs/` + `docs/CHANGELOG.md` | — | 新建 `docs/llm-claim-validation.md`;CHANGELOG `[Unreleased]` 扁平格式 `- [新功能] ...` |

**会让门禁变红的陷阱(已核实):**

- `tests/test_config_registry.py:281-297` 遍历全部注册键,断言 `help_key`、`examples`、`docs` 三者皆非空 —— 漏任一即 RED。
- `tests/test_config_registry.py:444` registry 的 `help_key` 必须出现在 `settingsHelp.ts`;`:450` 反向:`settingsHelp.ts` 的 key 必须是 registry `help_key` 或 `settings.llm_channel.` 前缀。**两向门。**
- `tests/test_config_registry.py:397-415` 只检 `.env.example` 的**裸活动键**(正则 `^([A-Z][A-Z0-9_]*)=`)。写注释行则不受检。
- `display_order` **无唯一性门禁**(backtest 类目 84 已重复出现两次),但仍应取未占用值以免打乱 UI 排序。

因注册进 registry 会触及 `apps/dsa-web/src/locales/settingsHelp.ts`,**本增量会触发 web-gate**(仅 locale 文件,不改 Web 组件)。

---

## 10. 通知渲染 —— 两套引擎都要改

仓库有**两套**报告渲染引擎,由 `config.report_renderer_enabled`(`config.py:847`,**默认 `False`**)切换:

| 引擎 | 入口 | 现状 |
| --- | --- | --- |
| 传统 Python 拼接(**默认生效**) | `src/notification.py:1050` `generate_dashboard_report` | 渲染 `margin_trading`(:1307)、`ggt_context`(:1329);**不渲染 `phase_decision`** |
| Jinja(`REPORT_RENDERER_ENABLED=true` 时) | `templates/report_markdown.j2` | 渲染 `margin_trading`(:117)、`phase_decision`(:123-153);**不渲染 `ggt_context`** |

两引擎**本就已经漂移**(`ggt_context` 只在传统引擎有,`phase_decision` 只在 Jinja 有)。若 claim_validation 只加一侧,切换 flag 时提示行会时有时无。**两侧都加。**

`claim_validation` 是**顶层 dashboard 键**(与 `phase_decision` 同级),不塞进 `data_perspective`。

**双语是硬约束**:`src/report_language.py` 的 `_REPORT_LABELS`(L202)只有 `'zh'`(L203)与 `'en'`(**L325**)两块,新标签必须同时写入两处,否则另一语言 `KeyError`。

不加提示行的面(跟随 `margin_trading` / `ggt_context` / `phase_decision` 的既有边界):`generate_wechat_dashboard`(:1426)、`generate_single_stock_report`(:1728)、`report_wechat.j2`、`report_brief.j2` —— 这些面当前对上述三者全不渲染。被降权后的 `confidence_level` 仍会在所有面显形。

---

## 11. 测试面(全离线,无网络、无真实 LLM)

### 11.1 单元测试(纯函数)

| # | 测试 | 锁住什么 |
| --- | --- | --- |
| 1 | 容差参数化表 | round / 截断 / 百分点 / 跨量级(仙股 `0.53`、茅台 `1800.42`、**crypto 极小价 `1.23e-5`**)/ 转位(`12.34`↔`21.34`)/ 多出有效数字(`72.34` vs fact `72.3`) |
| 1b | **整数 claim 必须 pass(D17 回归锁)** | `fact=1800.4231`,claim `"1800"` → `d=0`、`tol=1.0` → **pass**。若实现退回 `len(token.split('.')[-1])`,`d=4`、`tol=1e-4` → 误报 → 红。**这是 Blocker 的直接锁,不可省** |
| 1c | **科学计数法(D17 回归锁)** | claim `"1.23e-5"` → 抽出 `1.23e-05`(非 `1.23`)、`d=7`;`'1e+16'` → `d=-16` 经钳制得 `0`。若正则缺指数段 → 值错 5 个数量级 → 红 |
| 2 | `d` 由 `Decimal` 求 | `Decimal("12.30")` → `d=2`(**尾零保留**);JSON number `12.3` → `Decimal(repr(...))` → `d=1`(**偏松,只漏报不误报**);`d` 钳制在 `[0,8]`;`|fact|×1e-9` 下限对 `1.23e-5` 生效 |
| 3 | **facts drift-lock** | 用**唯一哨兵值**构造 `context`(`ma5=11111.1111`、`ma10=22222.2222`……每个字段一个不会偶然出现的值)→ `collect_prompt_facts` 与 `_format_prompt` 分别跑 → 断言每个 fact 的字符串出现在 prompt 文本里。**哨兵值消除「短字面量恒真」**(否则 `fact=0` 时 `"0"` 到处都是,断言恒绿)。**legacy 与非 legacy 两个 prompt 分支各跑一次** |
| 4 | 条件渲染 | `context` 缺 `realtime` / `chip` / `trend_analysis` 时,对应 fact 不进 facts、不参与校验 |
| 5 | `current_price` 双源 | `today['close']` 与 `realtime['price']` 必须**取不同值**(否则退化为 tautology);LLM 复述任一 → pass;复述第三个值 → mismatch |
| 6 | 结构判据 | `stop_loss ≥ ideal_buy` / `take_profit ≤ ideal_buy` / `secondary_buy` 越界 / 值 `≤ 0` / 非有限 → violation |
| 7 | 结构判据:突破买入 | `ideal_buy=12.8` / `stop=12.0` / `target=13.5` → ok。**文档性护栏**(§4.4:真正的防线是 `validate_structure` 的签名拿不到 `current_price`,变异不可表达);若将来有人加该参数并引入判据,本例变红 |
| 8 | 结构判据缺失/畸形 | 只有 `stop_loss` 无 `ideal_buy` → `not_applicable`;`sniper_points` 为 `None` / `''` / `[]` → `not_applicable`,不抛错 |
| 9 | claim 缺失语义 | `'N/A'` / `''` / `None` → absent 不校验;**数值 `0` → 有效 claim,参与校验**(锁 §8 的 `_is_claim_absent`,防误用 `_is_value_placeholder`) |
| 10 | cap no-op | 置信度已是中/低时,转录 mismatch 不改变它 |
| 11 | `parse_sniper_value` 抽取后行为不变 | `tests/test_storage.py:90-139` 既有夹具全绿。**这是回归护栏,不是等价性证明** —— 等价性由「函数体逐字未改」保证 |

### 11.2 异常安全(§5.1 的硬保证必须有锁)

「一个防幻觉守卫自己把主流程搞崩」是最坏的结果。这个声明不能只写在 spec 里。

| # | 测试 | 锁住什么 |
| --- | --- | --- |
| 12 | 守卫内部抛错不阻塞 | monkeypatch `parse_sniper_value` / `extract_numeric_claim` 抛异常 → `apply_claim_validation` **不抛**,`result` 原样返回。删掉 `try/except` 的变异会红 |
| 13 | 非 dict dashboard | `result.dashboard` 依次设为 `None` / `''` / `[]` → 守卫跳过、不抛错、不新增键 |
| 14 | `extract_llm_claims` 纯读不抛 | 畸形 `result`(`SimpleNamespace` 缺 `dashboard`)→ 返回 `None`。复用 `tests/test_phase_decision_guardrail.py:308-314` 的 `SimpleNamespace` 先例 |

### 11.3 **pipeline 级集成测试 —— 锁住顺序**

> 这一节是对抗式审查逼出来的。**§3.1 与 §3.3 的顺序约束只存在于 `pipeline.py` 的插入位置**。如果测试自己按「正确顺序」手工调用两个函数,那么无论有人把 `pipeline.py` 里的调用挪到哪儿,测试都恒绿 —— 它锁不住自己声称锁的东西。因此这三条**必须驱动真实 pipeline**(stub LLM,复用 `tests/test_agent_pipeline.py` 的 mock 手法),不能是 guardrail 级单元测试。

| # | 测试 | 锁住什么 |
| --- | --- | --- |
| 15 | **B-after-guardrail(非-agent)** | stub LLM 返回 `confidence="高"` 且含转录 mismatch 的 dashboard;构造「保守盘口阶段 + 立即买卖信号」→ 断言最终 `confidence_level == "低"`。**若 `apply_claim_validation` 被挪到 guardrail 之前,它会先把「高」降成「中」,guardrail 的 `initially_high_confidence` 变 `False`,高→低 分支失效,结果是「中」→ 红。** |
| 16 | B-after-guardrail(agent 路径) | 同上,驱动 `_analyze_with_agent`。锁住四插入点不漏 agent 侧 |
| 17 | **A-before-any-backfill(假警报侧)** | 构造 `price_position.ma5` 为占位符 `'N/A'`,且 `trend_result.ma5` 的重算值 **≠** prompt fact 的 `ma5` → 断言 `transcription.mismatches` 为**空**。**若 `extract_llm_claims` 被挪到 `fill_price_position_if_needed` 之后,系统填入的重算值会与 fact 不符 → 假 mismatch → 红。** |
| 17b | **A-before-any-backfill(tautology 侧,D18)** | 构造 `chip_structure.profit_ratio` 为占位符、`chip_data` 有效 → 断言 `transcription.checked` **不含** `profit_ratio`(该字段 absent,未参与比对)。**若 A 被挪到 `normalize_chip_structure_availability`(Step 7.6)之后,系统回填的 `"72.3%"` 会被当成 LLM claim 拿去和同源 fact 自比 → `checked` 计入 → 红。** |
| 18 | 门控(两条路径) | 开关关 → `dashboard` 无 `claim_validation` 键、`result.prompt_facts is None`、`collect_prompt_facts` 未被调用 |
| 19 | agent 路径转录降级 | agent 路径 `prompt_facts is None` → transcription `not_applicable`,structural **照跑** |
| 20 | guardrail adjustment 未被抑制 | 「core_degraded + 原本高置信 + 转录 mismatch」→ 断言 `phase_decision` 的 `confidence_capped_core_data_degraded` **仍被追加** |

**测试 15 与 17 是本设计最重要的两把锁。** 15 锁 §3.1 的 Blocker 不复发,17 锁 tautology 陷阱不复发。两者都必须走真实 pipeline,否则毫无意义。

> **考虑过但未采纳的替代方案**:把 Step 7.7 的四步序列抽成一个被两条 pipeline 分支共同调用的 helper,让顺序只存在一处。它更干净,但两条分支当前并不对称(agent 侧在 `fill` 与 `stabilize` 之间还设 `result.current_price` / `result.change_pct`,`pipeline.py:1168-1171`),抽取需要参数化这个差异,属于热路径重构。按 AGENTS.md「稳定性优先于顺手优化」,本增量走集成测试路线;若将来该序列再长出第五步,再评估抽取。

### 11.4 集成与门禁

| # | 测试 | 锁住什么 |
| --- | --- | --- |
| 21 | 双引擎渲染 | violation 时,`generate_dashboard_report` 与 `report_markdown.j2` **两侧**都出现提示行;zh / en 双语各一次 |
| 22 | config 五件套 | registry 条目 **16 键**齐全;`help_key` ↔ locale 双向一致;`.env.example` 为注释行 |

---

## 12. 风险、边界与未决

### 12.1 已知边界

- **只覆盖个股分析路径。** 大盘复盘与图片提取无可校验的数值 claim 面(§1.2)。
- **agent 路径无转录校验。** 该路径不经 `_format_prompt`,无 `prompt_facts`。结构类仍覆盖。这是显式的 `not_applicable`,不是静默跳过。
- **转录类覆盖 9 个字段**(`current_price` / `ma5` / `ma10` / `ma20` / `bias_ma5` / `volume_ratio` / `turnover_rate` / `profit_ratio` / `avg_cost`)。`concentration`(`concentration_90` vs `_70` 歧义)、`capital_flow.*`(LLM 后系统回填,非 claim)、`support/resistance`(推断类)被刻意排除,理由见 §4.2。
- **`profit_ratio` 的 fact 已被 prompt 的 `{:.1%}` round 到 1 位。** 这不是缺陷:LLM 看到的就是 `72.3%`,守卫校验的正是它复述这个值的保真度。
- **区间型 `ideal_buy`(如 `'180-182'`)取最后一个数**,与落库口径一致(`parse_sniper_value`)。区间本身无单一 `d`,故不参与转录类校验(它本就是自主生成类)。
- **crypto 标的通常没有 `chip` 块**(`chip_data = fetcher_manager.get_chip_distribution(code)`,`pipeline.py:349`;`if chip_data:` 门控 `:350`,`enhanced['chip']` 才被建 `:776`)。因此 `profit_ratio` / `avg_cost` 对 crypto 恒缺失、不参与校验 —— 这正是 §4.2「缺失即 fact 缺失」的正常工作方式,不是缺陷。crypto 的极小价问题由 D12 的容差硬化处理。
- **`GeminiAnalyzer.batch_analyze`(`analyzer.py:4052`)绕过守卫**——它直接循环 `self.analyze(context)`,不经 pipeline Step 7.7。但**全仓零调用点**(已 grep 确认),是死代码。生产路径只有 `pipeline.py:579` 一条。本增量不动它;若将来复活该方法,须同时接上守卫。

### 12.2 风险

| 风险 | 缓解 |
| --- | --- |
| `collect_prompt_facts` 与 `_format_prompt` 漂移 → 守卫拿错基准制造假警报 | drift-lock 测试 #3(唯一哨兵值,两个 prompt 分支各跑一次) |
| 四个插入点漏一个 → agent / 非-agent 漂移 | 集成测试 #16、#18、#19 驱动真实 agent 路径 |
| 与 phase guardrail 的降级交互(§3.1 Blocker) | 两步拆分 + **集成测试 #15、#20**(必须走真实 pipeline,否则 tautology) |
| 假警报陷阱(extract 挪到 `fill_price_position` 之后) | **集成测试 #17** |
| tautology 陷阱(extract 挪到 `normalize_chip_structure_availability` 之后) | **集成测试 #17b** |
| `d` 公式退化(整数 / 科学计数法)→ 守卫自己产生幻觉 | **测试 #1b、#1c**(D17 直接回归锁) |
| 守卫自身抛错阻塞主流程 | §5.1 try/except + 测试 #12、#13、#14 |
| 提示行只加一套渲染引擎 | 测试 #21 双引擎断言 |
| 开关默认关,守卫永不生效 | 进 `config_registry` → Web 设置页可见可开(§9) |
| crypto 极小价使容差失效 | D12 容差硬化 + 测试 #1、#2 含 `1.23e-5` 用例 |

### 12.3 未决 / deferred

- **`src/reports/` 目录不存在**,而 `AGENTS.md` §3 的目录清单声称它承担「报告生成」。实际渲染在 `src/notification.py` + `templates/*.j2`。这是既有文档漂移,**不在本增量范围内修**,记录在此供后续 docs 任务处理。
- 容差乘数 env knob:v1 不做(YAGNI)。若出现误报,凭证据再加。
- 微信 / brief 面的提示行:跟随既有边界不加。若后续需要,单独增量。

---

## 13. 决策记录

| # | 决策 | 依据 |
| --- | --- | --- |
| D1 | 校验范围 = 转录类 + 结构类(窄而硬),不做推断类 | 用户拍板;误报率 ≈ 0 才配得上降权动作 |
| D2 | 动作 = 标注 + 分级降权,不覆盖数值/不改方向/不重试 | 用户拍板;证据强度与动作强度匹配 |
| D3 | 透出 = 报告 payload + 通知 markdown,无 Web 徽章 | 用户拍板;对齐 margin / ggt 先例 |
| D4 | 比对基准 = `prompt_facts`(我们喂给它的),不是权威重算值 | `ma5` 有三个来源(DB 预存 / TrendAnalyzer 重算 / VPS rolling),用重算值核对复述 DB 值会系统性误报 |
| D5 | 两步拆分:extract 在**任何确定性回填之前**,apply 在 guardrail 后 | **Blocker**:单点挂在 fill 前会抑制 phase guardrail 的高→低安全降级(§3.1)。**2026-07-09 复审收紧**:原「在 `fill_price_position` 之前」不够 —— Step 7.6 `normalize_chip_structure_availability` 更早,会让 `chip_structure` 校验退化为 tautology |
| D6 | facts 走并列纯函数 `collect_prompt_facts`,`_format_prompt` 零改 | ~30 个测试断言其字符串返回值;`self.analyzer` 跨线程共享,实例属性是竞态 |
| D7 | 容差自校准、零旋钮(**具体公式见 D12/D17,本条已被其取代**) | 自校准,跨量级正确,且能逮住「凭空多出有效数字」 |
| D8 | 结构判据自写,不调 `is_invalid_price_level` | 其 `entry ≤ current_price` 是 `derive_price_levels` 的构造不变式,LLM `ideal_buy` 无此不变式 |
| D9 | 抽 `parse_sniper_value` 到 `src/sniper_parsing.py` 共享 | 避免平行实现;结构判据须与落库口径一致 |
| D10 | 不复用 `_is_value_placeholder` | 它把数值 `0` 判为占位,而 `bias_ma5 = 0` 是合法 claim |
| D11 | 开关进 `config_registry`(接受触发 web-gate) | 用户可见能力开关;env-only 会让守卫永不被开启 |
| D12 | 容差加 `|fact|×1e-9` 下限,`d` 钳 `[0,8]` 而非 `[0,4]` | 对抗审查采纳:钳到 4 时 crypto 极小价(`ma5≈1.23e-5`)容差恒为 `1e-4`,比值本身大一个数量级 → 守卫对该类标的**静默失效** |
| D13 | facts 从**渲染字符串反解**,不从原始值重算 | `0.7234×100 = 72.34000000000001`(float),而 prompt 里是 `72.3%`。反解使 fact 与 prompt token 按构造相等,drift-lock 断言成为恒等式 |
| D14 | `validate_structure(sniper_points)` 签名只收 sniper_points | 签名即护栏:拿不到 `current_price` 的函数不可能拿它做判据,§4.4 的误用在类型上不可表达 |
| D15 | 顺序约束用 **pipeline 级集成测试**锁,不抽公共 helper | 对抗审查采纳:guardrail 级单元测试对顺序是 tautology。抽 helper 更干净但两条分支不对称(agent 侧多设 `current_price`/`change_pct`),属热路径重构,按「稳定性优先」不做 |
| D16 | drift-lock 用**唯一哨兵值**构造 context | 子串断言在 `fact=0` 时恒真(prompt 里到处是 `"0"`),哨兵值消除该退化 |
| D17 | `d` 用 `decimal.Decimal(...).as_tuple().exponent` 求,抽取正则含指数段 | **Blocker(2026-07-09 复审)**:`len(token.split('.')[-1])` 对 `'1800'` 返回 4 → `tol=1e-4` → **任何无小数点的 claim 都被误报**,包括 §4.1 自己的头号示例;且既有正则抽 `'1.23e-5'` 只得 `1.23`,在 crypto 上必然假警报 |
| D18 | A 步提到**所有 `fill_*` / `normalize_*` 之前** | **Important(2026-07-09 复审)**:`normalize_chip_structure_availability`(Step 7.6)与 prompt 同源同格式回填 `chip_structure` → `profit_ratio` / `avg_cost` 自己跟自己比,恒 pass,`checked` 虚高。位置不变式取代逐字段论证 |
| D19 | 开关门控读在 `GeminiAnalyzer.analyze()` 内 | 契约歧义:§11 #18 要断言「关则 `collect_prompt_facts` 未被调用」,门控位置不定义则无法实现 |
