# 设计：perp 杠杆情景回测（engine_version 标签隔离，零 schema）

- 日期：2026-06-11
- 状态：设计已批准（方案 A：配置/参数 + engine_version 情景标签）；对抗审查已收敛（确认 9 条：1 blocker=API 查询面、4 major、4 minor，本版已全部吸收），待用户终审后写实施计划
- 子项目：perp 系列收官——子项目 E 显式推迟项（杠杆/强平）的落地
- 关联前序：E（资金费+做空 1x）、做空 Web 透出、Binance 备援均已落地于本地分支栈

## 0. 背景与定位

E spec §8 推迟杠杆的两条理由及本设计的回应：

1. **"analysis 不含杠杆信息"** → 杠杆定位为**情景参数**（what-if 旋钮），不是 AI 建议：用户问"若该批 perp 建议按 3x 执行会怎样（含强平风险）"。来源：配置 `CRYPTO_BACKTEST_LEVERAGE`（默认 1）或 API `leverage` 参数。
2. **"日线无法忠实模拟盘内强平 → 虚构精度"** → 用**保守口径 + 全部近似显式声明**回应（§2 四条声明），并以"触线即强平"的日线可判定事件为锚，不假装盘内精度。

## 1. 范围与不变量

- **只作用于 perp 标的**（`is_perp`）的 `long`/`short` 行；`cash` 仍 0%。
- **L=1（默认）= E 现路径，行为字节级一致**（最高优先级不变量：`leverage=1` 时引擎不进杠杆分支，所有既有用例与锁套件零改动全绿）。
- 现货/股票（`is_perp=False`）任何 L 下不受影响（引擎对非 perp 忽略 leverage）。
- **L>1 的运行只评估 perp 候选**，过滤分两层（对抗审查确认项：若只在循环内跳过，非 perp 候选在 `v1-xN` 命名空间下永远无结果行可去重，每次 run 重新被 `get_candidates` 选中并占满 limit 配额，导致 perp 候选饥饿、通用 run 实际不可用）：
  - **SQL 粗滤（防饥饿/配额）**：`get_candidates` 增可选参数 `perp_only: bool = False`，为 True 时查询追加 `func.upper(AnalysisHistory.code).like('%:PERP')`（与 `is_perp_code` 的后缀约定一致，大小写无关）；
  - **循环内精确兜底（语义真值）**：`is_perp_code(analysis.code)` 为假者跳过不写入（拦截粗滤漏网，如 quote 非 USDT/USDC 的构造码）。统计照实反映 perp-only run。
- 合法域 `1 ≤ L ≤ 125`（int；API Field 校验拒绝 + service 钳制 `[1,125]` 兜底，config 解析钳下限）。

**明确不做（YAGNI）**：维持保证金率/分档杠杆/资金费率对强平价的影响（声明为简化）；逐仓外的全仓模式；杠杆下的做空 TP/SL（E 裁定沿用）；Web 杠杆选择器（API/config 已可用，Web 仅加强平标签）；`leverage` DB 列（方案 B 已否决——仓库无迁移机制）。

## 2. 引擎语义（`src/core/backtest_engine.py`）

`evaluate_single` 增 kwargs 参数 `leverage: int = 1`（置于 `funding_cost_pct` 之后）。**仅 `is_perp and leverage > 1` 进杠杆分支**；否则一切走现路径（含 L=1 的 perp——保证 E 字节级一致；1x 做空理论爆仓点 +100% 不模拟，与 E 一致并文档声明）。

### 2.1 强平价（线性、逐仓、简化口径）

```
liq_long  = entry × (1 − 1/L)
liq_short = entry × (1 + 1/L)
```

**近似声明 ①**：不含维持保证金率（MMR）→ 强平价比真实略远、结果**略乐观**；真实交易所强平更早。

### 2.2 逐 bar 检查（窗口内按日序）

