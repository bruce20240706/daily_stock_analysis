# K 线可视化 + 量价信号引擎 + 图上买卖标注（第一期 L3）设计

- 日期：2026-06-15
- 状态：**Draft v2（已吸收对抗式专家审查，收敛 6 个 blocking 缺口）**
- 主题：把"分析+推送"系统补齐"可视化 K 线 + 由真实指标得出的买卖信号在图上标注 + 量价一致/不一致研判"这条最后一公里
- 关联：差距分析（多智能体审计产物）+ spec 专家审查（5 视角对抗式，verdict=needs-revision，本 v2 逐条处置）

---

## 1. 背景与问题

系统已能对 A股 / 港股 / 加密永续做确定性指标计算 + LLM 研判 + 回测，后端也已通过 `GET /api/v1/stocks/{code}/history` 暴露日线 OHLCV。但：

- **分析结论以 Markdown 文本 / 飞书卡片为终点**，前端零图表消费 `/history`（唯一图表是 `PortfolioPage` 的 recharts 饼图）。
- **买卖信号没有机读契约**：`SniperPoints`（`report_schema.py:91`，`ideal_buy` 等为 `Union[str,int,float]`）实际是带中文标签的 LLM 散文价位，无时间锚/方向枚举。
- **真实指标没驱动用户看到的结论**：确定性规则信号（`_generate_signal`，`src/stock_analyzer.py:584`）只对**最新一根** bar 评估、产出单个 `BuySignal` 枚举，且只作 context；最终 `operation_advice` 由 LLM 决定。注意：`operation_advice` 落库为 `String(20)` **方向枚举**（`src/storage.py:245`），不是自由文本散文——`source=llm` 标注无需 NLP，直接枚举映射即可。

> 真正的差距不在"分析能力"，而在"信号契约 + 可视化交付 + 可交互操作"。本期目标是补齐这条链路的第一段。

## 2. 目标与非目标

### 本期目标（L3）
1. 在界面里看到 A股 / 港股通 / 加密永续的 **K 线图 + 成交量副图**（可交互：十字光标 / 缩放）。
2. 把**由确定性指标/量价规则得出的买卖信号**以箭头**标注在 K 线上**，点击可钻取依据。
3. 用日线成交量数据落地一套**量价一致/不一致**信号引擎（量价八法 / OBV 背离 / 放量突破 / Anchored VWAP）。
4. 买卖信号**双轨呈现**：规则信号（实心 ▲/▼，逐 bar 密集）与 LLM 结论（空心 △/▽，**本期仅最新 1 个点**）同时上图，一致合并、冲突并排。
5. 买卖价位（入/损/标）**数值化**并画成价位线（**单一权威来源，见 5.3**）。
6. **可信支撑**：复用现有 `BacktestResult` 给每类信号回填**历史方向命中率**作为 confidence 实证，使 `verified` 不再恒 false。

### 非目标（明确不做，留后续阶段）
- weekly / monthly / 分钟 / 分时多周期（复权对齐 + 历史窗口成本，留 P2）。
- 加密 CVD / 爆仓热力 / 订单簿 / OI 时间序列（需新数据源）。
- A股 L2 逐笔级主力资金（现有为日级聚合）。
- 全市场量价 screener（容器 C，留信号引擎成熟后）。
- 执行桥 / 自动下单（解耦，留 P2 opt-in）。
- B 工作台全屏盯盘页与手绘工具（终态，留后续）。
- **LLM 结论的历史时间序列**（本期仅画最新 1 点，历史 LLM 序列留后续）。

## 3. 已锁定决策

