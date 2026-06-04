# 设计方案：AI 自动选股系统（个人版 · QuantPick）

- 日期：2026-06-04
- 状态：已确认（阶段零骨架已落地；已追加择时/信号层设计与骨架）
- 作者：Bruce + Claude

## 1. 背景与目标

为个人构建一个**离线运行的 A 股 / 港股选股研究工具**：以规则量化 + 技术指标打分为地基，叠加可插拔的 AI（LLM）定性分析，每日产出**带评分和理由的候选股清单**，并结合成交量、趋势等技术指标给出**买卖操作建议（择时）**。

目标用户与边界：**仅个人使用**——不对外提供荐股服务（在中国向他人提供证券投资建议属持牌业务），因此无需多用户、鉴权与合规牌照，专注分析质量本身。

## 2. 关键决策（已确认）

| 决策点 | 选择 | 理由 |
|---|---|---|
| 使用场景 | 个人离线工具 | 规避合规负担，聚焦分析质量 |
| 核心方法 | 规则量化打地基 + AI 增强 | 可解释、可回测、风险可控、可渐进 |
| 输出形态 | 每日选股清单 + 评分排序 | 最实用、风险最低的起点 |
| 技术栈 | Python 为主 | 契合 A 股 / 港股量化生态 |
| 操作周期 | 多周期可配置，默认中短线波段 | 不锁死；技术指标在波段最有效 |
| AI 增强 | 先 LLM 定性分析，预留 ML 接口 | 低成本高价值，已有 Claude；ML 后置 |
| 数据源 | 全免费：akshare 主 + baostock 备 + adata 补；港股走 akshare | 个人用足够，零成本 |
| 错误处理 | 数据/批处理边界用 `Result`/错误码；内部用异常 | 健壮不崩，又不与 Python 生态打架 |
| 存储 | parquet（行情）+ SQLite（元数据），不引入 MySQL | 零部署、单机足够 |
| 买卖信号范围 | 候选股买入 + 持仓/自选卖出 | 卖出需持仓上下文，新增轻量 `portfolio` 模块 |
| 信号逻辑 | 趋势过滤 + 量价触发 | 经典稳健、可解释、震荡市少误信号 |
| 建议粒度 | 方向 + 强度(0–100) + 参考价位 | 可操作；ATR 仅用于算止损止盈距离、不参与方向 |

## 3. 范围与分阶段

- **阶段零（已完成）**：项目骨架、接口、类型契约、配置、CLI 命令壳、示例策略、测试骨架；并追加择时/信号层与持仓模块骨架。
- **阶段一（MVP）**：数据层（akshare + 缓存 + 增量）、指标计算、因子取值、硬过滤、`Screener.run`、CLI 表格输出 → 跑出"今日候选股 + 评分拆解"。
- **择时/信号层（本次新增，紧接阶段一）**：trend 趋势过滤 + volume/momentum 触发、价位(ATR+摆动点)、portfolio 持仓、`advise` 命令与报告"操作建议"区、信号回测验证。
- **阶段二**：前向收益回测、Markdown 每日报告、更多策略。
- **阶段三**：AI（LLM）增强层（结构化分析卡，启用 prompt caching）。
- **阶段四（可选）**：港股深化、消息推送、ML 排序、C++ 加速热点。

## 4. 架构

分层、单向依赖：`data → indicators → factors → screening → strategies → signals → ai → backtest → report`，外加 `portfolio`(持仓上下文)，入口 `quantpick/cli.py`。`core` 提供配置/类型/错误码/日历/日志，被各层依赖且不反向依赖业务层。模块职责与数据契约见 `docs/architecture.md`。

数据流：

```
update : 多源拉取 → 本地 parquet/sqlite 缓存（增量）
screen : 指标 → 因子 → 硬过滤 → 多因子打分 → 排序 → Top-N
advise : 候选股买入信号 + 持仓卖出信号（趋势过滤 + 量价触发 → 方向/强度/价位）
ai     : 对 Top-N 生成 LLM 结构化分析卡（可选）
report : CLI 表格 + Markdown 报告（含操作建议区）
backtest: 用历史选股/信号验证胜率、超额（可选）
```

## 5. 数据源策略

- 统一日线 schema：`date, open, high, low, close, volume, amount`，升序。
- A 股：akshare 主（`stock_zh_a_hist`），baostock 备（历史更稳），adata 补（多源容错）。
- 港股：akshare（`stock_hk_hist`）；覆盖不算最全，**已知风险**，后续可接 futu / longbridge。
- `DataManager` 实现"缓存优先 + 按 `supports(market)` 顺序降级"；单只股票失败返回错误码、不中断整批。

## 6. 因子与打分

- 因子分类：技术面（趋势/动量/量价）、基本面（估值/质量/成长）、（可选）K 线形态。
- 每个因子产出单值 → 截面归一化（`zscore` / `rank_pct` / `winsorize`，已实现）→ 按策略权重加权求和（`weighted_score`，已实现，按总绝对权重归一，便于跨策略比较）。
- 方向：`higher_is_better=False` 的因子（如 PE）由打分阶段翻转。
- 可解释性：每只候选股保留各因子得分明细（`ScoredStock.factors`），报告中可拆解。

