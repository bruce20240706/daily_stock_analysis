# 链路A 风险画像前端 surfacing(Inc 1b)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Web 回测页绩效卡渲染 Inc 1a 已落库并已到达浏览器的链路A 风险画像(`diagnostics.riskMetrics`),含防误读 note。

**Architecture:** 纯前端单文件改动:`BacktestPage.tsx` 加页内窄化提取函数 `riskMetricsView`(仿既有 `phaseBreakdownText` 守卫模式)+ `PerformanceCard` 尾部渲染段。零后端/schema/API 改动——数据链已全通(engine→diagnostics_json→PerformanceMetrics.diagnostics→toCamelCase deep→浏览器)。

**Tech Stack:** React + TypeScript + vitest + @testing-library/react(全部既有,无新依赖)。

**Spec:** `docs/superpowers/specs/2026-07-02-chaina-risk-surfacing-design.md`(经对抗审查 9 发现收敛定稿)。

## Global Constraints

- **工作区**:主仓路径含空格,执行前建无空格 worktree `/root/chaina-risk-surfacing`(`git worktree add /root/chaina-risk-surfacing -b feature/chaina-risk-surfacing main`);前端命令 `cd /root/chaina-risk-surfacing/apps/dsa-web`,node_modules 不随 worktree,先 `npm ci`。
- **commit**:英文类型前缀 + 中文描述体,**不加 Co-Authored-By**。
- **camelCase 键**:浏览器侧是 `metrics.diagnostics?.riskMetrics` 内 `sharpe/sortino/maxDrawdownPct/worstSingleReturnPct/sample/note`(`toCamelCase` = camelcase-keys deep:true 深转,审查期已人工实证)。**测试 fixture 一律 camelCase**(该测试文件 mock 的是 `backtestApi` 已转换层)。
- **格式拍板(spec §4.2)**:最大回撤/最差单笔用页内既有 `pct()`(=`toFixed(1)+'%'`,null→`'--'`,`BacktestPage.tsx:40-43`);Sharpe/Sortino 用 `toFixed(2)`(比率无 % 后缀)。
- **fixture 硬规则(spec §7)**:禁改共享 `basePerformance`;per-test spread 覆盖;数值断言 `within(getByTestId('risk-metrics-section'))` 圈定。
- **pre-existing 失败不碰**:`BacktestPage.test.tsx` 现有恰好 3 个失败(`:210/248/276` 的 `toHaveBeenLastCalledWith` 缺 `interval` 键)——**验收判别式:新增测试后 `3 failed | 15 passed`**(11 既有过 + 4 新)。
- 不 push、不 tag;合并方式待用户确认。

---

### Task 1: PerformanceCard 风险画像段 + 4 测试 + CHANGELOG

**Files:**
- Modify: `apps/dsa-web/src/pages/BacktestPage.tsx`(`phaseBreakdownText` :199-212 之后加提取函数;`PerformanceCard` :216-253 内加渲染段)
- Test: `apps/dsa-web/src/pages/__tests__/BacktestPage.test.tsx`(文件末尾 describe 内追加 4 个 it)
- Modify: `docs/CHANGELOG.md`(`[Unreleased]` 顶部加一行)

**Interfaces:**
- Consumes: `PerformanceMetrics.diagnostics: Record<string, unknown>`(既有,`types/backtest.ts:108`);既有 `pct()`(`BacktestPage.tsx:40-43`)与 `MetricRow`(`:192-197`,props `{label, value, accent?}`)。
- Produces: 页内 `riskMetricsView(metrics: PerformanceMetrics): RiskMetricsView | null` + `data-testid="risk-metrics-section"` / `"risk-metrics-note"`(无跨任务消费者——单任务计划)。

- [ ] **Step 1: 写 4 个失败测试**(`BacktestPage.test.tsx` describe `'BacktestPage'` 内、文件末尾追加;`within` 已在 `:1` import)

