# M3 · 信号可信度 + 量价丰富度 设计 spec

> 状态：设计已与用户确认（2026-06-17），待写实现计划。
> 史诗脉络：K 线可视化 + 量价信号引擎（M0–M2d，已上线）→ 容器 A 抽屉 / C 看板 / B 工作台（已并入 main）→ **M3 本期：把"信号标注"升级为"可信、可操作的买卖信号"**。
> 前序资产：引擎 `src/services/volume_price_signals.py`（`SignalMarker`@:78、`derive_price_levels`@:293、量价八法/OBV 背离/放量突破/锚定 VWAP/canonical Wilder ATR/B 类降权 top-k）；契约/编排 `src/services/signals_service.py`（`build_signals_for_code`、双轨 rule+LLM、consistency、stale）；命中率回填 `src/services/signal_hit_rate.py`（`backfill_signal_hit_rate(signal_type, code)`、`resolve_marker_hit_fields(signal_type, code)`，当前按 code 聚合 completed `BacktestResult`）；看板 `src/services/signal_board_service.py`（`build_board(codes, …)`）；前端 `apps/dsa-web/src/components/kline/SignalDrilldownPanel.tsx`、`components/board/SignalBoardGroup.tsx`、`components/workstation/StockSignalsPanel.tsx`、`types/kline.ts`。

## 1. 目标与定位

把当前"日线收盘级、按 code 近似、做多方向、看图标注"的信号，升级为：**有规则级三重门回测背书、按「信号类型 × 市场」分层胜率、用多量能指标共振研判**的可信、可操作买卖信号。

两条主线：
- **A 可信度**：让"已验证"真正可信——每类信号有规则级前向回测的胜率 + 置信区间 + 相对买入持有的超额，用户看到的是"可下注证据"而非裸命中率。
- **B 丰富度**：兑现用户原话"用丰富成交量数据识别量价一致/背离并研判"——引入 CMF/MFI/量比进引擎判定，背离要多源共振才高置信，并量化背离强度与量能形态。

**定位口径（与用户确认）**：纯后端/引擎深耕，**零新数据源、零实时基础设施**；引擎纯函数写成 **bar-interval 无关**，为下一阶段"盘中实时择时"留口（M3 本身仍只跑日线）。

## 2. 范围与非目标（YAGNI）

**v1（M3）范围**
- A1 三重门评估器（纯函数，bar-interval 无关）：对历史 OHLCV 因果前向走查，重跑信号规则得触发点 + 复用 `derive_price_levels` 得 entry/止损/目标，前瞻 `horizon` 根 bar 判定 赢/输/平。
- A2 预计算批处理作业：对**自选池**标的跑 A1，按 **(signal_type × market)** 聚合胜率 + 样本 + Wilson 95% CI + 同窗口买入持有基准超额，落库 `signal_stats`。
- A3 可信度门控 + 契约：marker 命中率字段改读 (type×market) `signal_stats`；`SignalMarker` 增字段；`verified` 语义升级为"样本≥N 且 CI 下界 > 基准"。
- A4 展示（前端 additive，三处）：钻取面板 + 工作台信号 tab + 信号看板行。
- B1 新量能指标纯函数：CMF、MFI、量比（OHLCV 纯算，bar-interval 无关）。
- B2 多源背离共振（OBV+CMF+MFI）+ 背离强度量化分级。
- B3 量能形态分级（温和放量/天量/地量/缩量回踩）进 marker 语义。
- B4 crypto 量价参数差异化（引擎按 market 取参数；消化既有 plan-deferred knownGap）。

**非目标（M3 不做）**
- 换手率（需流通股本数据）→ M4 数据面。
- A 股资金面（主力资金流/龙虎榜/北向持股/融资融券/筹码分布，均需新数据源）→ M4。
- 周/月线多周期（当前 `stock_service.py` 对非日线 422 硬拒）→ M4·D1。
- 分钟级 K 线 / 实时行情流（WebSocket/SSE）/ 盘中数据源 → 后续实时阶段。
- 做空 setup 价位线（E1）、含仓位的交易计划（E2）→ 后续。
- 完整回测净值曲线 / TradingView 式 Strategy Tester UI / K 线逐根回放 → 后续；M3 仅把胜率数值上屏。
- 不改 A 抽屉 / C 看板 / B 工作台的既有交互（纯增量，仅在三处追加可信度展示）。

## 3. 锁定决策