| 决策点 | 结论 |
| --- | --- |
| 容器演进 | A 嵌入式 K 线抽屉 → C 信号仪表盘 → B 个股工作台（本期只做 **A**） |
| 本期范围 | **L3**：图 + 信号箭头 + 规则/LLM 双轨 + 买卖价位线 |
| 冲突策略 | **双轨并列**：规则▲ + LLM△ 同时上图；一致→合并强信号；冲突→并排 + 各自钻取依据；系统不替用户做主 |
| **LLM 双轨范围** | 规则信号逐 bar 密集；**LLM 仅画最新 1 个结论点**，consistency 只在该点邻域计算；早期 bar 无 LLM 显式留白并图例标注 |
| **可信定位** | **复用 `BacktestResult` 回填每类信号历史命中率**作为 confidence 实证（verified 不再恒 false） |
| 图表库 | **klinecharts@^9.8.12**（Apache-2.0；**禁止 `@latest`/`10.x`**，当前 latest=`10.0.0-beta3` 预发布）；vite 单独分包、仅 lazy 重面板内 import |
| 市场范围 | 三市场全做（A股 / 港股通 / 加密永续），统一走 `/history` + `data_provider`，遵守**价格基准契约（见 6）** |
| 涨跌颜色 | 默认中式红涨绿跌，可按市场/用户切换 |
| 前端 K 线/signals client | 放 `apps/dsa-web/src/api/stocks.ts`（个股数据入口）；**不复用 `history.ts`**（它是分析记录域，打 `/api/v1/history`，语义完全不同） |

## 4. 架构与数据流

前端 K 线抽屉并行拉两条数据，**两条必须同源同复权（见 6 价格基准契约）**：

```
[前端 KLineDrawer (klinecharts@^9.8.12, NEW, lazy)]
   │  GET /history (OHLCV)              │  GET /signals (markers + price_lines + consistency + status, NEW)
   ▼                                    ▼
[stocks.py /history 复用·微调]       [stocks.py /signals  NEW]
   │  data_provider 多源 fallback        │  组装（与 /history 共享同一 normalize_ohlcv 后的 bar 序列）：
   ▼                                    │   ① volume_price_signals.py (NEW 引擎, 逐 bar 密集)
[OHLCV]                                 │   ② 复用 _generate_signal → 单个 BuySignal(最新bar) → consistency 的"规则代表方向"
                                        │   ③ 价位反算器(NEW, ATR新增) → 写 result.dashboard.price_position → 现有文案护栏；entry/stop/target 经新校验 → price_lines
                                        │   ④ LLM operation_advice(最新1条 by code) → source=llm 最新1点 + as_of
                                        │   ⑤ BacktestResult 回填各 signal_type 历史命中率 → confidence 实证
                                        ▼
                              [SignalMarker[] + price_lines + consistency + status]
```

**复用为主，新增为辅**：新增 = 量价引擎 + 信号端点 + 价位反算器 + ATR + latest-by-code 查询 + 前端图表组件；其余复用 `data_provider`、`alert_indicators.normalize_ohlcv`、`_generate_signal`、`BacktestResult`。不造平行决策链、不造第二套量能口径。

## 5. 详细设计

### 5.1 后端 · 量价信号引擎（M1）

新文件 `src/services/volume_price_signals.py`，**纯函数**。

**调用契约**：`normalize_ohlcv(df, required_columns=('open','high','low','close','volume'))`——`required_columns` 是 **keyword-only 必填**（`src/services/alert_indicators.py:178`，按 `normalize_ohlcv(df)` 调用会 TypeError）；输出含 `date` 列、已升序、已丢未收盘 bar。

**单一摆动点基元（swing pivot）**：左右各 `k=3~5` 根确认、滞后 `k` 根（天然不含未来函数）。OBV 背离、缩量回调段起止、Upthrust/Spring 高低点**全部复用它**，消除多处 rolling-extremum 口径漂移。

**共用量化基元**（滚动量统一 `shift(1)` 防未来函数）：
- `spread=high-low`；`body=close-open`；`range_pos=(close-low)/spread`（`spread==0` 一字板 → `None`，中性，不兜 eps）
- `vol_ma=volume.rolling(20).mean().shift(1)`；`rel_vol=volume/vol_ma`（`vol_ma<=0|NaN` → `rel_vol=None` 标 `degraded`）
- `pct_chg=close.pct_change()`；`ma5/ma20`
- **鲁棒性**：一字板/涨跌停（`high==low`）单独标记并**排除出八法/VSA 或强降权**；前 N 根不足窗口 → `degraded`；窗口一律按**交易 bar 数**而非自然日。

**A 类（高置信，日线即可，首发）**