```typescript
  // ===== Inc 1b: 风险画像段(spec §7;fixture 一律 camelCase——mock 的是 backtestApi 已转换层)=====

  const riskMetricsFixture = {
    sample: 12,
    meanReturnPct: 1.83,
    returnStdPct: 4.51,
    sharpe: 0.8523,
    sortino: 1.2371,
    maxDrawdownPct: 12.34,
    worstSingleReturnPct: -8.6,
    note: '风险画像基于信号流事件序列,非真实组合回撤;单笔收益下钳 -100%',
  };

  it('renders risk metrics section with values and note when riskMetrics present', async () => {
    mockGetOverallPerformance.mockResolvedValue({
      ...basePerformance,
      diagnostics: { riskMetrics: riskMetricsFixture },
    });
    render(<BacktestPage />);
    const section = await waitFor(() => screen.getByTestId('risk-metrics-section'));
    const s = within(section);
    expect(s.getByText('风险画像(信号流,12 笔)')).toBeInTheDocument();
    expect(s.getByText('0.85')).toBeInTheDocument();       // sharpe toFixed(2)
    expect(s.getByText('1.24')).toBeInTheDocument();       // sortino toFixed(2)
    expect(s.getByText('12.3%')).toBeInTheDocument();      // maxDD 经 pct() 一位小数
    expect(s.getByText('-8.6%')).toBeInTheDocument();      // worst 经 pct()
    expect(s.getByTestId('risk-metrics-note')).toHaveTextContent(
      '风险画像基于信号流事件序列,非真实组合回撤;单笔收益下钳 -100%',
    );
  });

  it('renders no risk section for legacy rows without riskMetrics key', async () => {
    // 共享 basePerformance 原样(diagnostics: {})——缺席断言先例:SignalBoard.test.tsx:86-87
    render(<BacktestPage />);
    await waitFor(() => screen.getByText('方向准确率'));
    expect(screen.queryByTestId('risk-metrics-section')).toBeNull();
  });

  it('renders no risk section when sample is zero', async () => {
    mockGetOverallPerformance.mockResolvedValue({
      ...basePerformance,
      diagnostics: {
        riskMetrics: {
          sample: 0, meanReturnPct: null, returnStdPct: null, sharpe: null,
          sortino: null, maxDrawdownPct: null, worstSingleReturnPct: null,
          note: '风险画像基于信号流事件序列,非真实组合回撤;单笔收益下钳 -100%',
        },
      },
    });
    render(<BacktestPage />);
    await waitFor(() => screen.getByText('方向准确率'));
    expect(screen.queryByTestId('risk-metrics-section')).toBeNull();
  });

  it('degrades a null field to -- while other rows keep values', async () => {
    mockGetOverallPerformance.mockResolvedValue({
      ...basePerformance,
      diagnostics: { riskMetrics: { ...riskMetricsFixture, sharpe: null } },
    });
    render(<BacktestPage />);
    const section = await waitFor(() => screen.getByTestId('risk-metrics-section'));
    const s = within(section);
    expect(s.getByText('--')).toBeInTheDocument();          // 仅 Sharpe 行降级,section 内唯一 '--'
    expect(s.getByText('1.24')).toBeInTheDocument();
    expect(s.getByText('12.3%')).toBeInTheDocument();
  });
```

注意:`render(<BacktestPage />)` 与 `await waitFor(...)` 的调用形态照该文件既有 it(如 `:112`)——若既有 it 用 router 包裹或其他 helper,一并照抄。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/chaina-risk-surfacing/apps/dsa-web && npx vitest run src/pages/__tests__/BacktestPage.test.tsx`
Expected: 新 4 个 it FAIL(`Unable to find … risk-metrics-section` / legacy 两个可能误过?——不会:#2/#3 是缺席断言,在实现前**天然通过**;#1/#4 必红)。RED 判据 = `5 failed | 13 passed`(3 pre-existing + 新 #1/#4)。

- [ ] **Step 3: 实现**(`BacktestPage.tsx`)

`phaseBreakdownText`(`:199-212`)之后追加(页内窄化,不动 `types/backtest.ts`):

```typescript
// ============ Risk Metrics(Inc 1b:渲染链路A 已落库的风险画像)============

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

`PerformanceCard`(`:216-253`)内:`const phaseText = phaseBreakdownText(metrics);` 之后加一行 `const risk = riskMetricsView(metrics);`;`{phaseText ? (...) : null}` 块**之后**(`</Card>` 前)追加:

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

预期渲染声明(spec §4.2,勿当 bug 修):「最差单笔」行因后随 note div 非 `:last-child`,保留底部分隔线——与主卡各行观感一致。

- [ ] **Step 4: 跑测试确认通过 + 全量 web-gate**

Run: `npx vitest run src/pages/__tests__/BacktestPage.test.tsx`
Expected: **`3 failed | 15 passed`**(恰好保持既有 3 个 pre-existing 失败 `:210/248/276`,新 4 个全绿——验收判别式)。

Run: `npm run lint && npm run build`
Expected: lint 0 error,build 成功。

- [ ] **Step 5: CHANGELOG**(`docs/CHANGELOG.md` `[Unreleased]` 顶部追加一行,扁平格式禁 `###` 标题)

```markdown
- [改进] 回测页绩效卡新增"风险画像"段:渲染链路A 已落库的不年化 Sharpe/Sortino/最大回撤/最差单笔(信号流事件序列口径,含防误读说明;历史未刷新统计不显示,重跑回测后出现)
```

- [ ] **Step 6: Commit**

```bash
cd /root/chaina-risk-surfacing
git add apps/dsa-web/src/pages/BacktestPage.tsx apps/dsa-web/src/pages/__tests__/BacktestPage.test.tsx docs/CHANGELOG.md
git commit -m "feat: 回测页绩效卡渲染链路A 风险画像段(不年化 Sharpe/Sortino/最大回撤/最差单笔+防误读 note,legacy 行与 sample=0 整段缺省,纯前端零后端改动)"
```

---

## Self-Review 结论(plan 作者已核)

- **Spec 覆盖**:§4.1 提取函数/§4.2 渲染段(含 pct 拍板+分隔线声明)/§5 legacy 缺省/§7 测试 1-4 + web-gate + fixture 硬规则/§8 CHANGELOG——全部落在 Task 1。§4.3 触达面(两卡自动获得/动态路径)是既有代码性质,无需实现动作。
- **Placeholder 扫描**:无 TBD;测试与实现代码完整;render 形态注明照抄既有 it(实现者可见文件)。
- **类型一致性**:`riskMetricsView`/`RiskMetricsView`/testid 三处命名一致;fixture 键与 §4.1 读取键一致(camelCase)。
- **RED 判据显式**(`5 failed | 13 passed`)与 GREEN 验收判别式(`3 failed | 15 passed`)均锚定,防 pre-existing 失败混淆。
