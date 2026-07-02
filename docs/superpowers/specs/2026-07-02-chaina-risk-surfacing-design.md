# 链路A 风险画像前端 surfacing(Inc 1b)设计

## 0. 定位与背景

**目标**:让 Inc 1a(merge `97b06743`)落库的链路A 风险画像 `diagnostics.risk_metrics`(不年化 Sharpe/Sortino/事件净值 maxDD/worst_single)从"数据沉睡"变为**用户可见**——在 Web 回测页绩效卡渲染,maxDD 必须带防误读说明(信号流事件序列口径,非组合回撤)。

这是 [[dsa-actionable-signal-strategy]] Inc1「回测严谨性=命门」的第三个增量(1a 风险指标落库、1c 多重检验校正已交付)。

**⚠️ 用户暂离期间的范围拍板(spec 审阅门可翻)**:brainstorming 范围问题超时未答,按推荐默认取 **1b = 链路A surfacing only**;链路B 风险指标经探索证实为**数据管道缺口**(`SignalOutcome` 仅 win/loss/expired 分类、`classify_triple_barrier` 返回裸字符串、无任何收益幅度产生或落库,`src/services/signal_backtest.py:39-50,53-84`)——要算 Sharpe 须先设计三重门收益口径(触障价 vs 收盘价)+落库 schema,量级远超 surfacing,拆为独立后续增量。

## 1. 现状(基于真实代码,file:line,已核验)

**数据链其实已全通到浏览器,只是无人渲染**:

- 写:`backtest_engine.py:435` `diagnostics["risk_metrics"] = cls._compute_risk_metrics(completed)`;键集(`:768-825`):`sample, mean_return_pct, return_std_pct, sharpe, sortino, max_drawdown_pct, equity_final_pct, worst_single_return_pct, note`(n=0 时数值全 None、note 恒在;各值 round4 或 None,绝不 inf/NaN)。
- 落库:`backtest_service.py:875` 整包 `json.dumps` 进 `backtest_summaries.diagnostics_json`(`storage.py:401-403`);读回解析:`backtest_service.py:963`(唯一解析点)。
- API:`PerformanceMetrics.diagnostics: Dict[str, Any]`(`api/v1/schemas/backtest.py:107-108`)**已整包透出**——`GET /api/v1/backtest/performance[/{code}]` 两端点(`endpoints/backtest.py:156,210`)。
- 前端:`backtest.ts:91,122` 经 `toCamelCase`(= `camelcase-keys deep:true`,`api/utils.ts:12`)**深转所有嵌套键** → 浏览器里是 `metrics.diagnostics?.riskMetrics` 内 `sharpe/sortino/maxDrawdownPct/worstSingleReturnPct/equityFinalPct/meanReturnPct/returnStdPct/sample/note`(deep 转换有 `phaseBreakdown` 先例佐证:后端 `phase_breakdown` @`backtest_service.py:584` → 前端 `diagnostics?.phaseBreakdown` @`BacktestPage.tsx:200`)。
- 渲染:`BacktestPage.tsx` `PerformanceCard`(`:216-253`)只渲染 headline 字段;diagnostics 唯一消费 = `phaseBreakdownText`(`:199-212`,运行时守卫模式);**risk_metrics 到了浏览器却零渲染**。
- 类型:`types/backtest.ts:108` `diagnostics: Record<string, unknown>`(untyped bag,phaseBreakdown 先例=页内窄化不动全局类型)。
- 其余消费面(探索已核):agent tools 显式丢弃 diagnostics;日报/通知**零**回测内容(grep 模板+renderer+notification 零命中);CLI `--backtest` 仅打运行计数(`main.py:945-960`)。

## 2. 非目标(1b 范围外,明确不做)