1. **量价八法分类器（穷尽且互斥的二维查表，禁止留空）**：
   - 量档（对称无缝）：`low<0.7` / `shrink 0.7~0.8` / `normal 0.8~1.2` / `up 1.2~1.5` / `high>=1.5`
   - 价档：`down (pct_chg<-eps)` / `flat (|pct_chg|<=eps)` / `up (pct_chg>eps)`，`eps` 显式默认 0.4%
   - 量档 × 价档查表，**每格要么给信号要么显式 neutral 兜底**；附完整真值表 + 边界点（0.7/0.8/1.2/1.5、pct=±eps）测试
   - `close vs ma20` 与 `body>0 / range_pos`：作**方向过滤/置信调节**，不作分类前置（解决"高开收阴放量=派发"被误判看多：量增价升须叠加 `body>0 或 range_pos>0.5` 才确认）
   - 量档阈值与既有 `VolumeStatus`（`_analyze_volume`，5日均量、对称 1.5/0.7）对齐；本引擎用 20 日基准的差异在 `.env.example` 暴露可配并注明
2. **OBV + 顶底背离**：OBV 累计（收涨加量/收跌减量）。背离 = 最近两个**已确认 same-type swing pivot** 比较，价创新高/低而 OBV 未跟（只比相对形态，OBV 绝对值不可比）。
3. **放量突破 / 缩量回调**：突破 = `close >= high.rolling(N).max().shift(1)`（**明确不含当日**）且 `rel_vol>=2.0`，N∈{20,60}。缩量回调 = 上升趋势(`ma5>ma20`)中、自最近已确认 swing high 起的回调段，段内 `rel_vol<0.9` 且回撤 < `ATR 倍数`（替代固定 8%，自适应波动率）。
4. **Anchored VWAP**：**仅锚在"已确认的 A.3 放量突破日"**这一因果可定义事件（去掉"止跌高潮日"前视锚）；按典型价 `(H+L+C)/3` 量加权累计；重夺/失守按 edge-cross 当根触发。

**B 类（VSA/Wyckoff，日线近似、低置信）—— 量化降权契约（不只靠"视觉弱化"）**
5. VSA 单 bar：No Demand / No Supply / Stopping-Climactic / Effort-vs-Result。
6. Upthrust（假突破顶，看空）/ Spring（假跌破底，看多）：复用 swing pivot 基元。
- **强制约束**：B 类**不进入 consistency 方向投票、不驱动 price_lines**；置信权重上限 ≤ A 类 1/3；每窗口按强度取 top-k 限流；测试断言"B 类不改变 consistency 结论、不超频"。

**禁止用日线假冒**：CVD / 真日内 VWAP / 吸收 / 盘口失衡 / L2 主力净流入严格依赖逐笔/L2，本期不实现，也不用 OBV 或日线量冒充。

### 5.2 后端 · 信号契约 + 端点（M2a）

**新 schema `SignalMarker`**（追加，不动现有 `SniperPoints`）：

```
SignalMarker {
  timestamp,                   # epoch ms，唯一权威时间锚（前端按 timestamp 匹配蜡烛）
  price, anchor,               # anchor ∈ {low,high,close} 说明 price 锚定语义
  direction: bullish | bearish | neutral,
  signal_type,                 # volume_breakout / obv_top_divergence / vsa_no_demand / rule_score / llm_advice ...
  source: rule | llm,
  confidence: high | medium | low,         # 可排序；B类<=low；数据降权与方法论低置信用独立标志位区分
  is_daily_approx: bool, is_anomalous: bool,
  reason, threshold, observed_value,
  hit_rate, hit_sample,        # 来自 BacktestResult 回填（见 M2 命中率），无样本则 null
  verified: bool,              # hit_sample 达阈值则 true
  as_of                        # 仅 source=llm：该 LLM 结论生成时间（advice_timestamp）
}
```

**`timestamp` 统一基准**：`KLineData.date`（`'YYYY-MM-DD'` 无时区）→ epoch ms 统一按 **Asia/Shanghai**（与前端 `format.ts` 约定一致），三市场统一；前端新增 `KLineData→klinecharts` 纯函数映射适配（`date→epoch ms`、`amount→turnover`、`change_percent` 处理），归 `stocks.ts` util + 三市场日期格式单测。`bar_index` 不入契约（消除双源歧义）。

