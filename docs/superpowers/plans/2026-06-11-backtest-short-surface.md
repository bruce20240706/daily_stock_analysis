# 回测做空感知 Web 透出 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 回测结果表新增「仓位/模拟」列，让子项目 E 的做空仓位、资金费折算后模拟收益与出场原因首次用户可见。

**Architecture:** 纯 Web 渲染层（方案 A）：`BacktestPage.tsx` 加两个标签常量 + 一个 badge 助手 + 一列双行单元格（badge+收益 / 出场原因小字），presence-only（`!row.positionRecommendation` 整列 `--`）。零后端、零 API、零 TS 类型、零 DB 改动；桌面端复用同一 web 构建自动获得。

**Tech Stack:** React + TypeScript（vitest + @testing-library/react、eslint、vite build）。

**对应 spec：** `docs/superpowers/specs/2026-06-11-backtest-short-surface-design.md`（已按对抗审查收敛：cash 出场原因实为 `'cash'`；测试 fixture 用现代方向值规避「做多」文本碰撞）。

**分支：** 执行开始时由 controller 建 `feat/backtest-short-surface`（携带 spec/plan 提交）并把 `feat/crypto-perp-long-short-ratio` 恢复到其交付 tip——与前两个子项目同法，实现者无需处理。

---

## 关键约定（两个任务通用）

**Web 验证脚手架（仓库路径含空格，npm/vitest 必须在 /tmp 无空格副本跑；编辑永远在真实仓库）：**
```bash
rsync -a --delete --exclude node_modules "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/" /tmp/dsa-web-verify/
cd /tmp/dsa-web-verify && [ -d node_modules ] || npm ci
npx vitest run src/pages/__tests__/BacktestPage.test.tsx
```
每次验证前重跑 rsync。web-gate = `npm run lint && npm run build`（vitest 绿≠gate 绿，必须真跑）。

**commit 规范：** 英文类型前缀 + 中文描述，无 Co-Authored-By，无工具前缀。只本地提交，绝不 push。

**后端零改动**：最终核对 `git diff <base>..HEAD --stat -- '*.py'` 为空即可，不跑 ci_gate。

---

## Task 1: BacktestPage 新列「仓位/模拟」（TDD）

**Files:**
- Modify: `apps/dsa-web/src/pages/BacktestPage.tsx`（4 处：两个常量 + badge 助手；表头 th；行 td；min-w）
- Test: `apps/dsa-web/src/pages/__tests__/BacktestPage.test.tsx`

**背景陷阱（必读）**：既有用例断言 `screen.getByText('做多')`——命中**方向列**对 `directionExpected:'long'` 的遗留映射；基础 fixture 无 `positionRecommendation`（新列对它渲染 `--`），所以既有用例保持绿。**不要动基础 fixture**。新用例 fixture 一律用现代方向值（`up`/`down`/`flat`），避免「做多」双匹配抛错。

- [ ] **Step 1: 写失败测试** — 在 `BacktestPage.test.tsx` 的 `describe('BacktestPage', ...)` 块末尾（最后一个 `it` 之后）追加。先在文件顶部 `basePerformance` 定义之后加共享行 fixture：

```tsx
const perpRowBase = {
  analysisHistoryId: 201,
  code: 'BTC/USDT:PERP',
  stockName: 'BTC 永续',
  analysisDate: '2026-05-20',
  evalWindowDays: 10,
  engineVersion: 'test-engine',
  evalStatus: 'completed',
  operationAdvice: '卖出',
  trendPrediction: '看空',
  actualMovement: 'down',
  actualReturnPct: -5.2,
  directionExpected: 'down',
  directionCorrect: true,
  outcome: 'win',
};

function mockSingleRow(row: Record<string, unknown>) {
  mockGetResults.mockResolvedValue({ total: 1, page: 1, limit: 20, items: [row] });
}
```

再追加 6 个用例：

