# 战略文档:面向「可直接指导操作的买卖信号系统」的竞品情报与执行方案

> 日期:2026-07-01　类型:战略/规划(非代码)　范围:A股 / 港股通 / 加密数字货币
> 目标形态:**明确买卖信号系统**(择时点位 / 仓位 / 止盈止损),复用策略:**借鉴设计、在本项目现有架构内自建**(不直接内嵌外部 GPL/AGPL/非商用代码)。
> 本文档为内部战略存档;结论来自 2026-07-01 一次 5 视角开源生态调研(web 检索+抓取验证)+ 本项目现状盘点。

---

## 1. 背景与定位:本项目的血缘与护城河

**关键情报**:强证据表明本项目很可能是开源项目 [`ZhuLinsen/daily_stock_analysis`](https://github.com/ZhuLinsen/daily_stock_analysis)(MIT,~50k★)的**下游衍生**——两者数据源组合(TickFlow/akshare/tushare/pytdx/baostock/yfinance/Longbridge)与"多源抓数→技术分析+新闻检索→LLM 分析→决策看板→多渠道推送"主流程高度同源(TickFlow 这一冷门源同时出现基本可锁定血缘)。

**这界定了本项目真正的差异化护城河**:相对上游,本项目独有的增量是**整条盘中/分钟级回测能力体系**——
- crypto/perp 多市场覆盖;
- VPS 量价信号引擎(已向量化 O(n log k));
- 链路A(操作建议 / PnL / perp 杠杆回测);
- 链路B(信号可信度三重门,产 `hit_rate / CI / horizon` 统计);
- 成本模型(A股卖出单边印花税、港股双边印花税);
- interval 维度阈值覆盖机制、声明式数据源路由。

**上游没有这些。** 战略含义:本项目已从"又一个 LLM 荐股看板"分化为"**带可回测信号可信度的分析系统**"。后续应继续深耕该差异化,而非回退去追上游的看板功能。

---

## 2. 开源生态图谱(按可借鉴价值排序)

| 项目 | ★ / License | 定位 | 值得**借鉴设计**的点 | 复用度 |
|---|---|---|---|---|
| **TradingAgents** (Tauric) | 90k / Apache-2.0 | 多 agent 投研(多空辩论+风控 gate),**原生支持 Anthropic** | 把本项目**单轮 LLM 打分**升级为"多空辩论→交易员→风控审批"的**决策收敛层**;角色 prompt + 结构化决策输出 + 决策日志/反思 | 高 |
| **TradingAgents-astock** (simonlin1212) | 1.6k / Apache-2.0 | TradingAgents 的 A股深改版 | **A股制度建模逐项对标**:T+1/涨跌停/最小手数/沪深300 基准 + 政策·游资·解禁三类分析师 + mootdx/东财免 key 源 | 高 |
| **HKUDS/Vibe-Trading** | 16k / MIT | 自然语言驱动因子/回测 agent | **456 alpha 因子库**(GTJA191/Kakushadze101/Qlib158)可扩 VPS 因子面;18 源 fallback 与本项目声明式路由互印证 | 高 |
| **qrak/LLM_trader** | MIT | crypto 自治 agent | **claim-validation 守卫**(LLM 每条结论与计算值交叉核对)= 链路B 防 LLM 幻觉的现成范式 | 高(方法) |
| **FINSABER** (KDD 2026) | Apache-2.0 | LLM 择时**严谨回测框架** | 滚动窗(2yr/1yr step)防 data-snooping、next-open 执行防 look-ahead、buy&hold 零假设、成本现实性 = 链路B 严谨性升级蓝图 | 高(方法) |
| **aiagents-stock** (oficcejo) | 1.6k / MIT | A股多 agent | 龙虎榜/游资/板块轮动/问财选股适配 + **可执行进出场/止损输出**格式 | 中 |
| **StockBench / Look-Ahead-Bench** | Apache / arXiv 2601.13770 | LLM 决策**防污染评测** | post-cutoff 无污染切片 + alpha decay 检验(LLM 进信号后必须防"预训练记忆污染的伪 alpha") | 中(方法) |
| **alphalens-reloaded** | Apache-2.0 | 因子有效性检验 | IC/分位数收益/换手/分层,与链路B(hit_rate/CI)**正交互补** | 中 |
| **Riskfolio-Lib** | BSD-3 | 组合优化+风控 | CVaR/风险平价/HRP,补本项目缺失的组合层与仓位 | 中 |
| **FailSafeQA** (Writer) | HF 数据集 | LLM 稳健性/合规评测 | "数据缺失/脏上下文/fallback 降质时应**拒答而非编造**",与本项目多源降级哲学契合 | 中(方法) |
| **Qlib / QuantStats / vectorbt** | MIT/Apache | 因子·PIT·tear sheet·参数扫 | 回测纪律(purged CV/PIT)、风险指标口径、interval 阈值网格扫描 | 中(设计) |
| akshare(已依赖) | MIT | 数据 | 补**南向资金/港股通成份/持股统计**端点,填港股通空白,零新增依赖 | 高 |

**License 红线(即便借鉴也不照抄实现代码)**:OpenBB=AGPLv3(在线服务 import 会传染)、freqtrade/go-stock=GPL-3.0、CryptoTrade=CC-BY-NC-SA 非商用、TradingAgents-CN / A_Share_investment_Agent 含专有/非商用条款。本项目采"借鉴设计不集成代码",规避绝大多数传染风险。

**本项目已追平/领先(不重复造)**:盘中可回测信号可信度(链路B 因果三重门)**强于** TradingAgents/ai-hedge-fund(它们回测多为固定窗口喂 LLM、官方声明不保证复现);多市场含 crypto/perp;多源 fallback + 双市场成本模型。

---

## 3. 目标定义与合规红线

**目标**:对 A股 / 港股通 / crypto 产出**明确买卖信号**——每条信号含 `方向 / 入场区间 / 止损 / 目标位 / 仓位 / 置信度 / 时效 / 失效条件`,且信号质量经**样本外**验证。

**铁律**:信号系统的价值 = **出场后被验证的、样本外可信的信号质量**。差距不在"能否生成信号",而在"信号可不可信(L2)、可不可执行(L4)、不被 LLM 幻觉污染(L6)、合不合规(L6)"。

**⚠️ 合规红线(产品定位,须人决策,不可由工程默认)**:向公众发布"明确 A股买卖信号"在境内触及**证券投资咨询 / 投顾牌照**监管;crypto、港股通各有其口径。必须先在三种定位中拍板,它决定后续所有 UI/文案/分发/免责设计:
- (a) **自用 / 内部**(不对外分发信号);
- (b) **持牌 / 合规主体**运营;
- (c) **仅教育 + 强免责**(明确"非投资建议、风险自负",信号呈现为"分析结论"而非"操作指令")。

---

## 4. 差距分析(6 层)

> 每层:现状 → 差距 → 借鉴自谁 → 优先级(P0=命门/前置,P1=可管理化,P2=深化)。

### L1 信号生成:从"分析/打分"到"可执行信号对象" — P0
- 现状:VPS 出信号+可信度;LLM 出分析/评分;二者未融合成一个可执行对象。
- 差距:缺统一 **TradeSignal 结构** `{direction, entry_zone, stop, targets[], position_size, confidence, horizon, invalidation}`;止损/目标应**波动率自适应**(VPS 已算 ATR,可直接用)。
- 借鉴:TradingAgents(辩论→风控 gate 再出信号)、aiagents-stock(进出场/止损输出)、FINSABER/Trading-R1(波动率调整决策)。

### L2 回测严谨性:**命门** — P0
- 现状:链路B 因果三重门 hit_rate/CI(诚实、已防右端截尾);链路A 带成本 PnL;但偏单信号、近似样本内;interval 阈值标定存多重检验风险。
- 差距:① walk-forward + purged/embargo CV;② 样本外 / post-cutoff 无污染评测(LLM 进信号后尤为关键);③ 风险调整指标(Sharpe/Sortino/**maxDD**)+ tear sheet;④ 阈值搜索 data-snooping 校正;⑤ 组合级 equity curve。
- 借鉴:FINSABER、StockBench + Look-Ahead-Bench、alphalens、QuantStats。

### L3 风控 / 仓位 / 组合 — P1
- 现状:单标的分析,无组合层、无风险预算、无回撤约束。
- 差距:仓位 sizing(波动率/凯利分数)、组合构建(风险平价/CVaR/HRP)、敞口与相关性上限、分市场风险(A股 T+1/涨跌停、crypto 杠杆爆仓)。
- 借鉴:Riskfolio-Lib(设计)、vnpy CTA 成本/止损设计。

### L4 分市场可执行性与「港股通」专属空白 — P0(港股通)/ P1(A股制度)
- A股:涨跌停(封板买不进)、T+1、最小手数、停牌、ST——信号须带**可执行性校验**(涨停价买入信号是废信号)。借鉴 TradingAgents-astock 制度建模。
- **港股通(明确功能缺口)**:本项目有"港股"数据,但无"港股通"专属逻辑。港股通 ≠ 港股:① 标的须在**港股通成份**内(非所有港股可买)、② 南向资金流向、③ 持股统计、④ 汇率(CNH)、⑤ 交易日历差异。借鉴 akshare 南向/港股通端点(零新增依赖)。
- crypto:funding/爆仓/24-7——相对已覆盖(有 perp)。

### L5 信号生命周期 / 交付 / 追踪 — P1
- 现状:批量报告+通知;M3.1 已有 `status`(active/aging/expired)雏形。
- 差距:信号全生命周期(开仓/触发目标/止损/失效)、带入场·止损·目标的实时/定时推送、**实际 vs 预测追踪回填**(闭合可信度回路)、失效告警。
- 借鉴:自建(扩 M3.1 status);TradingAgents 决策日志。

### L6 LLM 可信度 / 防幻觉 / 合规 — P0
- 现状:LLM 单轮分析,无自校验。
- 差距:① **claim-validation**(LLM 价位/信号 vs VPS 计算值交叉核对,防编造)——qrak/LLM_trader;② **辩论+风控 gate 再出 BUY**——TradingAgents;③ **降级即拒答**(fallback/数据稀薄时降权或拒出,不编造)——FailSafeQA;④ **合规定位**(见 §3)。

---

## 5. 执行方案:增量分解与排序

**工程纪律(沿用本项目一贯做法,每增量必须满足)**:opt-in / 默认不改变现有行为(字节级)/ 不破坏现有契约(追加字段优先)/ 多源 fallback 与稳定性优先 / 逐增量走 brainstorming→spec→plan→subagent-driven + 对抗式审查 + ci_gate。每个增量都是可独立交付、可独立回滚的 mini-epic。

### 增量清单(每项:范围 / 交付物 / 验收 / 依赖 / 沙箱可验性)

**Inc 0 —(前置门)合规定位拍板 + TradeSignal 契约定型** — P0,非代码为主
- 范围:§3 三选一定位(人决策);定义 canonical `TradeSignal` schema(`src/schemas/`),含波动率自适应止损/目标字段;定义"信号 vs 分析结论"的呈现边界(按定位)。
- 交付物:定位决策记录 + `TradeSignal` schema + 呈现契约文档。
- 验收:schema 通过 pydantic/schema 测试;不改变现有报告载荷(追加而非替换)。
- 依赖:无(但 gates L1/L4/L5/L6-②)。
- 说明:**everything 挂在此契约上**,先定型,但**合规那一半必须你拍**。

**Inc 1 — 回测严谨性升级(walk-forward + 样本外 + 风险调整指标)** — P0,**推荐首个代码增量**
- 范围:在既有 `signal_backtest_service` / 链路A/B 上追加:滚动 walk-forward 评估(train/test 窗+步长)、样本外切分、Sortino/maxDD/Sharpe 与 tear-sheet 式汇总、阈值搜索的 data-snooping 感知报告(多重检验标注)。默认关闭、opt-in;既有 `hit_rate/CI/horizon` 输出不变。
- 交付物:新评估模式 + 指标扩展 + 文档;CLI/API opt-in 开关。
- 验收:crypto 真实分钟数据端到端(沙箱可验,BINANCE_BASE_URL);A股/HK 离线确定性用例;既有链路B 用例零回归。
- 依赖:无(评估既有信号,不需 TradeSignal / 不需合规)。
- 沙箱可验:**是**(crypto 真网 + 离线)。**这是命门短板、增量安全、可离线验、无合规依赖 → 最佳起点。**

**Inc 2 — 港股通专属维度** — P0
- 范围:`data_provider` 增补 akshare 南向资金 / 港股通成份 / 持股统计端点(带 fallback,东财不可达时 fail-closed);港股通**成份 universe 过滤**(标的可买性);南向资金特征进报告/信号维度。
- 交付物:数据适配 + universe 过滤 + 报告字段;文档。
- 验收:离线单测(端点 schema/成份过滤/1m fail-closed)+ facade 兜底;真网端到端待 eastmoney 可达环境(与既有 HK MVP 同现实约束)。
- 依赖:无。沙箱可验:部分(离线全覆盖;东财真网受沙箱 egress 限制)。

**Inc 3 — LLM claim-validation 守卫** — P0
- 范围:在 LLM 分析出结论后,把其陈述的价位/信号/指标与 VPS 计算值交叉核对;不一致则降权/标注/拒出;沿用本项目 fallback 降级即拒答哲学。
- 交付物:校验层 + 一致性标注字段(追加)+ 文档。
- 验收:离线 fixture(LLM 编造价位→被拦;一致→通过);不改变一致时的既有行为。
- 依赖:轻依赖 TradeSignal(可先对现有报告字段做);沙箱可验:**是**(离线)。

**Inc 4 — 决策收敛层(多空辩论 + 风控 gate → TradeSignal)** — P1
- 范围:把单轮 LLM 分析重构为"多空辩论→交易员→风控审批"管线,产出 TradeSignal;结构可离线 mock,真 LLM 需在线。
- 依赖:Inc 0(TradeSignal)+ Inc 3(claim-validation)。沙箱可验:结构离线;真 LLM 需在线。

**Inc 5 — 仓位/风控/组合 + A股制度可执行性** — P1
- 范围:波动率/凯利仓位 sizing;A股 涨跌停/T+1/最小手数**可执行性过滤**(废信号剔除);(后续)组合风险预算。
- 依赖:Inc 0 + Inc 1。沙箱可验:是(离线确定性)。

**Inc 6 — 信号生命周期追踪** — P1
- 范围:扩 M3.1 `status` 为完整生命周期(开/触目标/止损/失效)+ 实际 vs 预测回填(闭合可信度回路)+ 失效告警。
- 依赖:Inc 0 + Inc 1。沙箱可验:是。

**Inc 7 — 因子面扩充 + 参数扫描工业化 + 因子有效性** — P2
- 范围:借鉴 alpha 因子库扩 VPS 因子面;interval 阈值网格扫描工业化;alphalens 式 IC/分层因子有效性。
- 依赖:Inc 1(回测纪律先立)。沙箱可验:crypto 真网 + 离线。

### 依赖图与推荐路径

```
Inc 0 (契约+合规,门) ──┬─→ Inc 4 (决策层) ─→ Inc 5 (仓位/制度) ─→ Inc 6 (生命周期)
                        │
Inc 1 (回测严谨性)★首 ──┴─→ Inc 7 (因子/扫描)
Inc 2 (港股通)  ── 独立,可并行
Inc 3 (claim-validation) ── 近独立,可并行
```

**推荐执行顺序**:
1. **Inc 0**:你拍合规定位(§3)+ 我起草 TradeSignal 契约(轻,先定型)。
2. **Inc 1(回测严谨性)= 第一个真正开工的代码增量** —— 命门、增量安全、离线+crypto 真网可验、无合规依赖。
3. 并行:**Inc 2(港股通)**、**Inc 3(claim-validation)**。
4. 待 Inc 0 契约定型:**Inc 4 → Inc 5 → Inc 6**。
5. 长期:**Inc 7**。

---

## 6. 借鉴映射速查(gap → OSS,只学设计)

| 差距 | 借鉴项目 | 具体学什么 |
|---|---|---|
| 回测严谨性(L2) | FINSABER / StockBench / Look-Ahead-Bench / QuantStats / alphalens | 滚动窗防 snooping、next-open 防 look-ahead、post-cutoff 无污染切片、alpha decay、Sortino/maxDD、IC/分层 |
| 决策收敛(L1/L6) | TradingAgents / Trading-R1 | 多空辩论→风控 gate 编排、结构化决策输出、波动率调整决策 |
| 防幻觉(L6) | qrak/LLM_trader / FailSafeQA | 结论 vs 计算值 claim-validation、降级即拒答 |
| A股制度(L4) | TradingAgents-astock | T+1/涨跌停/最小手数/沪深300 基准 |
| 港股通(L4) | akshare 端点 | 南向资金 / 成份 universe / 持股统计 |
| 仓位/组合(L3) | Riskfolio-Lib | 波动率/凯利仓位、CVaR/风险平价/HRP |
| 因子面(L7) | Vibe-Trading / vectorbt | alpha 因子库、参数网格扫描 |

---

## 7. 明确不做 / 风险

- **不做自动下单执行**(超出"分析与决策支持"定位;涉券商/交易所 API 与更高合规,除非定位改变)。
- **不直接内嵌** GPL/AGPL/非商用代码(只借鉴设计)。
- **不在合规定位未拍板前**对外分发"明确 A股买卖指令"。
- 风险:LLM 择时的样本外可信度(Inc 1 前不可轻信);东财真网在本沙箱不可达(港股通端点端到端待环境);data-snooping(阈值标定必须带多重检验校正)。

---

## 8. 下一步

1. 你拍板 **§3 合规定位**(自用 / 持牌 / 教育免责)——产品红线。
2. 选定**首个开工增量**(推荐 **Inc 1 回测严谨性**;或 **Inc 2 港股通**)。
3. 该增量走 brainstorming→spec→plan→subagent-driven,逐增量交付。