**新端点 `GET /api/v1/stocks/{code}/signals`**，与 `/history` 同源（共享同一 `normalize_ohlcv` 后的 bar 序列、同 `days`、同丢未收盘规则）：
- 顶层 `status: 'ok' | 'degraded'`；`degraded` 仍返回 **200** + 已能算出的部分（`markers` 可空、`consistency='unknown'`、`degraded_reason`）。
- `price_lines: {entry,stop,target}` 各子字段**允许 null**（任一反算不出为 null，不整体省略，不影响 markers）。
- 硬错误沿用现有 `ErrorResponse`（`api/v1/schemas/common.py`）+ 在 `responses={}` 声明，与 `/history` 一致。
- **数据新鲜度**：声明 `/signals` 为**日线收盘级**（含当日未收盘已丢弃），非盘中实时；日线 history 路径**不走** realtime 600s TTL（该 TTL 仅作用于 `efinance_fetcher.py:138` 的 quote 缓存）。

**`consistency` 单一可执行定义**（解决"三密度+多信号投票"歧义）：
- "规则代表方向" = **收敛后的 `_generate_signal` 单个 `BuySignal`**（最新 bar），**不**让 N 条量价 marker 直接投票。
- 方向映射两张表：`BuySignal(7态) → {bullish/bearish/neutral}`；`operation_advice → {bullish/bearish/neutral}` **复用现有** `normalize_decision_action` / `infer_decision_type_from_advice`（`src/report_language.py:731`），未识别 → `neutral` 且标 `unknown`，不新建平行解析。
- consistency **只在"存在 LLM 点的 bar 邻域"计算**（本期即最新 1 点）；其余 bar 不参与。
- **LLM 陈旧度**：`operation_advice` 取自 `AnalysisHistory` 最新一条（需新增 **latest-by-code 查询**，复用 `ix_analysis_code_time` 索引；现有 `get_latest_analysis_by_query_id` 需 `query_id` 不适用）；超过 N 个交易日则 consistency 标 `stale`/降级 `unknown`。

**`/history` 微调**（`api/v1/endpoints/stocks.py:491`，`days=Query(30, ge=1, le=365)`）：
- 仅改**该端点 Query** 的默认（30→120）与上限（保守起步，如 365→**不激进放满**）；`days` 语义注明为**日历回看天数**（`get_daily_data` 按 `days*2` 估交易日，`base.py:494`）。
- **`365` 在仓库是 3+ 处独立常量**（`stocks.py:491`、`alert_indicators.MAX_REQUESTED_DAYS:24`、`data_tools._DAILY_HISTORY_MAX_DAYS:24`、config_registry、前端 AlertRuleForm）——**绝不"顺手统一"成全局常量**（否则放宽 alert 取数上限、改 agent/config 语义，`tests/test_alert_worker.py` 的 "at most 365 days" 断言回归失败）。
- weekly/monthly 维持现状（`stock_service.py:109` 抛错 → 端点 `unsupported_period`），本期不解锁。

### 5.3 后端 · 价位数值化反算器（M2b）

新增 ATR 计算（经核验 `stock_analyzer/analyzer/alert_indicators` **三处均无**）。反算器基于 `MA5/MA20/近20日高低/ATR` 算 `entry/stop/target` 与风险回报比，**输入统一取自与图上规则信号同源的 `TrendAnalysisResult`/同一 normalize_ohlcv DataFrame**（避免两套 MA/高低口径）。