- **long**：每根 bar 先查强平（`bar.low ≤ liq_long` → 强平于该 bar），未强平再走既有 SL/TP 检查（含既有 `ambiguous_stop_loss` 逻辑）。**同 bar 强平+TP/SL 双触 → 保守强平优先**（与既有同 bar 双触按止损的保守偏置一致；**近似声明 ②**：日线无法判定 bar 内先后，触线即判强平为保守裁定）。
- **short**：每根 bar 仅查强平（`bar.high ≥ liq_short`）；无 TP/SL（E 裁定不变），未强平持有至窗口末。
- **结构落点**：`_evaluate_targets` 零改动（预测字段与基线出场照旧产出）；杠杆分支独立扫描首个触强平的 bar 序号，与基线出场 bar 序号对账——强平 bar 序号 ≤ 出场 bar 序号（同 bar 含，即强平优先）→ 覆盖 `simulated_exit_*` 为强平结果；强平 bar 晚于既有 TP/SL 出场 bar → 已出场不强平（**按日序判定，禁止整窗 min/max 判强平**——对抗审查确认该误实现可通过其余全部用例，已补判别用例于 §5）。short 的对账基线即窗口末（与 E 的 `window_end_short` 覆盖先例同型）。

### 2.3 强平行的字段语义

- `simulated_return_pct = -100.0`（保证金归零；**近似声明 ③**：强平后资金费不再计入）。
- `simulated_exit_price = liq 价`；`simulated_exit_reason = "liquidated"`（String(24) 容纳）。
- `simulated_entry_price = start_price` 不变。
- **预测质量字段维持既有逻辑不动**：`first_hit`/`hit_stop_loss`/`hit_take_profit`（由 `_evaluate_targets` 衡量预测命中，不感知强平）与 `direction_expected`/`direction_correct`/`outcome`（基于标的价格走势）照旧——沿用现架构"预测命中与模拟执行分离"：方向可以判对（`direction_correct=True`）但模拟执行被强平（`simulated_return_pct=-100`），两者并存即是杠杆风险的呈现方式。

### 2.4 非强平行的收益放大

```
simulated_return_pct = L × 方向收益% + L × 带符号资金费%
```

（资金费按名义本金=保证金×L 收取，折算到保证金口径 ×L；long 为 `−L×funding`、short 为 `+L×funding`，与 E 同号约定。）最终 **clamp 至 ≥ −100.0**（**近似声明 ④**：保证金不可亏穿；可能在"价格未触强平线但资金费拖到 < −100%"的极端构造下生效）。long 的 TP/SL 提前出场同样按出场价计算方向收益后 ×L。

## 3. service 与落库（`src/services/backtest_service.py`）

- `run_backtest(..., leverage: Optional[int] = None)`：None → `getattr(config, "crypto_backtest_leverage", 1)`；钳制到 `[1, 125]`（int）。
- **入口语义（对抗审查确认项：config 是全局的，若所有入口都落 config 默认，设 `CRYPTO_BACKTEST_LEVERAGE=3` 会静默把每日自动回测整条 v1 生产流水线切到 v1-x3 并剔除全部股票候选）**：
  - `main.py` 每日自动回测（~line 650）**显式传 `leverage=1`**——生产 v1 流水线对该配置免疫，股票回测照常；
  - CLI `--backtest`（~line 891）与 API `/run` 未显式传 leverage 时落 config 默认——按需运行是用户显式动作，config 语义即"按需运行的默认杠杆"，文档与 `.env.example` 写明此边界；
  - L>1 run 在日志中显式标注情景（如 `leverage scenario run L=3 (perp-only, engine_version=v1-x3)`），不静默。
- **L>1 时**：
  - `engine_version` 写为情景标签 `f"{base_version}-x{L}"`（如 `v1-x3`；`v1-x125` = 7 字符，两表 String(16) 容纳）——在构建 `EvaluationConfig` 前注入，run 全程（含 `get_candidates` 去重与落库）使用标签版本。不对 base_version 做 `-xN` 后缀剥离（保持简单）；文档约束 `BACKTEST_ENGINE_VERSION` 不应设为带 `-xN` 后缀的标签值，否则会叠出 `v1-x3-x3` 之类复合标签。
  - 候选过滤：按 §1 两层执行——`get_candidates(perp_only=True)` SQL 粗滤 + 循环内 `is_perp_code` 精确兜底。
  - `evaluate_single` 传 `leverage=L`。