```tsx
  it('renders short position with funding-adjusted simulated return and exit reason', async () => {
    mockSingleRow({ ...perpRowBase, positionRecommendation: 'short', simulatedReturnPct: 5.4, simulatedExitReason: 'window_end_short' });
    render(<BacktestPage />);

    expect(await screen.findByText('做空')).toBeInTheDocument();
    expect(screen.getByText('仓位/模拟')).toBeInTheDocument();      // 新表头
    const ret = screen.getByText('5.4%');                           // 模拟收益（正，绿色）
    expect(ret).toHaveClass('text-success');
    expect(screen.getByText('窗口期满(空)')).toBeInTheDocument();
    expect(screen.getByText('-5.2%')).toBeInTheDocument();          // 价格列红负与结果绿共存的核心反差场景
  });

  it('renders long position with take-profit exit', async () => {
    mockSingleRow({ ...perpRowBase, directionExpected: 'up', actualMovement: 'up', actualReturnPct: 8.0, positionRecommendation: 'long', simulatedReturnPct: 10.0, simulatedExitReason: 'take_profit' });
    render(<BacktestPage />);

    expect(await screen.findByText('做多')).toBeInTheDocument();    // 此 fixture 中唯一（方向列为 '看涨'）
    expect(screen.getByText('10.0%')).toBeInTheDocument();
    expect(screen.getByText('止盈')).toBeInTheDocument();
  });

  it('renders cash position with zero return and no-trade reason', async () => {
    mockSingleRow({ ...perpRowBase, directionExpected: 'flat', actualMovement: 'flat', actualReturnPct: 0.3, positionRecommendation: 'cash', simulatedReturnPct: 0.0, simulatedExitReason: 'cash' });
    render(<BacktestPage />);

    expect(await screen.findByText('空仓')).toBeInTheDocument();
    const ret = screen.getByText('0.0%');
    expect(ret).toHaveClass('text-secondary-text');                 // 0 中性色：真实零收益，非缺数据
    expect(screen.getByText('无交易')).toBeInTheDocument();         // 引擎 cash 行出场原因为 'cash'
  });

  it('renders -- without badge when positionRecommendation is absent', async () => {
    mockSingleRow({ ...perpRowBase });                              // 无 positionRecommendation
    render(<BacktestPage />);

    await screen.findByText('BTC/USDT:PERP');
    expect(screen.queryByText('做空')).not.toBeInTheDocument();
    expect(screen.queryByText('做多')).not.toBeInTheDocument();
    expect(screen.queryByText('空仓')).not.toBeInTheDocument();
    expect(screen.queryByText('无交易')).not.toBeInTheDocument();
  });

  it('renders badge but -- return when simulatedReturnPct is missing', async () => {
    mockSingleRow({ ...perpRowBase, directionExpected: 'up', positionRecommendation: 'long', simulatedExitReason: 'window_end' });
    render(<BacktestPage />);

    expect(await screen.findByText('做多')).toBeInTheDocument();
    expect(screen.getByText('窗口期满')).toBeInTheDocument();       // badge 照渲、收益位为 pct(null)='--'
  });

  it('echoes unknown exit reason verbatim', async () => {
    mockSingleRow({ ...perpRowBase, positionRecommendation: 'short', simulatedReturnPct: 1.0, simulatedExitReason: 'some_future_reason' });
    render(<BacktestPage />);

    expect(await screen.findByText('做空')).toBeInTheDocument();
    expect(screen.getByText('some_future_reason')).toBeInTheDocument();  // labelFromMap 未知值原样回显
  });
```

- [ ] **Step 2: 运行确认失败**

```bash
rsync -a --delete --exclude node_modules "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/" /tmp/dsa-web-verify/
cd /tmp/dsa-web-verify && [ -d node_modules ] || npm ci
npx vitest run src/pages/__tests__/BacktestPage.test.tsx
```
Expected: 6 个新用例 FAIL（找不到 `做空`/`仓位/模拟` 等），原 4 个用例 PASS。

- [ ] **Step 3: 实现（在真实仓库编辑 `BacktestPage.tsx`）**

3a. 在 `DIRECTION_EXPECTED_LABELS` 常量之后追加：

```tsx
const POSITION_LABELS: Record<string, string> = {
  long: '做多',
  short: '做空',
  cash: '空仓',
};

const EXIT_REASON_LABELS: Record<string, string> = {
  take_profit: '止盈',
  stop_loss: '止损',
  window_end: '窗口期满',
  window_end_short: '窗口期满(空)',
  cash: '无交易',
};
```

3b. 在 `actualMovementBadge` 函数之后追加 badge 助手（与其 switch 风格同构）：

```tsx
function positionBadge(position: string) {
  switch (position) {
    case 'long':
      return <Badge variant="success">{POSITION_LABELS.long}</Badge>;
    case 'short':
      return <Badge variant="danger">{POSITION_LABELS.short}</Badge>;
    case 'cash':
      return <Badge variant="default">{POSITION_LABELS.cash}</Badge>;
    default:
      return <Badge variant="default">{position}</Badge>;
  }
}
```

3c. 表头：在 `'窗口收益'` 的 `<th>`（三元切换 `实际表现`/`窗口收益` 那个）与 `'方向匹配'` 的 `<th>` 之间插入：

```tsx
                      <th className="backtest-table-head-cell">仓位/模拟</th>
```

3d. 行单元格：在「窗口收益」`<td>`（含 `actualMovementBadge(row.actualMovement)` 的那个）与「方向匹配」`<td>`（含 `boolIcon(row.directionCorrect)` 的那个）之间插入：