- **链路B 风险指标**(数据缺口非 surfacing 缺口,见 §0;独立增量,须先设计三重门收益口径)。
- **API 一等字段化**(`PerformanceMetrics` 加 typed risk 字段):diagnostics 已透出可用,重复数据,YAGNI。
- **CLI 摘要打印**(`--backtest` 后 log 风险指标):用户面在 Web,低价值。
- **日报/通知加回测内容**:现状零回测内容,拉入属产品决策,另议。
- **后端任何改动**:零(引擎/service/schema/API 全不动)。
- **不动 `types/backtest.ts` 的 `diagnostics: Record<string, unknown>`**(遵 phaseBreakdown 页内窄化先例)。

## 3. 设计决策

| # | 决策 | 取值 |
|---|---|---|
| D1 | 方案 | **纯前端渲染**(方案 A):数据已在浏览器,只加渲染层;零后端风险、最小 diff |
| D2 | 渲染位置 | `PerformanceCard` 尾部新段(仿 `phaseText` 的 `border-t` 分隔模式)→ **总体与个股两卡自动获得**(组件复用) |
| D3 | 防误读 note | **渲染后端 `note` 字段**(单一真源,随后端口径演进;Inc 1a 起恒写,含"信号流非组合回撤/单笔-100饱和"文案) |
| D4 | legacy 兼容 | `riskMetrics` 缺失(Inc 1a 前的历史 summary 行)或 `sample===0`(无非 cash 完成样本)→ **整段不渲染**(与现状视觉一致,避免一排 '--' 噪音);个别字段 null(如 n=1 时 sharpe)→ 该行 `'--'` |

## 4. 方案

### 4.1 提取函数(仿 `phaseBreakdownText` 模式)

`BacktestPage.tsx` 新增页内窄接口与守卫式提取函数(不动全局类型):

```typescript
interface RiskMetricsView {
  sharpe: number | null;
  sortino: number | null;
  maxDrawdownPct: number | null;
  worstSingleReturnPct: number | null;
  sample: number;
  note: string | null;
}

function riskMetricsView(metrics: PerformanceMetrics): RiskMetricsView | null {
  const rm = metrics.diagnostics?.riskMetrics;
  if (!rm || typeof rm !== 'object') return null;          // legacy 行:无该键 → 整段不渲染
  const item = rm as Record<string, unknown>;
  const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
  const sample = num(item.sample) ?? 0;
  if (sample === 0) return null;                            // 无非 cash 完成样本 → 不渲染
  return {
    sharpe: num(item.sharpe),
    sortino: num(item.sortino),
    maxDrawdownPct: num(item.maxDrawdownPct),
    worstSingleReturnPct: num(item.worstSingleReturnPct),
    sample,
    note: typeof item.note === 'string' ? item.note : null,
  };
}
```

要点:
- 键名是 **camelCase**(`maxDrawdownPct` 非 `max_drawdown_pct`)——`toCamelCase deep:true` 深转,phaseBreakdown 先例佐证(§1)。
- `num` 守卫兼防 `null`(后端 None→JSON null)与意外字符串;`Number.isFinite` 防御(后端已保证不产 inf/NaN,双保险)。
- `mean_return_pct/return_std_pct/equity_final_pct` **不渲染**(卡片已有"平均模拟收益";equity_final 与 maxDD 同源冗余;克制噪音)——但守卫函数不禁止未来扩展。

### 4.2 渲染段(`PerformanceCard` 尾部,`phaseText` 块之后)

```tsx
{risk ? (
  <div className="mt-3 border-t border-white/10 pt-2" data-testid="risk-metrics-section">
    <div className="mb-1 text-xs text-muted-text">风险画像(信号流,{risk.sample} 笔)</div>
    <MetricRow label="Sharpe(不年化)" value={risk.sharpe != null ? risk.sharpe.toFixed(2) : '--'} />
    <MetricRow label="Sortino(不年化)" value={risk.sortino != null ? risk.sortino.toFixed(2) : '--'} />
    <MetricRow label="最大回撤" value={pct(risk.maxDrawdownPct)} />
    <MetricRow label="最差单笔" value={pct(risk.worstSingleReturnPct)} />
    {risk.note ? (
      <div className="mt-1 text-xs text-muted-text" data-testid="risk-metrics-note">{risk.note}</div>
    ) : null}
  </div>
) : null}
```