- **L=1 时**：engine_version 不打标签（仍 `v1`），`perp_only` 不启用，全部行为与现状一致（含非 perp 候选照常评估）。
- **查询面（对抗审查 blocker 修正）**：现状 API 读路径无 engine_version 参数、service 读路径（`get_recent_evaluations` ~263、`get_summary` ~321/动态汇总 ~766）钉死 config 基础版本——`v1-xN` 行落库后经现有 API/Web **完全不可见**，原稿"已可经 API 查询"声明为假。本设计补齐：`get_recent_evaluations`/`get_summary` 增 `engine_version: Optional[str] = None` 参数（None → config 现行为，即默认仍只看 `v1`），三个读端点透传（见 §4）。
- 落库与汇总隔离自动成立（已核，模型名修正：汇总表是 `BacktestSummary`/`backtest_summaries`，原稿误写 BacktestPerformance）：`BacktestResult` 唯一键 `(analysis_history_id, eval_window_days, engine_version)`、`BacktestSummary` 唯一键 `(scope, code, eval_window_days, engine_version)` 均含 engine_version → 1x 与各杠杆情景共存互不覆盖，行/汇总自描述。

## 4. 配置 / API / Web

- **config**：`src/config.py` 增 `crypto_backtest_leverage: int = 1`（env `CRYPTO_BACKTEST_LEVERAGE`，`parse_env_int` 钳制 minimum=1；上限在 service 钳制）；`.env.example` 注释行（默认 1=与现状一致；仅 perp 标的生效；情景参数非 AI 建议；**仅影响按需运行——CLI --backtest / API 未显式传 leverage 时，每日自动回测固定 1x 不受影响**）。registry 不注册（对称 `binance_base_url`/`BINANCE_FAPI_BASE_URL` 先例——非 UI 配置面）。
- **API（写）**：`api/v1/schemas/backtest.py` 的 `BacktestRunRequest` 增 `leverage: Optional[int] = Field(None, ge=1, le=125, description="perp 杠杆情景（默认取配置，1=与现状一致；仅 perp 标的生效）")`；`api/v1/endpoints/backtest.py` 透传给 service。
- **API（读，blocker 修正）**：`GET /results`、`GET /performance`、`GET /performance/{code}` 各增可选 Query 参数 `engine_version: Optional[str] = None`（None → 现行为，按 config 基础版本过滤），透传 service →（/results 路径）repo 既有 `engine_version` 精确过滤（backtest_repo.py ~417 已支持，仅需穿参）。响应字段 `engine_version` 已存在，无 schema 变更。
- **Web**：`apps/dsa-web/src/pages/BacktestPage.tsx` 的 `EXIT_REASON_LABELS` 加 `liquidated: '强平'` 一行 + 一条渲染用例。其余 UI 不动（杠杆行经新增的 `engine_version` 查询参数可查可见；Web 情景切换器 YAGNI——页面默认仍展示 1x，用户用 API 按需查情景）。

## 5. 测试

- **引擎**（扩展 `tests/test_perp_backtest_engine.py` 或新文件 `tests/test_perp_leverage_backtest.py`——以新文件为准，保持 E 的用例文件零改动作回归证据）：
  - **L=1 不变量**：同输入下 `leverage=1` 与不传 leverage 的输出**逐字段相等**（long/short/cash 三态对照）。
  - long 强平：构造 `bar.low ≤ entry×(1−1/L)` → `-100.0`/`liquidated`/exit=liq 价；强平 bar 之后的 TP 触达不改变结果（已出场）。
  - short 强平：`bar.high ≥ entry×(1+1/L)` → 同上。
  - **先出场后触线 → 不强平**（对抗审查确认缺口：整窗 min/max 误实现的唯一判别用例）：long bar1 触 TP 出场、bar3 才跌穿强平线 → `take_profit`/`L×tp收益`，**非** liquidated。
  - 同 bar 强平+TP 双触 → 强平优先（保守）。
  - **同 bar 强平+SL 双触 → 强平优先**（对抗审查确认缺口：设 SL 且 SL > liq_long 时，触强平的 bar 必同触 SL——杠杆强平最常见形态；fixture 必须显式设置 stop_loss，断言 `liquidated`/−100 而非 `stop_loss`）。
  - **L=1 门控回归判别**（对抗审查确认缺口：L=1 自比较恒真，需可观测差异点）：L=1 perp short 构造 `bar.high ≥ 2×entry`（理论爆仓点 +100%）→ 仍 `window_end_short`、不强平、收益按 E 公式（若门控误写 `>=` 该用例即挂）。
  - 非强平放大：long TP 出场 `L×tp收益 − L×funding`；short 窗口末 `L×(entry−end)/entry×100 + L×funding`。
  - clamp：构造资金费极端值使原始值 < −100 → 输出恰 −100.0。
  - `is_perp=False` + `leverage=5` → 与 1x 完全一致（忽略杠杆）。
  - 预测质量字段分离：强平行 `direction_correct`/`first_hit` 与无杠杆评估一致。