## 7. 策略

- 策略 = YAML（`name / description / markets / factor_weights / top_n`），见 `config/strategies/*.yaml`。
- 加策略 = 加配置，不改代码。已提供 `ma_trend`、`momentum_breakout`、`value` 三个示例。

## 8. AI 增强层

- `Analyst` 接口；`LLMAnalyst` 对 Top-N 候选汇总（评分拆解 + 关键指标 + 基本面 + 近期新闻/公告）→ 让 LLM 产出结构化卡（结论/看多/看空/风险）。
- 多模型适配（Claude / DeepSeek / 通义 / 本地 Ollama）；**密钥仅来自环境变量**；启用 prompt caching 降本；失败软降级（报告仍可出）。
- 预留 `MLRanker` 接口供阶段四接入。

## 9. 回测

- 轻量、非撮合级：对历史某日选股/信号计算 T+5/10/20 前向收益、胜率、相对沪深 300 / 恒生超额、最大回撤。
- 目的是验证打分策略与择时信号的有效性，不追求完整交易模拟（那是 backtrader/qlib 的领域）。

## 10. 风险与合规

- **合规**：仅个人使用；所有输出标注"非投资建议"（`ai.base.DISCLAIMER`，报告页脚渲染）。
- **港股数据完整性**：免费源覆盖有限，已在数据层用 adapter 抽象，便于后续替换。
- **过拟合**：规则/因子/信号需用回测和样本外检验，避免参数过拟合。
- **数据质量**：多源交叉校验、缺失/停牌处理在数据层与过滤层兜底。
- **持仓隐私**：`config/holdings.yaml` 含个人持仓，已 gitignore；仓库只跟踪 `holdings.example.yaml`。

## 11. 开源借鉴

数据：akshare / baostock / adata；指标：pandas-ta（可选 TA-Lib）；规则选股范本：InStock（指标/形态/策略/回测的模块划分）；因子素材：QuantsPlaybook；LLM 增强范本：daily_stock_analysis、TradingAgents-CN；ML 思路：Microsoft qlib。**借鉴架构与因子，不被重型框架绑定。**

## 12. 择时 / 信号层（买卖操作建议，已确认新增）

### 12.1 目标与边界
在选股（横截面：选谁）之上增加择时（时间序列：何时进出）。买入信号作用于每日候选股；卖出信号作用于 `portfolio` 持仓/自选清单。日频（收盘后算、次日操作），不做盘中实时。

### 12.2 架构新增
- `quantpick/signals/`：`base`(SignalDirection / Regime / PriceLevels / Signal 类型) · `trend`(趋势过滤闸门 → Regime) · `triggers`(量价 + 动量触发，`BUY_RULES` / `SELL_RULES`) · `levels`(价位计算) · `engine`(`SignalEngine` 编排)。
- `quantpick/portfolio/`：`holdings`(`Holding` + `load_holdings`，读 `config/holdings.yaml`)。
- 配置：`config/signals.yaml`(阈值) + `config/holdings.yaml`(持仓，gitignore；模板 `holdings.example.yaml`)。
- CLI：新增 `advise`(候选买入 + 持仓卖出)；`run` 串入 advise。报告新增"操作建议"区。

### 12.3 信号逻辑（趋势过滤 + 量价触发）
1. 趋势过滤(闸门)：MA(20/60) 排列 + ADX 判 `UP / DOWN / RANGE`；`ADX < adx_min` → RANGE，抑制趋势信号。
2. 买入触发(仅 UP)：MACD 金叉 / 回踩均线放量企稳 / 放量突破 N 日高，且 RSI 不超买、量比达标确认。
3. 卖出触发(持仓)：MACD 死叉 / 放量跌破均线 / RSI 超买背离 / 跌破跟踪止损或摆动低点 / 触及止损止盈。
4. 强度(0–100)：由 `ADX 强度 + 命中触发数 + 量能确认 + 动量` 综合（非投票制）。

### 12.4 价位（ATR 方案，已确认）
`止损 = max(入场 − stop_atr_mult × ATR, 近期摆动低点)`；`止盈 = 入场 + target_rr × (入场 − 止损)`（默认 2:1）。ATR 仅作风险尺度，不参与方向判断。`signals.levels.stop_from_atr / build_levels` 已实现并测试。

### 12.5 输出与类型
每只：方向(BUY/SELL/HOLD) + 强度 + 命中规则(理由) + 参考价位(入场/止损/止盈)。`Signal` 为择时层规范输出；report 层把 `ScoredStock`(选股) 与 `Signal`(择时) 组合成"操作建议"。**core 不依赖信号层**（保持单向依赖）。

### 12.6 回测衔接
信号规则可回测：买入触发后的前向收益/胜率，复用 `backtest` 层验证择时有效性；上线前需样本外检验，防过拟合。

## 13. 免责声明

本项目仅用于个人学习与量化研究。所有输出不构成任何投资建议，不保证准确性或盈利性。据此操作风险自负。股市有风险，投资需谨慎。