**与现有护栏的正确关系**（修正 v1 的错误叙述）：
- `stabilize_decision_with_structure(result, trend_result, fundamental_context) → None`（`src/analyzer.py:901`）**不接受价位入参**，它从 `result.dashboard...price_position` 读 support/resistance/current_price，职责是把激进 buy/sell **文案**降级为震荡措辞。正确路径：反算器把 support/resistance/current_price **写入 `result.dashboard.price_position`** 再调用现有函数，而非"把 entry/stop/target 喂进去"。
- `_is_invalid_stop_loss`（`src/analyzer.py:234/301`）是两个函数内**重复定义的内嵌闭包**，外部不可 import，仅判 LLM 报告字段是否占位——**从复用引用移除**，不当作价位护栏。其 :234/:301 重复是可消除技术债（顺手记录，不在本期处理）。
- 若需"反算止损/目标是否合理"的风险校验，明确是**新增校验函数**（非复用），并定义判无效时回退（回退 ATR 派生值 / 隐藏该线）。
- **价位线单一权威来源 = 反算器数值经校验/修正后的值（source=rule）**；LLM `SniperPoints`（`Union[str]` 散文、无时间锚）**本期不画线**，仅在钻取面板以文本展示；**价位线不做双轨**。`AnalysisHistory` 已有 `ideal_buy/secondary_buy/stop_loss/take_profit` 为 `Column(Float)`（`src/storage.py:255-258`），可作反算器对照来源。

### 5.3b 后端 · 信号命中率回填（M2c，"可信"实证）

复用现有 `BacktestResult`（`src/storage.py:289+`，已含 `direction_correct/outcome/hit_take_profit/first_hit`）。对每个 signal_type 的历史触发，复用 `BacktestEngine` 的前向结果评估基元（前向 N bar 看方向是否正确），聚合出**每类信号的历史方向命中率 + 样本数** → 回填 `SignalMarker.hit_rate/hit_sample`，`hit_sample` 达阈值则 `verified=true`。

> 说明：这是把"信号-回测同源"（原阶段4）的一个**最小切片**前移以兑现"可信"诉求；不做全量滚动回测。命中率为**历史统计、非未来保证**，前端须如实标注。

### 5.4 前端 · klinecharts 抽屉（M0 渲染 → M2d 标注）

- **依赖**：`klinecharts@^9.8.12` 写入 `package.json`（精确 caret，禁 `@latest`/`10.x`）；`vite.config.ts` 分包 `'klinecharts':'vendor-klinecharts'`（与 recharts `vendor-charts` 区分）；**仅 lazy 重面板内 import，禁止任何同步路径 import**，保证 M0 不影响首屏。
- **组件**：新建 `KLineDrawer.tsx`（**不要扩展同名 `StockHistoryTrendDrawer`**——那是历史分析记录表格）；复用 `common/Drawer`、仿 `ReportMarkdownDrawer` 范式（Drawer 壳 + lazy 重面板 + ErrorBoundary + chunk 失败兜底，对应"`/signals` 失败不影响出图"降级）。对外契约 `{stockCode, stockName?, market?, isOpen, onClose}`。
- **类型**：新建 `src/types/kline.ts`（`KLine` / `SignalMarker`），**不堆进已 572 行的 `analysis.ts`**。
- **M0**：蜡烛 + 成交量副图（红涨绿跌可切）+ 十字光标 / 缩放；`stocks.ts` 新增 `getKlineHistory`/`getSignals` client + 映射适配 util。**入口先接 `HomePage` StockBar 一个最小入口**（`stockCode` 现成），其余入口（ScreeningPage/HistoryList）留后续增量。
- **M2d**：双轨标注 ▲/△ + 合并/并排 + 点击钻取 + 价位线——klinecharts 9.8 **无内置模板**（仅 priceLine/simpleAnnotation/segment），须 `registerOverlay` **自绘 glyph + onClick 桥接** React 钻取面板（**显式列为 M2d 主要工作量、单独估时**）；B 类视觉弱化、hit_rate 展示也在此层；价位线用内置 `priceLine`。

## 6. 价格基准契约与稳定性护栏