- **service**（新文件，同引擎用例放 `tests/test_perp_leverage_backtest.py` 或拆分）：L=3 → 落库行 `engine_version == 'v1-x3'`；同一 analysis 的 1x 行共存不覆盖；非 perp 候选被剔除（不写入）；`get_candidates(perp_only=True)` SQL 粗滤——种子混合 perp/股票分析行，断言仅 perp 被选中、股票行不占 limit 配额（饥饿判别：limit 设小于股票行数仍能选到更老的 perp）；leverage=None → config 默认；钳制（0→1、126→125 或拒绝——API 层 Field 校验拒绝，config 层钳下限，service 层钳制兜底）。
- **入口钉死**（main.py 每日自动回测 `leverage=1`）：main.py 流程不易做单测，作为实施清单项 + 最终人工核查项（grep 确认调用处显式传参），不强求自动化用例。
- **API**：schema 校验用例（`leverage=0` → 422）按既有 API 测试约定；读端点 `engine_version` 参数用例——种子 `v1` 与 `v1-x3` 两行，`GET /results?engine_version=v1-x3` 仅返回标签行、不带参数仍只返回 `v1` 行（默认行为不变判别）。
- **Web**：`BacktestPage.test.tsx` 加 liquidated 行渲染用例（fixture `simulatedExitReason:'liquidated'` → 「强平」）。
- **锁套件零改动**：`tests/test_backtest_engine.py`、`tests/test_crypto_backtest.py`、`tests/test_backtest_summary.py`、以及 E 的 `tests/test_perp_backtest_engine.py`（本期当作第四个锁文件，新用例进新文件）。

验证：特性文件 pytest → 全量 `ci_gate.sh` → web-gate（BacktestPage 改动触发）。

## 6. 文档

- `docs/crypto-guide.md` 永续回测小节加「**杠杆情景回测（v1-xN）**」子节：情景参数定位（非 AI 建议）、强平口径与**四条近似声明**（无 MMR 略乐观/触线即强平保守/强平后免资金费/clamp −100%）、engine_version 标签隔离语义（1x 与杠杆行共存、读 API 经 `engine_version` 参数查询情景行）、仅 perp 生效、`CRYPTO_BACKTEST_LEVERAGE`（仅按需运行）/API `leverage` 用法、`BACKTEST_ENGINE_VERSION` 不应设为 `-xN` 标签值。
- **既有矛盾表述修订**（对抗审查确认缺口——落地后这四处将与新子节直接矛盾，须同步改）：
  - `:77` 小节标题 `### 永续回测（资金费 + 做空，1x）` → 去掉 `1x` 限定（如 `（资金费 + 做空 + 杠杆情景）`）；
  - `:79` 括注 `（固定 1x，无杠杆/强平）` → 改述为默认 1x、可选杠杆情景含强平模拟；
  - `:84` `出场原因（做空恒为窗口期满；…）` → 补杠杆情景下做空可 `liquidated`（强平）；
  - `:255` 已知限制 `杠杆仍不做` → 更新为已做杠杆情景，仍不做项改列 MMR/分档杠杆/全仓等（与 §1 YAGNI 一致）。