```tsx
                        <td className="backtest-table-cell">
                          {row.positionRecommendation ? (
                            <div className="flex flex-col gap-1">
                              <div className="flex items-center gap-2">
                                {positionBadge(row.positionRecommendation)}
                                <span className={
                                  row.simulatedReturnPct != null
                                    ? row.simulatedReturnPct > 0 ? 'text-success' : row.simulatedReturnPct < 0 ? 'text-danger' : 'text-secondary-text'
                                    : 'text-muted-text'
                                }>
                                  {pct(row.simulatedReturnPct)}
                                </span>
                              </div>
                              <span className="text-xs text-muted-text">
                                {labelFromMap(row.simulatedExitReason, EXIT_REASON_LABELS)}
                              </span>
                            </div>
                          ) : (
                            '--'
                          )}
                        </td>
```

3e. 表宽：`<table className="backtest-table min-w-[900px] ...">` 的 `min-w-[900px]` 改为 `min-w-[1020px]`（spec 允许按实际渲染在 1000–1020 间微调）。

- [ ] **Step 4: 运行确认通过（10/10，含既有 4 个零改动）**

```bash
rsync -a --delete --exclude node_modules "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web/" /tmp/dsa-web-verify/
cd /tmp/dsa-web-verify
npx vitest run src/pages/__tests__/BacktestPage.test.tsx
```
Expected: 10 passed。

- [ ] **Step 5: web-gate**

```bash
cd /tmp/dsa-web-verify && npm run lint && npm run build
```
Expected: lint 0 error、build 成功（输出尾部贴进报告）。

- [ ] **Step 6: 提交（在真实仓库）**

```bash
git add apps/dsa-web/src/pages/BacktestPage.tsx apps/dsa-web/src/pages/__tests__/BacktestPage.test.tsx
git commit -m "feat(web): 回测表新增仓位/模拟列（做空/资金费折算收益/出场原因首次可见）"
```

---

## Task 2: 文档同步（docs-only）

**Files:**
- Modify: `docs/crypto-guide.md`（「永续回测（资金费 + 做空，1x）」小节）
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 段末尾扁平一行）

> Docs only, tests not run。需核对小节名/字段语义与实际一致（先读目标段落再编辑）。

- [ ] **Step 1: crypto-guide 补一行**

在 `docs/crypto-guide.md` 的 `### 永续回测（资金费 + 做空，1x）` 小节末尾（「门控/降级」条目之后）追加：

```markdown
- **Web 透出**：回测页结果表「仓位/模拟」列展示仓位（做多/做空/空仓）、资金费折算后的模拟收益与出场原因（做空恒为窗口期满；空仓显示"无交易"）；旧记录无仓位字段时该列显示 `--`。
```

- [ ] **Step 2: CHANGELOG 扁平一行**

在 `docs/CHANGELOG.md` 的 `## [Unreleased]` 段**末尾**（最后一条 `- [类型] ...` 之后）追加单行（禁止新增 `###` 类目标题）：

```markdown
- [新功能] Web 回测页新增「仓位/模拟」列：透出做多/做空/空仓、资金费折算后的模拟收益与出场原因（perp 做空回测结果首次用户可见，旧数据降级显示 --）
```

- [ ] **Step 3: 核对与提交**

核对：小节名逐字一致；`[Unreleased]` 内无新增 `###`；本任务未触碰任何 `.py`/`.tsx`。

```bash
git add docs/crypto-guide.md docs/CHANGELOG.md
git commit -m "docs: 回测做空透出补 crypto-guide 永续回测小节与 CHANGELOG"
```

---

## 最终验证（全部任务完成后）

- [ ] 后端零改动：`git diff <branch-base>..HEAD --stat -- '*.py'` → 空
- [ ] web-gate 证据在 Task 1（Task 2 为 docs-only，无 apps/ 改动则证据仍有效；若有改动需重跑）
- [ ] 交付说明：改了什么 / 为什么 / 验证情况 / 未验证项（真实浏览器视觉效果——vitest 断言 DOM 不断言布局，列宽 1020 为估值）/ 风险点 / 回滚方式（单 commit revert）

---

## Self-Review（plan 对照 spec）

- **§2 新列结构**（badge 变体/着色三元/出场原因小字/falsy 守卫/min-w）→ Task 1 Step 3a–3e。✓
- **§2 cash='cash'→'无交易' 修正后事实** → EXIT_REASON_LABELS 与 cash 用例一致。✓
- **§3 六个测试场景 + fixture 钉死（down/up/flat 规避做多碰撞）** → Step 1 六用例逐一对应；perpRowBase.directionExpected='down'。✓
- **§4 验证（vitest→web-gate，不跑 ci_gate）** → Step 2/4/5 + 最终验证。✓
- **§5 文档** → Task 2。✓
- **类型一致性**：`POSITION_LABELS`/`EXIT_REASON_LABELS`/`positionBadge` 在 Step 1 测试与 Step 3 实现间命名一致；`pct`/`labelFromMap`/`Badge` 为文件既有符号。✓
- **占位扫描**：每步含完整代码/命令/期望输出。✓