要点:
- `const risk = riskMetricsView(metrics);` 在 `PerformanceCard` 组件体内与 `phaseText` 并列。
- 复用既有 `MetricRow`(`:192-197`)与既有 `border-t` 小节样式——零新样式类。
- **note 必渲染**(D3):后端 note 即防误读说明;标题行"(信号流,N 笔)"再加一层口径提示。
- **百分比格式已拍板(对抗审查 F1,原条件句歧义已消)**:最大回撤/最差单笔两行用页内既有 `pct()` helper(**已核 `BacktestPage.tsx:40-43` = `value == null ? '--' : value.toFixed(1)+'%'`,一位小数、null 内建 '--'**)——与卡内全部既有百分比行(`:223-228` 均经 pct(),全文件零处 toFixed(2))一致,一致性优先于精度;Sharpe/Sortino 是比率无 `%` 后缀,`pct()` 不适用,维持 `toFixed(2)`(一位小数会损失比率分辨率)。§7 测试锚值随之为一位小数(如 maxDD `12.3%`——后端 maxDD 为正数幅度)。
- **分隔线预期声明(对抗审查 F3)**:「最差单笔」行因后随 note div 非 `:last-child`(`.backtest-metric-row:last-child` 豁免 @`index.css:2623-2625` 不生效),会保留底部分隔线——与主卡各行观感一致(主卡末行同样后随 footer div,该豁免今天也从不生效),属预期渲染,实现/review 不必当 bug 修。

### 4.3 触达面

`PerformanceCard` 被总体与个股两处调用 → 两卡自动获得风险段;date-filtered 动态路径**已核(对抗审查确认)**:`backtest_service.py:1049` 经 `compute_summary`(engine:435 恒写 `risk_metrics`),`:1056-1063` 仅向 diagnostics **追加** phase 键不覆盖 → 动态汇总同样带风险段。

## 5. 兼容性

- **零后端/schema/API/存储改动**;纯 `apps/dsa-web` 单文件(+测试)改动。
- **legacy 行**(Inc 1a 之前生成的 summary,diagnostics 无 risk_metrics)→ `riskMetricsView` 返 null → 整段不渲染,与升级前视觉逐像素一致。
- **刷新动力学(对抗审查 F5 精确化)**:每日自动回测(`main.py:690-703`,`backtest_enabled` 默认 true,force=False、固定 v1@1d@config 窗)当日只要有 ≥1 条新成熟结果就会重算**总体** summary → 总体卡通常**次日自动**获得风险段;但**个股** summary 仅重算当次 touched_codes——不再被分析的股票的 legacy 个股行**不会**经调度刷新;`v1-x{L}` 杠杆与 `v1-5m` 等盘中命名空间的 legacy 行同样不被每日调度触及——这些须手动按对应 interval/leverage 重跑 `--backtest --backtest-force` 才出现风险段。优雅缺省保证任何未刷新行为不渲染而非错误。
- **sample=0 / 字段 None**:§3 D4。
- **旧后端 + 新前端**(仅前端部署):同 legacy 行为,优雅缺省。

## 6. 诚实边界

- note 单一真源在后端 `_RISK_NOTE`(随口径演进,前端不复制文案);标题行"信号流"字样为前端第二层提示。
- 本增量**不改任何统计口径**——纯渲染已有数据。
- 指标为**不年化**、**信号流事件序列**口径:maxDD 是按 analysis_date 排序的事件净值峰谷,**不是**真实组合回撤(用户未必每笔等权满仓);单笔 ≤-100% 饱和。以上语义已在后端 note 与 Inc 1a spec,前端如实透传。