- `docs/CHANGELOG.md` `[Unreleased]` 扁平一行：`- [新功能] perp 回测新增杠杆情景（CRYPTO_BACKTEST_LEVERAGE/API leverage，1-125x，含保守强平模拟与收益放大；结果按 engine_version 标签 v1-xN 与 1x 隔离共存，读 API 增 engine_version 查询参数；默认 1x 行为不变，每日自动回测固定 1x，仅 perp 生效）`。

## 7. 风险与回滚

- **默认零扰动**：L=1 不进任何新分支（不变量锁测）；现货/股票全程免疫。
- **config 影响面（对抗审查确认项的裁定结果）**：`CRYPTO_BACKTEST_LEVERAGE>1` 只改变按需运行（CLI/API 未显式传参时）；每日自动回测显式钉 `leverage=1` 对其免疫，v1 生产流水线不会被静默切走。按需运行被切换时有显式日志 + `.env.example`/文档边界说明，符合"不配置也可运行，配置后增强能力"护栏。
- **数据风险**：杠杆行天然隔离在 `v1-xN` 命名空间，误跑可按 engine_version 删除/忽略，不污染 1x 数据；读 API 默认仍只看 config 基础版本（`engine_version` 参数是显式 opt-in），现有客户端零感知。
- **口径风险**：四条近似已显式声明进文档与 spec；强平判定偏保守（触线即强平）、强平价偏乐观（无 MMR）——两个偏置方向相反但各自声明，不抵消混淆。
- **回滚**：按提交 revert；删 leverage 参数与杠杆分支即回 E 语义，已写入的 `v1-xN` 行成为孤儿数据（无害，可手工清理）。

## 8. 已核实事实（设计依据）

- `BacktestResult` 唯一键 `(analysis_history_id, eval_window_days, engine_version)`、`BacktestSummary`（表 `backtest_summaries`）唯一键 `(scope, code, eval_window_days, engine_version)`——标签隔离两层都成立（storage.py 已核；对抗审查修正：原稿误写 BacktestPerformance，仓库无此模型，勿与 API 层 `PerformanceMetrics`/`/performance` 端点混淆）。
- 两表 `engine_version` 均 String(16)（`v1-x125` 7 字符 ✓）；`simulated_exit_reason` String(24)（`liquidated` 10 字符 ✓）。
- `evaluate_single` 为全 kwargs 形态（`is_perp`/`funding_cost_pct` 默认参数先例），加 `leverage: int = 1` 同型。
- service 调用点：`is_perp` 判定（line ~133）与 `evaluate_single` 调用（~143）形态清晰；`engine_version` 在 run 入口从 config 读出（~49）后构建 `EvaluationConfig`——标签注入点明确。
- **API 读路径现状（blocker 依据，已亲核）**：`api/v1/endpoints/backtest.py` 全文无 `engine_version` 查询参数；`get_recent_evaluations`（~263）/`get_summary`（~321、动态汇总 ~766）均 `engine_version = str(getattr(config, "backtest_engine_version", "v1"))` 钉死过滤；repo 侧 `backtest_repo.py` ~417 已支持 `engine_version` 条件过滤（穿参即可）。
- **候选去重机制（饥饿问题依据，已亲核）**：`get_candidates` force=False 时按 `(eval_window_days, engine_version)` 的既有结果行 `not_in` 去重后 `order_by(created_at desc).limit(limit)`——无行可去重的候选永久重扫。
- **入口现状（劫持问题依据，已亲核）**：`main.py` ~650（每日自动回测）与 ~891（CLI --backtest）均不传 leverage；`is_perp_code` 为大写 `:PERP` 后缀 + quote ∈ {USDT, USDC}（SQL 粗滤 `LIKE '%:PERP'` 可行依据）。
- `docs/crypto-guide.md` :77/:79/:84/:255 四处"1x/无杠杆/恒窗口期满/杠杆仍不做"表述已亲核（§6 修订清单依据）。
- 仓库无 DB 迁移机制（无 ALTER TABLE/ensure_column）——方案 B（新列）的否决依据。
- E spec §8 推迟理由原文（杠杆/强平）——本设计 §0 逐条回应。