| # | 决策 | 取值 |
| --- | --- | --- |
| D1 | 回测产出定义 | 三重门：N 个 bar 内先触目标=赢 / 先触止损=输 / 到期未触=平；复用 `derive_price_levels`(@:293) 的 entry/stop/target |
| D2 | 胜率聚合粒度 | **signal_type × market**（market ∈ {cn, hk, us, crypto}，由 code 分类得出） |
| D3 | 执行模式 | 预计算批处理（周期作业落 `signal_stats`，marker 读预计算值；**非** on-demand、**非** hybrid） |
| D4 | 可信门控 | 样本≥N（复用 `signal_hit_verified_min_sample`）+ Wilson 95% CI + 同窗口买入持有基准超额；`verified = 样本≥N 且 ci_low > baseline_win_rate` |
| D5 | 量价丰富度 | CMF+MFI+量比 进引擎判定；OBV+CMF+MFI 多源背离共振 + 强度分级；量能形态分级。换手率不在本期 |
| D6 | 盘中就绪 | 引擎/评估器纯函数 **bar-interval 无关**（按 bar 数计、不硬编码"交易日"；`signal_stats` key 预留 interval 维，默认 `1d`）；M3 仍只跑日线、不接实时数据 |
| D7 | 批作业覆盖范围 | 自选池（与容器 C 看板同源的自选标的集合） |
| D8 | 可信度展示落点 | 钻取面板 `SignalDrilldownPanel` + 工作台信号 tab `StockSignalsPanel` + 看板行 `SignalBoardGroup`（三处均 additive） |
| D9 | crypto 参数差异化(B4) | 纳入 M3（引擎按 market 取参数，缺省回落日线 A 股口径） |

## 4. 架构与组件

### 4.1 A 可信度

**A1 三重门评估器**（新纯函数模块，建议 `src/services/signal_backtest.py`，bar-interval 无关）
- 输入：单标的的历史 OHLCV bars（按 bar 序）、market、horizon（bar 数）、可选 crypto/market 参数组。
- 过程：对每根历史 bar `t` **仅用 ≤t 的数据**（因果，复用引擎既有防未来函数保证）重跑信号检测得在 `t` 触发的 markers；对每个触发点用 `derive_price_levels` 得 entry/stop/target；前瞻 `[t+1, t+horizon]`：先触 target=赢、先触 stop=输、到期未触=平（同 bar 同触按"先止损"的保守口径，实现期定义并测试）。
- 输出：`list[SignalOutcome]`，每条 `{signal_type, market, direction, outcome ∈ {win, loss, expired}, trigger_ts}`。
- 仅评估**做多** setup（与现状一致；做空 E1 为非目标）。

**A2 预计算批处理作业**（新作业入口，可并入现有 dsa-daily 或独立脚本/CLI）
- 取自选池 codes → 各自取历史 OHLCV（复用既有日线取数路径）→ 跑 A1 → 汇总到 (signal_type × market) 桶。
- 每桶统计：`sample`（win+loss，expired 不计入分母，与现 `hit_sample` 口径一致：仅"有方向结论"的样本）、`win_rate = win/sample`、Wilson 95% `ci_low/ci_high`、`baseline_win_rate`、`excess = ci_low - baseline_win_rate`。
- **baseline 单一定义（固定，无歧义）**：`baseline_win_rate` = **同 market、同 horizon、同一三重门规则、对全体 bar（非仅信号触发点）入场**的赢率——即"在这个市场，随便哪天按同样的目标/止损/到期规则入场"的基准赢率。如此 signal 胜率 vs baseline 是**同口径**比较，`excess` 才真正度量"信号相对随机入场的超额边际"。（不用"简单买入持有上涨占比"，因其退出逻辑与三重门不可比。）
- 落库 `signal_stats`（见 §6），key = `(signal_type, market, interval=1d, horizon)`；带 `computed_at` 供陈旧度。
- 幂等：同 key 覆盖写（`replace_existing` 语义，参考 `backtest_repo.save_results_batch`）。

**A3 可信度门控 + 契约重写**
- 改写 `signal_hit_rate.py` 的聚合**源**：`resolve_marker_hit_fields(signal_type, code)` 不再聚合该 code 的 completed `BacktestResult`，改为按 `market(code)` 读 `signal_stats[(signal_type, market, 1d, horizon)]`。函数签名不变（已是 `(signal_type, code)`，前向兼容承诺在 `signals_service.py:224-235` 已就绪）。
- 命中字段映射（沿用既有 `SignalMarker` 字段名 `hit_rate/hit_sample`，内部统计 `win_rate/sample` 在 resolver 边界映射过去，保持契约兼容）：
  - 无桶 / `sample < min_sample` → `hit_rate=None, hit_sample=None, verified=False`（对齐"无样本则 null"契约，UI 标"样本不足"）。
  - 有桶且 `sample≥min_sample` → 回填 `hit_rate(=win_rate)/hit_sample(=sample)/ci_low/ci_high/baseline_excess`，`verified = ci_low > baseline_win_rate`。
- 旧的 per-code `BacktestResult` 命中率路径对**信号 marker** 停用（store 与代码保留，供其它消费方），即"改源不删库"。