## 7. 测试(vitest,`BacktestPage.test.tsx` 新增独立 it)

**前置警示(对抗审查已定位根因)**:该文件现有 **3 个 pre-existing 失败** = `interval:'1d'` 调用参数漂移(`BacktestPage.test.tsx:210/248/276` 的 `toHaveBeenLastCalledWith` 缺 `interval` 键,而 `intervalFilter` 默认 `'1d'` 经 `BacktestPage.tsx:323/:355/:365` 注入)——新增测试必须全绿,**不修不碰**那 3 个(超范围,交付说明单列);**验收判别式:新增测试后失败集应保持恰好这 3 个(`3 failed | 11+N passed`)**。断言用新 `data-testid`(`risk-metrics-section`/`risk-metrics-note`)。

**mock 层已落定(对抗审查 F1':原 snake_case 主线与事实相反,删除)**:该文件 `vi.mock('../../api/backtest')` 整模块替换 `backtestApi`(`:17-24`),fixture 是**已转换域对象——一律写 camelCase 键**:`diagnostics: { riskMetrics: { sample, sharpe, sortino, maxDrawdownPct, worstSingleReturnPct, note, … } }`。**披露**:risk_metrics 载荷的 snake→camel 深转换因此不在本增量测试覆盖内——契约依赖 `phaseBreakdown` 生产先例(`BacktestPage.tsx:200`)+ `camelcase-keys@10.0.2 deep:true` 审查期人工实证(risk_metrics→riskMetrics 等 7 键全部按预期转换、note 字符串值不受影响、null 透传)。

**fixture 硬规则(对抗审查 F3'):**(a)**禁止改动共享 `basePerformance`**(`:26-49`,其 `diagnostics: {}` 被全部 14 个既有 it 经 beforeEach 共享)——每个新测试内用 `mockGetOverallPerformance.mockResolvedValue({ ...basePerformance, diagnostics: { riskMetrics: {...} } })` per-test 覆盖;(b)数值断言一律 `within(screen.getByTestId('risk-metrics-section'))` 圈定,防与卡内其他数值撞串(pct() 一位小数格式与既有行同型,裸 getByText 有多匹配隐患);(c)默认仅渲染一张总体卡——若测试同时给个股 performance 喂带 `sample>0` 的 riskMetrics,两卡各出一个 section,须 `getAllByTestId` 或 `within(个股卡)`。

1. **完整渲染**:per-test 覆盖 `diagnostics.riskMetrics` 全字段(camelCase)→ within(section) 断言 4 行数值(锚一位小数如 maxDD `12.3%`(正数幅度)、Sharpe `0.85`)、标题含样本数、note 文本可见。
2. **legacy 不渲染**:diagnostics 无 `riskMetrics` 键(即共享 basePerformance 原样)→ `queryByTestId('risk-metrics-section')` 为 null(缺席断言先例:`SignalBoard.test.tsx:86-87`)。
3. **sample=0 不渲染**:`riskMetrics.sample=0`(数值全 null)→ 同上不渲染。
4. **字段级 null 降级**:`sharpe=null` 其余有值 → Sharpe 行 `'--'`,其余行正常数值(within 圈定)。
5. **web-gate**:`npm run lint && npm run build` 全绿(改动纯前端,后端 ci_gate 不触发;CHANGELOG 属 docs)。

## 8. 交付结构

改了什么(纯前端:BacktestPage 风险段 + 测试)/ 为什么(Inc 1a 数据沉睡→可见,命门增量的 surfacing 闭环)/ 验证(4 vitest + web-gate;3 pre-existing 失败单列不碰)/ 未验证项(真实后端端到端可选:跑一次 `--backtest` 开页面目验)/ 风险(极低:单文件渲染层,legacy 整段缺省)/ 回滚(单 commit revert)。

CHANGELOG `[改进]` 扁平一条(让已落库的风险画像在回测页可见,含防误读说明)。