- **价格基准契约（新增，核心）**：`/signals` 与 `/history` 必须来自**同一次拉取（同源、同复权）**，不允许 `/history` 用源A 而 `/signals` 重拉源B。量价引擎以**前复权连续序列**为输入前提；数据为未复权/跨源拼接 → 返回 `degraded` 而非出 marker。复权方式各源不一（A股 qfq / yfinance auto_adjust / longbridge history=ForwardAdjust 但实时=NoAdjust），且 `DataFetcherManager` 跨源 fallback。
- 除权日 / 停牌 / 涨跌停一字板列为**已知假信号源**，强制降权或剔除（一字板 `rel_vol` 极低会被误判"量减价升"，语义相反）。
- 加密 7×24 的 `vol_ma(20)` 与 A/HK 日线 20 **不可跨市场直接比较**，在 confidence/降权体现。
- 全部**追加字段 / 新端点**，保留旧 `sniper_points` 与 `/history` 现有行为。
- `/signals` 失败不影响 `/history` 出图（前端降级：有图无标注）；单数据源失败走现有 fallback；单标的信号失败不拖垮抽屉。
- 港股通暂按通用 HK 处理（"是否港股通标的"标签留作小增强）。

## 7. 测试策略

- **后端**：
  - 八法**完整真值表 + 边界点**（0.7/0.8/1.2/1.5、pct=±eps、close=ma20）测试，断言无死区。
  - 未来函数反例：`价格每日微创新高`不误触 OBV 背离；`事后才知是底`不被 AnchoredVWAP 提前锚定；放量突破 `shift(1)` 不含当日。
  - 鲁棒性：`vol_ma<=0/NaN`→degraded；一字板排除；窗口不足→degraded。
  - B 类断言：不改变 consistency、不超频、不驱动 price_lines。
  - 价位反算器：**确实把 support/resistance 写进 `result.dashboard.price_position` 并经现有文案护栏**（非绕过、非另起链）；新校验函数回退路径。
  - 契约：`/signals` 的 `ok/degraded` 形状、`consistency` 三态 + `stale/unknown`、旧 schema 向后兼容；`pytest -m "not network"` + `./scripts/ci_gate.sh`。
- **前端**：抽屉渲染（双轨 overlay、价位线、降级"有图无标注"）组件测试 + `KLineData→klinecharts` 三市场日期映射单测 + 类型契约；`npm run lint && npm run build`（在 `/tmp` 无空格副本跑，避开工作区路径空格坑）。
- 文档：同步 `docs/CHANGELOG.md`（`[Unreleased]` 扁平格式）及受影响 `docs/*.md`、`.env.example`（量价阈值/eps/swing k/陈旧阈值等可配项）。

## 8. 交付切分（各里程碑可独立合入/回滚）

| 里程碑 | 内容 | 对应路线图 |
| --- | --- | --- |
| **M0** | `KLineDrawer` + 蜡烛 + 量副图 + 十字光标/缩放；`stocks.ts` history client + 映射适配 + `types/kline.ts`；`/history` 放宽 days（仅端点 Query）；接 StockBar 入口；klinecharts 分包 | 阶段0 |
| **M1** | `volume_price_signals.py`（swing pivot 基元 + A 类八法查表/OBV/突破/AVWAP + B 类降权契约）+ 真值表/未来函数/鲁棒性单测 | 阶段1 |
| **M2a** | `SignalMarker` schema + `/signals` 端点（rule markers + consistency 单一定义 + LLM 最新1点 + status/degraded 契约 + latest-by-code 查询） | 阶段2 |
| **M2b** | ATR + 价位反算器 + 写 `price_position` 挂现有护栏 + price_lines（重点测"喂进护栏非绕过"） | 阶段2 |
| **M2c** | 信号命中率回填（复用 `BacktestResult`/前向评估 → hit_rate/verified） | 阶段2（前移阶段4切片） |
| **M2d** | 前端双轨标注（registerOverlay 自绘）+ 钻取 + 价位线 + hit_rate 展示 + B 类弱化 | 阶段2 |

## 9. 风险与回滚

- **风险**：klinecharts 与现有 vite/recharts 共存（已分包+lazy 缓解）；三市场复权/交易日历/停牌差异（已立价格基准契约+degraded）；B 类日线近似假信号（已量化降权契约）；价位反算器与文案护栏协同（测试确认走 `price_position`）；命中率回填依赖回测前向评估切片（限定最小切片，非全量）；`registerOverlay` 自绘交互工作量（已单列 M2d 估时）。
- **回滚**：M0~M2d 各为独立增量；前端为新增组件/路由，移除即恢复；`/signals`、`volume_price_signals`、ATR、latest-by-code 查询均为新增，删除不影响主流程；`/history` days 仅端点 Query 默认/上限变更，改回即可（未动其它 3 处 365 常量）。