**A4 展示**（前端，additive，三处）
- 契约新增字段（snake→camel 经现有 mapper）：`SignalMarker` 增 `ciLow / ciHigh / baselineExcess`（`hitRate/hitSample/verified` 已存在，语义升级为 type×market）。
- 文案统一（复用现有"暂无样本"风格）：`命中率 X% [a–b] · 样本 n · 超额 +Δpp · 已验证` / `样本不足` / `无超额`（已验证=verified、无超额=有样本但 ci_low≤baseline）。
- 落点：`SignalDrilldownPanel`（钻取详情新增 CI/样本/超额行）、`StockSignalsPanel`（工作台信号 tab 命中率列扩展为带 CI/超额的可信度单元）、`SignalBoardGroup`（看板行命中率单元同口径展示）。三处共用一个纯展示格式化函数（避免现有 `fmtHit`/`formatHitRate` 三处复制的口径漂移再扩散——本期顺带抽出共享 util）。

### 4.2 B 丰富度

**B1 量能指标纯函数**（并入 `volume_price_signals.py`，bar-interval 无关）
- CMF（Chaikin Money Flow，窗口可配）、MFI（Money Flow Index，窗口可配）、量比（current vol / 过去 N bar 均量）。均 OHLCV 纯算、无新数据源。窗口不足 → 该指标 presence-only 省略。

**B2 多源背离共振 + 强度分级**
- 现状仅 OBV 单源背离 → 升级为 OBV/CMF/MFI 三源；仅当 ≥2 源（实现期定阈，默认多数源）同向背离才判"高置信背离"信号；单源仅作弱提示或不出信号。
- 背离强度量化：用背离幅度（价格极值与指标背向程度）× 持续 bar 数映射为强度档（如 weak/medium/strong），写入 marker（reason/confidence）。

**B3 量能形态分级**
- 基于量比 × ATR/价档：温和放量 / 天量 / 地量 / 缩量回踩 等档位，进 marker 语义（喂钻取/看板/工作台展示与一致性判定）。纯函数扩展，不新增数据。

**B4 crypto 参数差异化**
- 引擎入口按 `market` 选参数组：crypto（7×24、无涨跌停、高波动）用单独 ATR 周期 / 突破窗口 / 八法量档阈值；非 crypto 缺省回落现日线 A 股口径。参数走 `.env.example` + `config_registry`，不配置走默认。

## 5. 数据流与执行

- **离线/批（A2，周期）**：自选池 codes → 各取历史 OHLCV（日线，interval=1d）→ A1 三重门（复用 `derive_price_levels`，因果前向）→ 按 (signal_type × market) 聚合（Wilson CI + baseline）→ 落 `signal_stats`（覆盖写 + `computed_at`）。
- **在线/请求（不变快路径）**：`/signals`（`build_signals_for_code`）→ `compute_volume_price_signals`（含 B1 指标 + B2 多源背离 + B3 形态 + B4 市场参数）→ marker 命中字段经 `resolve_marker_hit_fields` 从 `signal_stats` 读 (type×market) → 双轨契约 + A4 三处展示。请求路径**不触发**回测，仅读预计算。

## 6. 契约与存储变更

- **`signal_stats` 存储**（新表，建议 `src/storage.py` 加表 + `backtest_repo` 同款 repo 或新 `signal_stats_repo`）：
  - 主键 `(signal_type, market, interval, horizon)`；列 `win, loss, sample, win_rate, ci_low, ci_high, baseline_win_rate, excess, computed_at`。
  - `interval` 默认 `'1d'`（D6 预留维，本期只写 1d）。
- **`SignalMarker` 契约（`volume_price_signals.py` dataclass + 前端 `types/kline.ts`）**：新增 `ci_low / ci_high / baseline_excess`（可空）；`hit_rate/hit_sample/verified` 保留、语义改为 type×market。前端 `stocks.ts` mapper 增对应 camel 字段。
- **配置（`.env.example` + `src/config.py` + `src/core/config_registry.py`，不配置走默认）**：
  - `SIGNAL_BACKTEST_HORIZON_BARS`（三重门前瞻 bar 数，默认值实现期定，建议 ~10）。
  - `SIGNAL_HIT_VERIFIED_MIN_SAMPLE`（复用既有）。
  - `SIGNAL_BACKTEST_ENABLED` / 覆盖范围开关（默认自选池）。
  - crypto 参数组：`VPS_CRYPTO_*`（ATR 周期/突破窗口/量档阈值；不配置回落默认）。
- 同步更新 `docs/volume-price-signals.md`、`docs/CHANGELOG.md`（`[Unreleased]` 扁平一行）。

## 7. 错误 / 空态 / 降级（沿用现哲学）