## 10. 复用引用（审计核验于 2026-06-15，实现时以现状为准）

- `GET /history`：`api/v1/endpoints/stocks.py:478-543`，`days=Query(30,ge=1,le=365)`@:491；`KLineData`：`api/v1/schemas/stocks.py:51`；非 daily 抛错：`src/services/stock_service.py:106-112`。
- 归一化：`src/services/alert_indicators.py:178`（`normalize_ohlcv`，`required_columns` keyword-only 必填）/`:36`（`IndicatorEvaluation`）/`:24`（`MAX_REQUESTED_DAYS=365`，**不动**）。
- 规则信号：`src/stock_analyzer.py:584`（`_generate_signal`，仅评最新 bar、产单个 `BuySignal`，需先 `analyze(df,code)`）；方向规范化复用 `src/report_language.py:731`（`infer_decision_type_from_advice`）。
- 文案护栏：`src/analyzer.py:901`（`stabilize_decision_with_structure`，签名不收价位、读 `price_position`、原地改 result）。
- **不可复用**：`src/analyzer.py:234/301`（`_is_invalid_stop_loss`，内嵌闭包，仅 LLM 字段占位校验，已从价位护栏移除）。
- 价位/筹码：`src/schemas/report_schema.py:91`（`SniperPoints`，`Union[str]`）；`src/storage.py:255-258`（`AnalysisHistory.ideal_buy/...` Float）；`:245`（`operation_advice` String(20) 枚举）；`ix_analysis_code_time` 索引。
- 命中率：`src/storage.py:289+`（`BacktestResult`，含 `direction_correct/outcome/hit_take_profit/first_hit`）。
- 量能口径：`src/stock_analyzer.py:_analyze_volume`（5日均量、对称 1.5/0.7，`VolumeStatus`）——本引擎对齐或显式说明差异。
- ATR：`stock_analyzer/analyzer/alert_indicators` **三处均无**，本期新增。
- 前端：`apps/dsa-web/src/api/stocks.ts`（仅 extract/parse）；`api/history.ts`（分析记录域，**不复用**）；`types/analysis.ts`（572 行，**不堆**）；同名 `StockHistoryTrendDrawer`（历史表格，**别撞名**）；klinecharts 未引入、recharts `^3.3.0`；`vite.config.ts vendorChunkByPackage`；`format.ts`（Asia/Shanghai）。

## 11. 开放问题（多数已在 v2 锁定，余下细项）

1. `/history` days 上限保守值：建议默认 120、上限起步不激进放满 365 以上（需先评 `data_provider` 各源长窗口回溯深度/超时，longbridge by_offset 取数较短）。
2. 港股通"是否可交易标的"标签是否纳入本期（建议作为小增强，不阻塞）。
3. 命中率回填的样本阈值（`hit_sample` 多少才置 `verified=true`）与 N bar 前向窗口，建议沿用回测既有默认（`eval_window_days`）。

## 12. 专家审查处置（needs-revision → v2）

| 审查 blocking | 处置 |
| --- | --- |
| 双轨时间密度三分裂 + consistency 无定义 | 5.2：consistency 单一来源=`BuySignal`、两张映射表复用现有规范化、仅 LLM 点邻域计算、LLM 最新1点 |
| 价位线来源未裁定 + 喂不进护栏 | 5.3：单一权威=反算器数值；正确路径写 `price_position` 调现有函数；新校验函数显式标新增 |
| `_is_invalid_stop_loss` 误当护栏 | 5.3/10：从复用移除，标内嵌闭包不可复用 |
| 八法死区 + 阈值不自洽 | 5.1：穷尽互斥二维查表 + 真值表测试 + 对齐 VolumeStatus |
| OBV/AVWAP/突破未来函数 | 5.1：单一 swing pivot 基元 + `shift(1)` 突破 + AVWAP 仅锚突破日 + 反例测试 |
| 三市场复权漂移 | 6：价格基准契约（同源同复权/degraded/交易bar窗口/除权停牌一字板降权） |