- `signal_stats` 缺桶 / 小样本 → marker 标"样本不足"、`verified=False`，**不阻塞** `/signals` 主流程。
- 新量能指标某项窗口不足 → presence-only 省略该指标，不拖垮信号生成。
- 批作业单标的失败 → 跳过该标的、记日志，不影响其它标的聚合（单源失败不拖垮整批）。
- crypto 参数缺省 → 回落日线 A 股口径。
- baseline 不可得（数据不足）→ 该桶 `verified=False` 并标"无基准"，不误判为已验证。

## 8. 测试策略

- **后端纯函数**：
  - A1 三重门：先触目标=赢 / 先触止损=输 / 到期未触=平 / 同 bar 同触保守口径；防未来函数（评估只用 ≤t 数据）。
  - A2 聚合：Wilson CI 边界（小样本宽区间）、样本门槛、baseline 超额判定（ci_low>baseline 才 verified）、expired 不计入分母。
  - B1：CMF/MFI/量比 对已知 OHLCV 序列的确定值；窗口不足省略。
  - B2：单源背离不触发高置信、多源同向才触发；强度分级边界。
  - B3：各量能形态档位边界。
  - B4：crypto 取差异化参数、非 crypto 回落默认。
- **存储/作业**：`signal_stats` 落库 + 覆盖写幂等 + 读取；`resolve_marker_hit_fields` 改读 type×market（缺桶→样本不足、有桶→回填+verified）；`signals_service` 集成读取。
- **前端**：钻取/信号 tab/看板行 三处展示新字段与文案（已验证 / 样本不足 / 无超额 / CI 区间）；共享格式化 util 单测。
- **门禁**：后端 `./scripts/ci_gate.sh`（含新测试）；前端 `npm run lint` + 完整 `vitest` + `npm run build`；无空格持久 worktree 验证（沿用 A/C/B）。

## 9. 风险与缓解

- **历史信号重算成本（A1×自选池）**：批作业对每标的全历史逐 bar 重跑信号。缓解：限自选池（D7）、限历史窗口、批作业离线跑、覆盖写；度量后再决定是否扩池/增量更新。
- **baseline 计算成本**：全体 bar 入场赢率需对每根 bar 算价位+前瞻（比仅信号触发点重）。缓解：与 A1 同一前向走查复用（一次走查同时产 signal 与 all-bar 两组 outcome）、离线批跑、覆盖写；baseline 定义已固化为单一口径（§4.1 A2），无歧义。
- **改写命中率源的回归**：`resolve_marker_hit_fields` 改源可能影响现有 marker 展示与容器 C/B 既有测试。缓解：签名不变、缺桶=样本不足（与旧"无样本"表现一致）、补回归测试覆盖三处消费方。
- **B2 多源背离假阴性**：要求多源共振会减少信号数量。缓解：单源保留为弱提示档（不直接出高置信信号），强度分级让用户分辨；阈值可配。
- **bar-interval 无关的过度抽象**：D6 仅要求纯函数按 bar 计、不硬编码交易日 + stats 预留 interval 维；**不**在本期引入任何实时/分钟数据路径，避免范围蔓延。
- **crypto 参数(B4)污染既有 A 股口径**：缺省必须逐字节回落现状。缓解：默认参数=现状常量，仅 crypto 分支取新值 + 回归测试守住非 crypto 不变。

## 10. 实现期需核实项

1. 既有"前向回测/`BacktestResult`"能力的确切入口与是否可直接复用为 A1 的前瞻判定（roadmap 称有 BacktestEngine 前向框架；需定位实际模块/函数，能复用则复用，不能则 A1 自含三重门走查）。
2. `derive_price_levels`(@:293) 在做多 setup 下对历史任意 bar 可独立调用（仅依赖 ≤t 数据，无 HomePage/实时状态耦合）。
3. market 分类来源：复用 `data_provider/akshare_fetcher._is_hk_code/_is_us_code` + crypto 检测，得 {cn, hk, us, crypto}；确认无遗漏分支。
4. 自选池 codes 的标准取数入口（容器 C 看板用的同一来源），供 A2 复用。
5. 信号检测纯函数可在"给定历史片段、仅用 ≤t 数据"下逐 bar 调用而不重复全量装配（性能与因果性）。
6. `signal_stats` 落库走 `src/storage.py` 建表 + 既有 repo 模式（参考 `backtest_repo`），迁移对现有 DB 安全（仅新增表）。

## 11. 执行方式

沿用 A/C/B：**Subagent-Driven**（每任务 fresh 实现 subagent + 两段 review），无空格**持久** worktree（`/root/<名>`，前端 `npm ci` + 全量门禁；勿用 /tmp）。spec 获批 → 调 writing-plans 出实现计划（M3-x 分阶段，TDD：先后端引擎/评估器/聚合/存储，再 service 接线，再前端三处展示，最后批作业 + 门禁 + 文档）。
