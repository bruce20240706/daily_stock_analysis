# 容器 B · 个股工作台 Implementation Plan（P0–P5）

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现。步骤用 `- [ ]` 复选框跟踪。
> **Spec（已提交 e529eff8）**：`docs/superpowers/specs/2026-06-16-stock-workstation-design.md`。

**Goal：** 新增可常驻、可导航的单股深度页 `/stock/:code`，汇聚 K 线+信号+报告+历史，并提供页内动作（自选/刷新分析/建告警/复制链接），全部复用现有组件与端点。

**Architecture：** 纯前端新增（后端零改动；`/stocks/{code}/quote` 端点已存在，仅缺前端 client）。页面布局 B：行情头 + K 线区（直接复用 `KLineChartPanel`）常驻顶部 + 下方 4 tab（信号/报告/历史/告警）。各数据源独立降级。从看板行/抽屉/首页报告加「在工作台打开」入口（additive）。

**Tech Stack：** React + TS + react-router（首个参数路由 `:code`）+ vitest/RTL/jsdom；后端无改动。验证沿用无空格 worktree（前端 `npm ci` + `npm run lint`(eslint .) + 完整 vitest + `npm run build`）。

---

## 计划期已核实的关键事实（实现前必读）

1. **无前端 `getQuote`**：后端 `GET /api/v1/stocks/{code}/quote`→`StockQuote` 已存在（`api/v1/endpoints/stocks.py:425`），但 `apps/dsa-web/src/api/stocks.ts` 无 quote 方法、`src/types` 无 `StockQuote` 类型。P0 补 client + 类型（薄包装，非新后端）。后端 StockQuote 字段：`stock_code,stock_name,current_price,change,change_percent,open,high,low,prev_close,volume,amount,update_time`。
2. **`KLineChartPanel`（`src/components/kline/KLineChartPanel.tsx`，命名导出）自取数**：props `{ stockCode: string; market?: string; stockName?: string; days?: number }`，内部调 `stocksApi.getKlineHistory`+`getSignals`。**不从 barrel 导出**（barrel 只导 `KLineDrawer`），B 直接 `import { KLineChartPanel } from '../components/kline/KLineChartPanel'`。复用时需仿 `KLineDrawer` 用 `Suspense`+错误边界包裹（panel 内部已 lazy 引 klinecharts）。
3. **`ReportSummary`（`src/components/report/ReportSummary.tsx`，命名导出）纯 props 驱动**：props `{ data: AnalysisResult | AnalysisReport; isHistory?: boolean; watchlist?: {...} }`，**内部已含 `ReportNews`**（按 `data.meta.id` 取新闻）→ 故**不设独立「新闻」tab**（避免重复取数）。
4. **报告取数 2 跳**：`historyApi.getList({ stockCode, limit: 1 })`→`items[0].id`→`historyApi.getDetail(id)`→`AnalysisReport`。
5. **刷新分析**：`analysisApi.analyzeAsync({ stockCode, reportType: 'detailed', forceRefresh: true })`→`{ taskId }`；轮询 `analysisApi.getStatus(taskId)` 至 `status==='completed'` 取 `result.report`；`status==='failed'`→错误态。`analyzeAsync` 在 409 抛 `DuplicateTaskError(stockCode, existingTaskId)`→改轮询 `existingTaskId`。
6. **自选**：`useWatchlist()`（`src/hooks/useWatchlist.ts`）→ `{ isInWatchlist, toggleWatchlist, isActioning, actionMessage, ... }`。
7. **告警**：`AlertRuleForm`（`src/components/alerts/AlertRuleForm.tsx`）**无 code 预填能力**，`target` 为自由输入。P4 给它加**可选 `lockedTarget?: string`**（additive，默认 undefined 行为不变）：设值时锁 `targetScope='single_symbol'`、预填并只读 target 控件。创建用 `alertsApi.createRule(payload)`；列出本股规则用 `alertsApi.listRules({ target: code, targetScope: 'single_symbol' })`。
8. **`HistoryList`/`AlertRuleList` 是管理型**（必填多选/删除/筛选回调），B 的只读视图**不复用**它们，改用各自的轻量只读小组件（复用 `HistoryItem`/`AlertRuleItem` **类型**与 api，不复用管理 UI）。
9. **无 toast / 无 clipboard util**：动作反馈用 `InlineAlert`（`src/components/common`，props `{ title?, message, variant?, action? }`）；复制用 `navigator.clipboard.writeText`（仿 `ReportDiagnostics.tsx`）。
10. **路由**：`App.tsx` 所有路由在单个 `<Route element={<Shell><RouteOutletBoundary/></Shell>}>` 组内；懒加载 `const X = lazy(()=>import('./pages/X'))`；`useParams`/`useNavigate` 已在用。新增首个参数路由 `/stock/:code`。**不加侧栏项**。

---

## File Structure

```
apps/dsa-web/src/
  types/kline.ts                         # MODIFY: 追加 StockQuote 类型（stock 市场数据类型同源）
  api/stocks.ts                          # MODIFY: 追加 getQuote + Raw 类型 + mapper
  pages/StockWorkstationPage.tsx         # NEW: 页面（/stock/:code，useParams；header+chart+tabs；动作编排）
  pages/__tests__/StockWorkstationPage.test.tsx   # NEW
  components/workstation/
    StockWorkstationHeader.tsx           # NEW: 行情头（名称/代码/quote + 4 动作按钮）
    StockSignalsPanel.tsx                # NEW: 信号 tab（consistency + price lines + markers 列表）
    StockHistoryPanel.tsx                # NEW: 历史 tab（只读列表，点条目回调）
    StockAlertsPanel.tsx                 # NEW: 告警 tab（AlertRuleForm[lockedTarget] + 本股规则只读列表 + 去告警页链接）
    __tests__/StockWorkstationHeader.test.tsx     # NEW
    __tests__/StockSignalsPanel.test.tsx          # NEW
    __tests__/StockHistoryPanel.test.tsx          # NEW
    __tests__/StockAlertsPanel.test.tsx           # NEW
  components/alerts/AlertRuleForm.tsx    # MODIFY: 追加可选 lockedTarget 预填+锁定
  components/alerts/__tests__/AlertRuleForm.test.tsx  # MODIFY/ADD: lockedTarget 行为
  App.tsx                                # MODIFY: 懒加载 + 路由 /stock/:code
  components/board/SignalBoardGroup.tsx  # MODIFY: 每行加「工作台↗」入口
  components/kline/KLineDrawer.tsx       # MODIFY: 头部加「在工作台打开↗」
  components/report/ReportOverview.tsx   # MODIFY: 加「在工作台打开↗」
docs/CHANGELOG.md / docs/stock-workstation.md   # MODIFY/NEW（P5）
```

---

# P0 · 路由 + 页面骨架 + quote client/type

### Task P0.1：StockQuote 类型 + getQuote client + api 测试

**Files**
- Modify: `apps/dsa-web/src/types/kline.ts`（追加，不动既有）
- Modify: `apps/dsa-web/src/api/stocks.ts`
- Create: `apps/dsa-web/src/api/__tests__/stocks.quote.test.ts`

- [ ] **Step 1: 追加类型**（`src/types/kline.ts` 末尾）

```typescript
// ============ Quote（/stocks/{code}/quote 实时行情） ============
export interface StockQuote {
  stockCode: string;
  stockName: string | null;
  currentPrice: number;
  change: number | null;
  changePercent: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  prevClose: number | null;
  volume: number | null;
  amount: number | null;
  updateTime: string | null;
}
```

- [ ] **Step 2: 写失败测试**（仿 `stocks.board.test.ts` 的 mock 风格）

```typescript
// apps/dsa-web/src/api/__tests__/stocks.quote.test.ts
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { stocksApi } from '../stocks';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../index', () => ({ default: { get } }));

describe('stocksApi.getQuote', () => {
  beforeEach(() => { get.mockReset(); });

  it('maps snake_case quote to camelCase and encodes the code', async () => {
    get.mockResolvedValueOnce({ data: {
      stock_code: 'BTC/USDT', stock_name: 'Bitcoin', current_price: 65000,
      change: 1200, change_percent: 1.88, open: 64000, high: 66000, low: 63500,
      prev_close: 63800, volume: 1234, amount: 80000000, update_time: '2026-06-16T15:00:00',
    }});
    const res = await stocksApi.getQuote('BTC/USDT');
    expect(get).toHaveBeenCalledWith('/api/v1/stocks/BTC%2FUSDT/quote');
    expect(res.currentPrice).toBe(65000);
    expect(res.changePercent).toBe(1.88);
    expect(res.prevClose).toBe(63800);
    expect(res.stockName).toBe('Bitcoin');
  });

  it('tolerates null optional fields', async () => {
    get.mockResolvedValueOnce({ data: {
      stock_code: '600519', stock_name: null, current_price: 1660, change: null,
      change_percent: null, open: null, high: null, low: null, prev_close: null,
      volume: null, amount: null, update_time: null,
    }});
    const res = await stocksApi.getQuote('600519');
    expect(res.currentPrice).toBe(1660);
    expect(res.change).toBeNull();
    expect(res.stockName).toBeNull();
  });
});
```

- [ ] **Step 3: 跑验证失败** → `cd /root/dsa-stock-workstation/apps/dsa-web && npx vitest run src/api/__tests__/stocks.quote.test.ts`（getQuote 未定义）。

- [ ] **Step 4: 实现**（`src/api/stocks.ts`：顶部 import 追加 `StockQuote`；`stocksApi` 内加方法。`getSignals` 已用 `encodeURIComponent(code)`，照此）

```typescript
// import type 行追加 StockQuote（与现有 from '../types/kline' 并列或合并）
import type { StockQuote } from '../types/kline';

// stocksApi 对象内追加：
  async getQuote(code: string): Promise<StockQuote> {
    const response = await apiClient.get(`/api/v1/stocks/${encodeURIComponent(code)}/quote`);
    const d = response.data as {
      stock_code: string; stock_name: string | null; current_price: number;
      change: number | null; change_percent: number | null; open: number | null;
      high: number | null; low: number | null; prev_close: number | null;
      volume: number | null; amount: number | null; update_time: string | null;
    };
    return {
      stockCode: d.stock_code, stockName: d.stock_name ?? null, currentPrice: d.current_price,
      change: d.change ?? null, changePercent: d.change_percent ?? null, open: d.open ?? null,
      high: d.high ?? null, low: d.low ?? null, prevClose: d.prev_close ?? null,
      volume: d.volume ?? null, amount: d.amount ?? null, updateTime: d.update_time ?? null,
    };
  },
```

- [ ] **Step 5: 跑验证通过 + tsc + eslint** → `npx vitest run src/api/__tests__/stocks.quote.test.ts`（2 passed）；`npx tsc --noEmit`；`npx eslint src/api/stocks.ts src/types/kline.ts src/api/__tests__/stocks.quote.test.ts`。

- [ ] **Step 6: Commit**

```bash
git add apps/dsa-web/src/types/kline.ts apps/dsa-web/src/api/stocks.ts apps/dsa-web/src/api/__tests__/stocks.quote.test.ts
git commit -m "feat: 前端 StockQuote 类型 + stocksApi.getQuote client(snake→camel)(P0,容器B)"
```

### Task P0.2：路由 + 页面骨架（useParams + 非法 code 降级）

**Files**
- Create: `apps/dsa-web/src/pages/StockWorkstationPage.tsx`
- Create: `apps/dsa-web/src/pages/__tests__/StockWorkstationPage.test.tsx`
- Modify: `apps/dsa-web/src/App.tsx`

- [ ] **Step 1: 写失败测试**（骨架版：渲染标题 + 非法 code 降级；mock 子区块依赖，后续任务再扩。用 `MemoryRouter` + `Routes` 驱动 `:code`）

```tsx
// apps/dsa-web/src/pages/__tests__/StockWorkstationPage.test.tsx
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

// 子区块在后续任务实现；骨架测试先 mock 掉，聚焦路由/参数/降级
vi.mock('../../components/kline/KLineChartPanel', () => ({ KLineChartPanel: () => <div data-testid="chart" /> }));
vi.mock('../../components/workstation/StockWorkstationHeader', () => ({ StockWorkstationHeader: (p: { code: string }) => <div data-testid="ws-header">{p.code}</div> }));

import StockWorkstationPage from '../StockWorkstationPage';

const renderAt = (path: string) => render(
  <MemoryRouter initialEntries={[path]}>
    <Routes><Route path="/stock/:code" element={<StockWorkstationPage />} /></Routes>
  </MemoryRouter>,
);

describe('StockWorkstationPage skeleton', () => {
  it('reads :code from the route and renders the header', () => {
    renderAt('/stock/600519');
    expect(screen.getByTestId('ws-header')).toHaveTextContent('600519');
  });

  it('decodes crypto codes from the route param', () => {
    renderAt('/stock/' + encodeURIComponent('BTC/USDT'));
    expect(screen.getByTestId('ws-header')).toHaveTextContent('BTC/USDT');
  });
});
```

- [ ] **Step 2: 跑验证失败** → 页面/组件不存在。

- [ ] **Step 3: 实现骨架**（先建一个最小 header 占位以让骨架测试可跑；真 header 在 P1 替换该文件）

先建占位 `apps/dsa-web/src/components/workstation/StockWorkstationHeader.tsx`：
```tsx
import type React from 'react';

interface StockWorkstationHeaderProps { code: string; }

export const StockWorkstationHeader: React.FC<StockWorkstationHeaderProps> = ({ code }) => (
  <header data-testid="ws-header">{code}</header>
);
```

页面 `apps/dsa-web/src/pages/StockWorkstationPage.tsx`：
```tsx
import type React from 'react';
import { useParams } from 'react-router-dom';
import { AppPage, InlineAlert } from '../components/common';
import { StockWorkstationHeader } from '../components/workstation/StockWorkstationHeader';

const StockWorkstationPage: React.FC = () => {
  const params = useParams<{ code: string }>();
  const code = params.code ? decodeURIComponent(params.code) : '';

  if (!code) {
    return (
      <AppPage className="space-y-4 pb-12 pt-6">
        <InlineAlert variant="danger" message="未找到该标的（缺少代码）。" />
      </AppPage>
    );
  }

  return (
    <AppPage className="space-y-4 pb-12 pt-6">
      <StockWorkstationHeader code={code} />
      {/* P2: 图区；P3: tabs */}
    </AppPage>
  );
};

export default StockWorkstationPage;
```

> 注：react-router 已对路径参数自动解码，再 `decodeURIComponent` 对普通码无害，但对含 `%` 的码可能二次解码。**实现时**核实 `:code` 是否已解码：若 `useParams` 已给出 `BTC/USDT`，则去掉 `decodeURIComponent`。骨架测试用 `encodeURIComponent` 入路径并断言解码结果，按实际表现二选一（保留能让 crypto 测试通过的那种）。

- [ ] **Step 4: App.tsx 注册路由**

```tsx
// 懒加载区追加：
const StockWorkstationPage = lazy(() => import('./pages/StockWorkstationPage'));
// Shell 路由组内追加（放在 /board 之后）：
<Route path="/stock/:code" element={<StockWorkstationPage />} />
```

- [ ] **Step 5: 跑验证通过 + eslint + tsc** → `npx vitest run src/pages/__tests__/StockWorkstationPage.test.tsx`（2 passed）；`npx eslint src/pages/StockWorkstationPage.tsx src/components/workstation/StockWorkstationHeader.tsx src/App.tsx`；`npx tsc --noEmit`。

- [ ] **Step 6: Commit**

```bash
git add apps/dsa-web/src/pages/StockWorkstationPage.tsx apps/dsa-web/src/pages/__tests__/StockWorkstationPage.test.tsx apps/dsa-web/src/components/workstation/StockWorkstationHeader.tsx apps/dsa-web/src/App.tsx
git commit -m "feat: /stock/:code 路由 + 工作台页面骨架(useParams/非法码降级)(P0,容器B)"
```

---

# P1 · 行情头（quote + 自选 + 复制链接）

### Task P1.1：StockWorkstationHeader（quote 渲染 + 自选 toggle + 复制链接 + 动作按钮槽）

**Files**
- Modify: `apps/dsa-web/src/components/workstation/StockWorkstationHeader.tsx`（替换 P0 占位）
- Create: `apps/dsa-web/src/components/workstation/__tests__/StockWorkstationHeader.test.tsx`
- Modify: `apps/dsa-web/src/pages/StockWorkstationPage.tsx`（页面装上真 header 并传 props——否则 header props 变必填后页面 tsc 不过）
- Modify: `apps/dsa-web/src/pages/__tests__/StockWorkstationPage.test.tsx`（页面现在会调 `useWatchlist`，须 mock 掉）

设计：header 自取 quote（`stocksApi.getQuote`）；自选 toggle 由父传入（页面级 `useWatchlist`，便于测试）；「刷新分析」「建告警」为回调 props（P4 接线，本任务先传占位）；「复制链接」内联 `navigator.clipboard`。中式涨跌色：涨=红(`text-danger`)、跌=绿(`text-success`)。

> **重要（任务级 tsc 绿）**：本任务把 header props 变为必填（`watchlist`/`onRefreshAnalysis`/`onBuildAlert`）。因此**必须同时**改页面：加 `const watchlist = useWatchlist()`，渲染 `<StockWorkstationHeader code={code} watchlist={watchlist} onRefreshAnalysis={() => {}} onBuildAlert={() => {}} />`（两个回调 P4.3 再换真实现）。并在页面测试顶部加 `vi.mock('../../hooks/useWatchlist', () => ({ useWatchlist: () => ({ isInWatchlist: () => false, toggleWatchlist: vi.fn(), isActioning: false, actionMessage: null, watchlistCodes: [], isLoading: false, addToWatchlist: vi.fn(), removeFromWatchlist: vi.fn(), refresh: vi.fn() }) }))`，避免真实 hook 在测试里打网络。P0.2 的骨架测试 mock 了 header，仍通过。

- [ ] **Step 1: 写失败测试**

```tsx
// apps/dsa-web/src/components/workstation/__tests__/StockWorkstationHeader.test.tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const { getQuote } = vi.hoisted(() => ({ getQuote: vi.fn() }));
vi.mock('../../../api/stocks', () => ({ stocksApi: { getQuote } }));

import { StockWorkstationHeader } from '../StockWorkstationHeader';

const watchlist = {
  isInWatchlist: (c: string) => c === '600519',
  toggleWatchlist: vi.fn(async () => {}),
  isActioning: false,
};

const baseProps = () => ({
  code: '600519',
  watchlist,
  onRefreshAnalysis: vi.fn(),
  onBuildAlert: vi.fn(),
});

describe('StockWorkstationHeader', () => {
  afterEach(() => { getQuote.mockReset(); vi.restoreAllMocks(); });

  it('renders code and fetched quote (price + change%)', async () => {
    getQuote.mockResolvedValueOnce({ stockCode: '600519', stockName: '贵州茅台',
      currentPrice: 1660, change: 20, changePercent: 1.22, open: null, high: null, low: null,
      prevClose: null, volume: null, amount: null, updateTime: null });
    render(<StockWorkstationHeader {...baseProps()} />);
    expect(screen.getByText('600519')).toBeInTheDocument();
    expect(await screen.findByText('贵州茅台')).toBeInTheDocument();
    expect(await screen.findByText(/1660/)).toBeInTheDocument();
    expect(await screen.findByText(/1\.22%/)).toBeInTheDocument();
  });

  it('toggles watchlist on star click', async () => {
    getQuote.mockResolvedValueOnce({ stockCode: '600519', stockName: '贵州茅台', currentPrice: 1660,
      change: null, changePercent: null, open: null, high: null, low: null, prevClose: null,
      volume: null, amount: null, updateTime: null });
    render(<StockWorkstationHeader {...baseProps()} />);
    fireEvent.click(screen.getByRole('button', { name: /自选/ }));
    await waitFor(() => expect(watchlist.toggleWatchlist).toHaveBeenCalledWith('600519'));
  });

  it('copies the workstation URL on copy click', async () => {
    getQuote.mockResolvedValueOnce({ stockCode: '600519', stockName: null, currentPrice: 1660,
      change: null, changePercent: null, open: null, high: null, low: null, prevClose: null,
      volume: null, amount: null, updateTime: null });
    const writeText = vi.fn(async () => {});
    Object.assign(navigator, { clipboard: { writeText } });
    render(<StockWorkstationHeader {...baseProps()} />);
    fireEvent.click(screen.getByRole('button', { name: /复制链接/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(expect.stringContaining('/stock/600519')));
  });

  it('invokes refresh + build-alert callbacks', async () => {
    getQuote.mockResolvedValueOnce({ stockCode: '600519', stockName: null, currentPrice: 1660,
      change: null, changePercent: null, open: null, high: null, low: null, prevClose: null,
      volume: null, amount: null, updateTime: null });
    const props = baseProps();
    render(<StockWorkstationHeader {...props} />);
    fireEvent.click(screen.getByRole('button', { name: /刷新分析/ }));
    fireEvent.click(screen.getByRole('button', { name: /建告警/ }));
    expect(props.onRefreshAnalysis).toHaveBeenCalled();
    expect(props.onBuildAlert).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: 跑验证失败**。

- [ ] **Step 3: 实现**

```tsx
// apps/dsa-web/src/components/workstation/StockWorkstationHeader.tsx
import { useCallback, useEffect, useState } from 'react';
import type React from 'react';
import { stocksApi } from '../../api/stocks';
import type { StockQuote } from '../../types/kline';
import { Button } from '../common';
import { cn } from '../../utils/cn';

interface StockWorkstationHeaderProps {
  code: string;
  watchlist: {
    isInWatchlist: (code: string) => boolean;
    toggleWatchlist: (code: string) => Promise<void>;
    isActioning: boolean;
  };
  onRefreshAnalysis: () => void;
  onBuildAlert: () => void;
  refreshing?: boolean;
}

const fmtPct = (v: number | null) => (v === null ? '' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`);

export const StockWorkstationHeader: React.FC<StockWorkstationHeaderProps> = ({
  code, watchlist, onRefreshAnalysis, onBuildAlert, refreshing = false,
}) => {
  const [quote, setQuote] = useState<StockQuote | null>(null);
  const [copied, setCopied] = useState(false);
  const starred = watchlist.isInWatchlist(code);

  useEffect(() => {
    let alive = true;
    void stocksApi.getQuote(code).then((q) => { if (alive) setQuote(q); }).catch(() => { /* 行情失败不阻塞页面 */ });
    return () => { alive = false; };
  }, [code]);

  const up = (quote?.changePercent ?? 0) >= 0;

  const copyLink = useCallback(async () => {
    const url = `${window.location.origin}/stock/${encodeURIComponent(code)}`;
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    }
  }, [code]);

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
      <div className="flex items-baseline gap-3">
        <span className="text-2xl font-bold text-foreground">{quote?.stockName ?? code}</span>
        <span className="text-sm text-secondary-text">{code}</span>
        {quote && (
          <span className={cn('text-lg font-semibold', up ? 'text-danger' : 'text-success')}>
            {quote.currentPrice}
            {quote.changePercent !== null && <span className="ml-2 text-sm">{fmtPct(quote.changePercent)}</span>}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={() => void watchlist.toggleWatchlist(code)} disabled={watchlist.isActioning}>
          {starred ? '★ 已自选' : '☆ 自选'}
        </Button>
        <Button variant="ghost" size="sm" onClick={onRefreshAnalysis} disabled={refreshing} isLoading={refreshing} loadingText="分析中…">
          刷新分析
        </Button>
        <Button variant="ghost" size="sm" onClick={onBuildAlert}>建告警</Button>
        <Button variant="ghost" size="sm" onClick={() => void copyLink()}>{copied ? '已复制' : '复制链接'}</Button>
      </div>
    </header>
  );
};
```

- [ ] **Step 4: 跑验证通过 + eslint + tsc**（注意：测试里点击「自选」按钮名匹配 `/自选/`，按钮文案含「自选」；`navigator.clipboard` 在 jsdom 需测试内注入，已在测试 Object.assign）。

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/components/workstation/StockWorkstationHeader.tsx apps/dsa-web/src/components/workstation/__tests__/StockWorkstationHeader.test.tsx apps/dsa-web/src/pages/StockWorkstationPage.tsx apps/dsa-web/src/pages/__tests__/StockWorkstationPage.test.tsx
git commit -m "feat: 工作台行情头(quote/自选/复制链接/动作槽)+页面装载(P1,容器B)"
```

---

# P2 · K 线区（复用 KLineChartPanel）

### Task P2.1：页面挂入 K 线区（Suspense + 错误边界）

**Files**
- Modify: `apps/dsa-web/src/pages/StockWorkstationPage.tsx`
- Modify: `apps/dsa-web/src/pages/__tests__/StockWorkstationPage.test.tsx`（追加：图区渲染断言）

- [ ] **Step 1: 追加失败测试**（页面把 code 传给 KLineChartPanel）

```tsx
// 追加到 StockWorkstationPage.test.tsx 内 describe
it('renders the K-line chart for the code', () => {
  renderAt('/stock/600519');
  expect(screen.getByTestId('chart')).toBeInTheDocument();
});
```
（已 mock `KLineChartPanel`→`data-testid="chart"`；现需页面真的渲染它。）

- [ ] **Step 2: 跑验证失败**（页面尚未渲染 chart）。

- [ ] **Step 3: 实现**（页面引入 `KLineChartPanel`，用 Suspense + 错误边界包裹；仿 `KLineDrawer` 但内联到页）

```tsx
// StockWorkstationPage.tsx：顶部
import { Suspense } from 'react';
import { KLineChartPanel } from '../components/kline/KLineChartPanel';
// 注：KLineChartPanel 自身 import 已含其依赖；若需懒载可 lazy()，此处直接引用即可（panel 内部已 lazy 引 klinecharts）。

// header 之后插入图区：
<section className="rounded-lg border border-border bg-card p-2">
  <Suspense fallback={<div className="h-64 animate-pulse rounded bg-hover" />}>
    <KLineChartPanel stockCode={code} />
  </Suspense>
</section>
```

> 错误边界：复用 `KLineDrawer` 内的 `KLineDrawerErrorBoundary` 不可直接导出（私有）。**实现时**二选一：(a) 把该错误边界抽到 `src/components/kline/KLineChartErrorBoundary.tsx` 并在 Drawer 与本页共用（最小重构、消除重复）；(b) 页内放一个等价的轻量 ErrorBoundary。优先 (a)（DRY），但属改动 A 文件——若想零触碰 A，用 (b)。测试中 chart 被 mock，错误边界不影响骨架测试。

- [ ] **Step 4: 跑验证通过 + eslint + tsc**。

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/pages/StockWorkstationPage.tsx apps/dsa-web/src/pages/__tests__/StockWorkstationPage.test.tsx
# 若采纳 (a)：一并 add src/components/kline/KLineChartErrorBoundary.tsx 与 KLineDrawer.tsx
git commit -m "feat: 工作台挂入 K 线区(复用 KLineChartPanel + Suspense/错误边界)(P2,容器B)"
```

---

# P3 · Tabs + 信号 / 报告 / 历史 面板

### Task P3.1：StockSignalsPanel（信号 tab）

**Files**
- Create: `apps/dsa-web/src/components/workstation/StockSignalsPanel.tsx`
- Create: `apps/dsa-web/src/components/workstation/__tests__/StockSignalsPanel.test.tsx`

设计：自取 `stocksApi.getSignals(code)`，渲染 consistency 徽章 + price lines + markers 列表（signalType/方向/source/hit-rate）。中式色。

- [ ] **Step 1: 写失败测试**

```tsx
// apps/dsa-web/src/components/workstation/__tests__/StockSignalsPanel.test.tsx
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
const { getSignals } = vi.hoisted(() => ({ getSignals: vi.fn() }));
vi.mock('../../../api/stocks', () => ({ stocksApi: { getSignals } }));
import { StockSignalsPanel } from '../StockSignalsPanel';

const sig = (over = {}) => ({ status: 'ok', consistency: 'consistent', degradedReason: null,
  priceLines: { entry: 1700, stop: 1620, target: 1850 },
  markers: [{ timestamp: 1, price: 1700, anchor: 'low', direction: 'bullish', signalType: 'volume_breakout',
    source: 'rule', confidence: 'high', isDailyApprox: false, isAnomalous: false, reason: '放量突破',
    threshold: null, observedValue: null, hitRate: 0.62, hitSample: 18, verified: true, asOf: null }],
  ...over });

describe('StockSignalsPanel', () => {
  afterEach(() => { getSignals.mockReset(); });
  it('renders consistency, price lines and markers after fetch', async () => {
    getSignals.mockResolvedValueOnce(sig());
    render(<StockSignalsPanel code="600519" />);
    expect(await screen.findByText('volume_breakout')).toBeInTheDocument();
    expect(screen.getByText(/一致/)).toBeInTheDocument();
    expect(screen.getByText(/1700/)).toBeInTheDocument();
  });
  it('shows an error state when signals fetch fails', async () => {
    getSignals.mockRejectedValueOnce(new Error('boom'));
    render(<StockSignalsPanel code="600519" />);
    expect(await screen.findByText(/信号加载失败/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 跑验证失败**。
- [ ] **Step 3: 实现**

```tsx
// apps/dsa-web/src/components/workstation/StockSignalsPanel.tsx
import { useEffect, useState } from 'react';
import type React from 'react';
import { stocksApi } from '../../api/stocks';
import type { SignalsResponse } from '../../types/kline';
import { Loading } from '../common';
import { cn } from '../../utils/cn';

const consistencyLabel: Record<string, string> = {
  consistent: '一致', divergent: '分歧', conflict: '冲突', unknown: '未知', stale: '过期',
};
const dirLabel: Record<string, string> = { bullish: '看多', bearish: '看空', neutral: '中性' };
const fmt = (v: number | null) => (v === null ? '—' : String(v));

export const StockSignalsPanel: React.FC<{ code: string }> = ({ code }) => {
  const [data, setData] = useState<SignalsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true); setError(false);
    void stocksApi.getSignals(code)
      .then((d) => { if (alive) setData(d); })
      .catch(() => { if (alive) setError(true); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [code]);

  if (loading) return <Loading label="正在加载信号" />;
  if (error || !data) return <div className="text-sm text-secondary-text">信号加载失败</div>;
  const pl = data.priceLines;
  return (
    <div className="space-y-3 text-sm">
      <div className="text-secondary-text">一致性：{consistencyLabel[data.consistency] ?? data.consistency}
        {' · '}入/损/标：{fmt(pl.entry)}/{fmt(pl.stop)}/{fmt(pl.target)}</div>
      <table className="w-full">
        <thead><tr className="text-xs text-secondary-text"><th className="text-left">信号</th><th>方向</th><th>来源</th><th>命中率</th></tr></thead>
        <tbody>
          {data.markers.map((m, i) => (
            <tr key={`${m.signalType}-${m.timestamp}-${i}`} className="border-t border-border/60">
              <td className="py-1 text-left">{m.signalType}</td>
              <td className={cn(m.direction === 'bullish' && 'text-danger', m.direction === 'bearish' && 'text-success')}>{dirLabel[m.direction]}</td>
              <td className="text-secondary-text">{m.source === 'llm' ? 'LLM' : '规则'}</td>
              <td className="text-secondary-text">{m.hitRate === null || m.hitSample === null || m.hitSample <= 0 ? '暂无样本' : `${Math.round(m.hitRate * 100)}% · ${m.verified ? '已验证' : '未验证'}`}</td>
            </tr>
          ))}
          {data.markers.length === 0 && <tr><td colSpan={4} className="py-2 text-secondary-text">暂无量价信号</td></tr>}
        </tbody>
      </table>
    </div>
  );
};
```

- [ ] **Step 4: 跑验证通过 + eslint + tsc**。
- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/components/workstation/StockSignalsPanel.tsx apps/dsa-web/src/components/workstation/__tests__/StockSignalsPanel.test.tsx
git commit -m "feat: 工作台信号面板(consistency/价位线/markers 列表)(P3,容器B)"
```

### Task P3.2：StockHistoryPanel（历史 tab，只读列表）

**Files**
- Create: `apps/dsa-web/src/components/workstation/StockHistoryPanel.tsx`
- Create: `apps/dsa-web/src/components/workstation/__tests__/StockHistoryPanel.test.tsx`

设计：自取 `historyApi.getList({ stockCode, limit: 20 })`，渲染只读行（日期 + 操作建议 + 情绪分）；点行 `onSelect(recordId)`（父用于切到报告 tab 并加载该条）。**不复用管理型 HistoryList**（见关键事实 #8）。

- [ ] **Step 1: 写失败测试**

```tsx
// apps/dsa-web/src/components/workstation/__tests__/StockHistoryPanel.test.tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
const { getList } = vi.hoisted(() => ({ getList: vi.fn() }));
vi.mock('../../../api/history', () => ({ historyApi: { getList } }));
import { StockHistoryPanel } from '../StockHistoryPanel';

const item = (over = {}) => ({ id: 7, queryId: 'q', stockCode: '600519', stockName: '贵州茅台',
  operationAdvice: '买入', sentimentScore: 72, createdAt: '2026-06-12T10:00:00', ...over });

describe('StockHistoryPanel', () => {
  afterEach(() => { getList.mockReset(); });
  it('lists past analyses and fires onSelect with recordId on row click', async () => {
    getList.mockResolvedValueOnce({ total: 1, page: 1, limit: 20, items: [item()] });
    const onSelect = vi.fn();
    render(<StockHistoryPanel code="600519" onSelect={onSelect} />);
    const row = await screen.findByText('买入');
    fireEvent.click(row);
    expect(onSelect).toHaveBeenCalledWith(7);
  });
  it('shows empty state when no history', async () => {
    getList.mockResolvedValueOnce({ total: 0, page: 1, limit: 20, items: [] });
    render(<StockHistoryPanel code="600519" onSelect={vi.fn()} />);
    expect(await screen.findByText(/尚无分析/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 跑验证失败**。
- [ ] **Step 3: 实现**

```tsx
// apps/dsa-web/src/components/workstation/StockHistoryPanel.tsx
import { useEffect, useState } from 'react';
import type React from 'react';
import { historyApi } from '../../api/history';
import type { HistoryItem } from '../../types/analysis';
import { Loading } from '../common';

export const StockHistoryPanel: React.FC<{ code: string; onSelect: (recordId: number) => void }> = ({ code, onSelect }) => {
  const [items, setItems] = useState<HistoryItem[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    setError(false); setItems(null);
    void historyApi.getList({ stockCode: code, limit: 20 })
      .then((r) => { if (alive) setItems(r.items); })
      .catch(() => { if (alive) setError(true); });
    return () => { alive = false; };
  }, [code]);

  if (error) return <div className="text-sm text-secondary-text">历史加载失败</div>;
  if (items === null) return <Loading label="正在加载历史" />;
  if (items.length === 0) return <div className="text-sm text-secondary-text">尚无分析记录，点上方「刷新分析」生成。</div>;

  return (
    <ul className="divide-y divide-border/60 text-sm">
      {items.map((it) => (
        <li key={it.id}>
          <button type="button" onClick={() => onSelect(it.id)}
            className="flex w-full items-center justify-between py-2 text-left hover:bg-hover">
            <span className="text-foreground">{it.operationAdvice ?? '—'}</span>
            <span className="text-xs text-secondary-text">{it.createdAt?.slice(0, 10)}{it.sentimentScore != null ? ` · 情绪 ${it.sentimentScore}` : ''}</span>
          </button>
        </li>
      ))}
    </ul>
  );
};
```

- [ ] **Step 4: 跑验证通过 + eslint + tsc**。
- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/components/workstation/StockHistoryPanel.tsx apps/dsa-web/src/components/workstation/__tests__/StockHistoryPanel.test.tsx
git commit -m "feat: 工作台历史面板(只读列表/点选回调)(P3,容器B)"
```

### Task P3.3：页面装配 tabs（信号/报告/历史）+ 报告 2 跳取数

**Files**
- Modify: `apps/dsa-web/src/pages/StockWorkstationPage.tsx`
- Modify: `apps/dsa-web/src/pages/__tests__/StockWorkstationPage.test.tsx`

设计：tab 状态（默认「信号」）；懒取——切到「报告」时才 2 跳取最新报告（或点历史条目取指定 recordId）；报告区用 `ReportSummary`。告警 tab 占位（P4 填）。页面持有 `useWatchlist` 并传给 header。

- [ ] **Step 1: 追加失败测试**（tab 切换 + 报告懒取 + 历史点选切报告）

```tsx
// 追加到 StockWorkstationPage.test.tsx 顶部 mock（注意：useWatchlist mock 已在 P1.1 加入本文件，勿重复）：
vi.mock('../../components/workstation/StockSignalsPanel', () => ({ StockSignalsPanel: () => <div data-testid="sig-panel" /> }));
vi.mock('../../components/workstation/StockHistoryPanel', () => ({ StockHistoryPanel: (p: { onSelect: (id: number) => void }) => <button data-testid="hist-row" onClick={() => p.onSelect(7)}>hist</button> }));
vi.mock('../../components/report/ReportSummary', () => ({ ReportSummary: (p: { data: { meta: { id?: number } } }) => <div data-testid="report">{p.data.meta.id}</div> }));
const { getList, getDetail } = vi.hoisted(() => ({ getList: vi.fn(), getDetail: vi.fn() }));
vi.mock('../../api/history', () => ({ historyApi: { getList, getDetail } }));

// 追加测试：
it('shows signals tab by default and switches to report (lazy 2-hop fetch)', async () => {
  getList.mockResolvedValueOnce({ total: 1, page: 1, limit: 1, items: [{ id: 7 }] });
  getDetail.mockResolvedValueOnce({ meta: { id: 7, stockCode: '600519', stockName: '贵州茅台', queryId: 'q', reportType: 'detailed', createdAt: 'x' }, summary: {} });
  renderAt('/stock/600519');
  expect(screen.getByTestId('sig-panel')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: /报告/ }));
  expect(await screen.findByTestId('report')).toHaveTextContent('7');
  expect(getList).toHaveBeenCalledWith({ stockCode: '600519', limit: 1 });
});

it('loading a history item opens its report', async () => {
  getDetail.mockResolvedValueOnce({ meta: { id: 7, stockCode: '600519', stockName: 'x', queryId: 'q', reportType: 'detailed', createdAt: 'x' }, summary: {} });
  renderAt('/stock/600519');
  fireEvent.click(screen.getByRole('tab', { name: /历史/ }));
  fireEvent.click(screen.getByTestId('hist-row'));
  expect(await screen.findByTestId('report')).toHaveTextContent('7');
  expect(getDetail).toHaveBeenCalledWith(7);
});
```
（需把 `fireEvent` 加入 import。）

- [ ] **Step 2: 跑验证失败**。
- [ ] **Step 3: 实现**（页面装配；tab 用 `role="tab"`；报告懒取 + 历史点选共用一个 `loadReport(recordId?)`）

```tsx
// StockWorkstationPage.tsx 关键片段（替换/扩展）
import { useCallback, useEffect, useState } from 'react';
import { historyApi } from '../api/history';
import type { AnalysisReport } from '../types/analysis';
import { useWatchlist } from '../hooks/useWatchlist';
import { StockSignalsPanel } from '../components/workstation/StockSignalsPanel';
import { StockHistoryPanel } from '../components/workstation/StockHistoryPanel';
import { ReportSummary } from '../components/report/ReportSummary';

type TabKey = 'signals' | 'report' | 'history' | 'alerts';
const TABS: { key: TabKey; label: string }[] = [
  { key: 'signals', label: '信号' }, { key: 'report', label: '报告' },
  { key: 'history', label: '历史' }, { key: 'alerts', label: '告警' },
];

// 组件内（`const watchlist = useWatchlist()` 已在 P1.1 加入，勿重复声明；本任务复用它）：
const [tab, setTab] = useState<TabKey>('signals');
const [report, setReport] = useState<AnalysisReport | null>(null);
const [reportLoading, setReportLoading] = useState(false);
const [reportError, setReportError] = useState<string | null>(null);

const loadReport = useCallback(async (recordId?: number) => {
  setReportLoading(true); setReportError(null);
  try {
    let id = recordId;
    if (id === undefined) {
      const list = await historyApi.getList({ stockCode: code, limit: 1 });
      id = list.items[0]?.id;
    }
    if (id === undefined) { setReport(null); return; }   // 无分析记录
    setReport(await historyApi.getDetail(id));
  } catch { setReportError('报告加载失败'); }
  finally { setReportLoading(false); }
}, [code]);

// 切到报告 tab 且尚未取过 → 取最新
useEffect(() => { if (tab === 'report' && !report && !reportLoading && !reportError) void loadReport(); }, [tab, report, reportLoading, reportError, loadReport]);

const onSelectHistory = useCallback((recordId: number) => { setTab('report'); void loadReport(recordId); }, [loadReport]);

// JSX：header（传 watchlist + onRefreshAnalysis(P4)/onBuildAlert(P4)）→ 图区 → tab 栏 → tab 内容
<div role="tablist" className="flex gap-2 border-b border-border">
  {TABS.map((t) => (
    <button key={t.key} role="tab" aria-selected={tab === t.key} onClick={() => setTab(t.key)}
      className={cn('px-3 py-2 text-sm', tab === t.key ? 'border-b-2 border-foreground text-foreground' : 'text-secondary-text')}>
      {t.label}
    </button>
  ))}
</div>
<div className="pt-3">
  {tab === 'signals' && <StockSignalsPanel code={code} />}
  {tab === 'report' && (
    reportLoading ? <Loading label="正在加载报告" />
    : reportError ? <InlineAlert variant="danger" message={reportError} />
    : report ? <ReportSummary data={report} isHistory watchlist={{ isInWatchlist: watchlist.isInWatchlist, onToggle: watchlist.toggleWatchlist, isActioning: watchlist.isActioning, actionMessage: watchlist.actionMessage }} />
    : <div data-testid="report-empty" className="text-sm text-secondary-text">尚无分析，点上方「刷新分析」生成。</div>
  )}
  {tab === 'history' && <StockHistoryPanel code={code} onSelect={onSelectHistory} />}
  {tab === 'alerts' && <div data-testid="alerts-slot" />}
</div>
```
（顶部补 `import { AppPage, InlineAlert, Loading } from '../components/common'`、`import { cn } from '../utils/cn'`；header 的 `onRefreshAnalysis`/`onBuildAlert` 先传占位 `() => {}`，P4 接线。）

- [ ] **Step 4: 跑验证通过 + eslint + tsc**（含 P0.2/P2.1 既有断言）。
- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/pages/StockWorkstationPage.tsx apps/dsa-web/src/pages/__tests__/StockWorkstationPage.test.tsx
git commit -m "feat: 工作台 tabs 装配(信号/报告懒取2跳/历史点选)(P3,容器B)"
```

---

# P4 · 动作接线（刷新分析 / 建告警）

### Task P4.1：AlertRuleForm 增可选 lockedTarget（预填+锁定单标的）

**Files**
- Modify: `apps/dsa-web/src/components/alerts/AlertRuleForm.tsx`
- Modify/Create: `apps/dsa-web/src/components/alerts/__tests__/AlertRuleForm.test.tsx`

- [ ] **Step 1: 写失败测试**（lockedTarget 时：target 预填且 onSubmit 带该 target + single_symbol；不破坏无 lockedTarget 的既有行为）

```tsx
// 追加到（或新建）AlertRuleForm.test.tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AlertRuleForm } from '../AlertRuleForm';

it('prefills and locks target when lockedTarget is set', async () => {
  const onSubmit = vi.fn(async () => true);
  render(<AlertRuleForm onSubmit={onSubmit} lockedTarget="600519" />);
  // 提交（用默认 alertType/参数）后，payload.target 锁定为 600519、scope=single_symbol
  fireEvent.click(screen.getByRole('button', { name: /创建/ }));
  expect(onSubmit).toHaveBeenCalled();
  const payload = onSubmit.mock.calls[0][0];
  expect(payload.target).toBe('600519');
  expect(payload.targetScope).toBe('single_symbol');
});
```
> 实现期：若 form 提交前有必填校验（如 price），测试需按真实必填项补字段后再断言 target 锁定。先读 `AlertRuleForm.tsx` 现有校验，调整测试输入使其能提交。

- [ ] **Step 2: 跑验证失败**。
- [ ] **Step 3: 实现**（读现有 `AlertRuleForm.tsx`，加可选 prop；`lockedTarget` 存在时：初始 `target=lockedTarget`、`targetScope='single_symbol'`，渲染只读 target（隐藏 scope 选择），提交 payload 强制这两项）

```tsx
// AlertRuleFormProps 追加：
//   lockedTarget?: string;
// 组件内：const isLocked = !!lockedTarget;
// 初始化 target/targetScope 用 lockedTarget（若提供）。
// renderTargetControl：isLocked 时渲染只读展示「标的：{lockedTarget}」，不渲染 scope 选择器。
// 构造 payload 时：target: isLocked ? lockedTarget : target, targetScope: isLocked ? 'single_symbol' : targetScope。
```
> 保持无 `lockedTarget` 时**逐字节等价**于现状（默认 undefined 分支）。`AlertsPage` 不传该 prop，行为不变。

- [ ] **Step 4: 跑验证通过**（含 AlertsPage 既有 form 测试若有）+ eslint + tsc。
- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/components/alerts/AlertRuleForm.tsx apps/dsa-web/src/components/alerts/__tests__/AlertRuleForm.test.tsx
git commit -m "feat: AlertRuleForm 增可选 lockedTarget(预填锁定单标的,默认行为不变)(P4,容器B)"
```

### Task P4.2：StockAlertsPanel（告警 tab）

**Files**
- Create: `apps/dsa-web/src/components/workstation/StockAlertsPanel.tsx`
- Create: `apps/dsa-web/src/components/workstation/__tests__/StockAlertsPanel.test.tsx`

设计：上方 `AlertRuleForm lockedTarget={code}`（提交调 `alertsApi.createRule`，成功 `InlineAlert` 反馈并刷新列表）；下方本股规则只读列表（`alertsApi.listRules({ target: code, targetScope: 'single_symbol' })`，显示规则名/类型/启用态）；底部「在告警页管理 →」链接 `/alerts`。

- [ ] **Step 1: 写失败测试**（创建调用 createRule(target=code)；列表渲染本股规则）

```tsx
// StockAlertsPanel.test.tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
const { createRule, listRules } = vi.hoisted(() => ({ createRule: vi.fn(), listRules: vi.fn() }));
vi.mock('../../../api/alerts', () => ({ alertsApi: { createRule, listRules } }));
import { StockAlertsPanel } from '../StockAlertsPanel';

const wrap = (ui: React.ReactNode) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe('StockAlertsPanel', () => {
  afterEach(() => { createRule.mockReset(); listRules.mockReset(); });
  it('lists existing rules for the code', async () => {
    listRules.mockResolvedValueOnce({ items: [{ id: 1, name: '600519 价格上穿', target: '600519', alertType: 'price_cross', targetScope: 'single_symbol', parameters: {}, severity: 'info', enabled: true, source: 'manual' }], total: 1, page: 1, pageSize: 20 });
    wrap(<StockAlertsPanel code="600519" />);
    expect(await screen.findByText('600519 价格上穿')).toBeInTheDocument();
    expect(listRules).toHaveBeenCalledWith({ target: '600519', targetScope: 'single_symbol' });
  });
  it('creates a rule prefilled to the code', async () => {
    listRules.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
    createRule.mockResolvedValueOnce({ id: 9 });
    wrap(<StockAlertsPanel code="600519" />);
    fireEvent.click(await screen.findByRole('button', { name: /创建/ }));
    await waitFor(() => expect(createRule).toHaveBeenCalled());
    expect(createRule.mock.calls[0][0].target).toBe('600519');
  });
});
```
> 同 P4.1：若 form 有必填项，测试先补字段再断言。

- [ ] **Step 2: 跑验证失败**。
- [ ] **Step 3: 实现**

```tsx
// apps/dsa-web/src/components/workstation/StockAlertsPanel.tsx
import { useCallback, useEffect, useState } from 'react';
import type React from 'react';
import { Link } from 'react-router-dom';
import { alertsApi } from '../../api/alerts';
import type { AlertRuleCreateRequest, AlertRuleItem } from '../../types/alerts';
import { AlertRuleForm } from '../alerts/AlertRuleForm';
import { InlineAlert, Loading } from '../common';

export const StockAlertsPanel: React.FC<{ code: string }> = ({ code }) => {
  const [rules, setRules] = useState<AlertRuleItem[] | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [created, setCreated] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { const r = await alertsApi.listRules({ target: code, targetScope: 'single_symbol' }); setRules(r.items); }
    catch { setRules([]); }
  }, [code]);
  useEffect(() => { void load(); }, [load]);

  const onSubmit = useCallback(async (payload: AlertRuleCreateRequest) => {
    setSubmitting(true); setCreated(null);
    try { await alertsApi.createRule(payload); setCreated('告警规则已创建'); await load(); return true; }
    finally { setSubmitting(false); }
  }, [load]);

  return (
    <div className="space-y-4 text-sm">
      {created && <InlineAlert variant="success" message={created} />}
      <AlertRuleForm onSubmit={onSubmit} isSubmitting={submitting} lockedTarget={code} />
      <div>
        <div className="mb-2 text-xs uppercase text-secondary-text">本股告警规则</div>
        {rules === null ? <Loading label="正在加载告警" />
          : rules.length === 0 ? <div className="text-secondary-text">尚无告警，新建一条。</div>
          : <ul className="divide-y divide-border/60">{rules.map((r) => (
              <li key={r.id} className="flex items-center justify-between py-2">
                <span className="text-foreground">{r.name}</span>
                <span className="text-xs text-secondary-text">{r.alertType}{r.enabled ? '' : ' · 已停用'}</span>
              </li>))}</ul>}
      </div>
      <Link to="/alerts" className="text-xs text-secondary-text underline">在告警页管理 →</Link>
    </div>
  );
};
```

- [ ] **Step 4: 跑验证通过 + eslint + tsc**。
- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/components/workstation/StockAlertsPanel.tsx apps/dsa-web/src/components/workstation/__tests__/StockAlertsPanel.test.tsx
git commit -m "feat: 工作台告警面板(预填创建+本股规则列表)(P4,容器B)"
```

### Task P4.3：页面接线 刷新分析 + 告警 tab

**Files**
- Modify: `apps/dsa-web/src/pages/StockWorkstationPage.tsx`
- Modify: `apps/dsa-web/src/pages/__tests__/StockWorkstationPage.test.tsx`

- [ ] **Step 1: 追加失败测试**（点 header「刷新分析」→ analyzeAsync + 轮询 getStatus 完成后刷新报告；告警 tab 渲染 StockAlertsPanel）

```tsx
// 追加 mocks：
vi.mock('../../components/workstation/StockAlertsPanel', () => ({ StockAlertsPanel: () => <div data-testid="alerts-panel" /> }));
const { analyzeAsync, getStatus } = vi.hoisted(() => ({ analyzeAsync: vi.fn(), getStatus: vi.fn() }));
vi.mock('../../api/analysis', () => ({ analysisApi: { analyzeAsync, getStatus }, DuplicateTaskError: class extends Error { existingTaskId = 'T1'; } }));
// header mock 需暴露 onRefreshAnalysis 触发：
vi.mock('../../components/workstation/StockWorkstationHeader', () => ({ StockWorkstationHeader: (p: { code: string; onRefreshAnalysis: () => void }) => (<div data-testid="ws-header">{p.code}<button onClick={p.onRefreshAnalysis}>刷新分析</button></div>) }));

it('refresh analysis triggers analyze + polls + reloads report', async () => {
  analyzeAsync.mockResolvedValueOnce({ taskId: 'T1', status: 'processing' });
  getStatus.mockResolvedValueOnce({ taskId: 'T1', status: 'completed', result: { report: { meta: { id: 9, stockCode: '600519', stockName: 'x', queryId: 'q', reportType: 'detailed', createdAt: 'x' }, summary: {} } } });
  renderAt('/stock/600519');
  fireEvent.click(screen.getByRole('button', { name: /刷新分析/ }));
  await waitFor(() => expect(analyzeAsync).toHaveBeenCalledWith(expect.objectContaining({ stockCode: '600519', forceRefresh: true })));
  await waitFor(() => expect(getStatus).toHaveBeenCalledWith('T1'));
});

it('alerts tab renders the alerts panel', () => {
  renderAt('/stock/600519');
  fireEvent.click(screen.getByRole('tab', { name: /告警/ }));
  expect(screen.getByTestId('alerts-panel')).toBeInTheDocument();
});
```
> 轮询测试：实现用真实 `setTimeout` 轮询时，测试需 `vi.useFakeTimers()` 或让实现首次轮询无延迟。**实现建议**：首次 `getStatus` 立即调用（无前置 delay），后续才 delay；测试只 mock 一次 completed 即可命中。

- [ ] **Step 2: 跑验证失败**。
- [ ] **Step 3: 实现**（页面加 `refreshing` 态 + `runAnalysis()`；告警 tab 渲染 `StockAlertsPanel`；header 的 `onBuildAlert` 切到告警 tab）

```tsx
import { analysisApi, DuplicateTaskError } from '../api/analysis';
import { StockAlertsPanel } from '../components/workstation/StockAlertsPanel';

const [refreshing, setRefreshing] = useState(false);

const pollUntilDone = useCallback(async (taskId: string) => {
  // 首次立即查，之后每 2s（最多 ~60s）；完成刷新报告
  for (let i = 0; i < 30; i += 1) {
    const st = await analysisApi.getStatus(taskId);
    if (st.status === 'completed') { if (st.result?.report) setReport(st.result.report); setTab('report'); return; }
    if (st.status === 'failed') { setReportError(st.error ?? '分析失败'); setTab('report'); return; }
    await new Promise((r) => { window.setTimeout(r, 2000); });
  }
}, []);

const runAnalysis = useCallback(async () => {
  setRefreshing(true);
  try {
    const resp = await analysisApi.analyzeAsync({ stockCode: code, reportType: 'detailed', forceRefresh: true });
    const taskId = 'taskId' in resp ? resp.taskId : undefined;
    if (taskId) await pollUntilDone(taskId);
  } catch (e) {
    if (e instanceof DuplicateTaskError) { await pollUntilDone(e.existingTaskId); }
    else { setReportError('发起分析失败'); setTab('report'); }
  } finally { setRefreshing(false); }
}, [code, pollUntilDone]);

// header：onRefreshAnalysis={() => void runAnalysis()} refreshing={refreshing} onBuildAlert={() => setTab('alerts')}
// 告警 tab：{tab === 'alerts' && <StockAlertsPanel code={code} />}
```
> `analyzeAsync` 返回可能是单股 `TaskAccepted` 或批量 `BatchTaskAcceptedResponse`；单股调用取 `resp.taskId`。实现期核实返回判别（`'taskId' in resp`）。

- [ ] **Step 4: 跑验证通过 + eslint + tsc**（含既有断言）。
- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/pages/StockWorkstationPage.tsx apps/dsa-web/src/pages/__tests__/StockWorkstationPage.test.tsx
git commit -m "feat: 工作台接线刷新分析(analyzeAsync+轮询)与告警 tab(P4,容器B)"
```

---

# P5 · 入口链接 + 门禁 + 文档

### Task P5.1：从看板行 / K 线抽屉 / 首页报告加「在工作台打开」入口

**Files**
- Modify: `apps/dsa-web/src/components/board/SignalBoardGroup.tsx`（每行加跳转，需 `useNavigate`；保留原 row→drawer 不变）
- Modify: `apps/dsa-web/src/components/kline/KLineDrawer.tsx`（头部加链接，跳转并 onClose）
- Modify: `apps/dsa-web/src/components/report/ReportOverview.tsx`（加链接，用 `meta.stockCode`）
- Modify 对应测试（断言入口存在且跳 `/stock/<code>`）

- [ ] **Step 1: 写/改失败测试**（各加一条：渲染「工作台」入口、点击 navigate 到 `/stock/<code>`）。看板行需注意：点行原本开抽屉，新入口是行内独立元素（`stopPropagation`，不触发行点击）。用 `MemoryRouter` + mock `useNavigate`：

```tsx
// 例：SignalBoardGroup 测试追加
import * as router from 'react-router-dom';
it('opens workstation without triggering the row drawer', () => {
  const navigate = vi.fn();
  vi.spyOn(router, 'useNavigate').mockReturnValue(navigate);
  const onRowClick = vi.fn();
  render(<MemoryRouter><table><tbody>{/* 渲染含一行 */}</tbody></table></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: /工作台/ }));
  expect(navigate).toHaveBeenCalledWith('/stock/600519');
  expect(onRowClick).not.toHaveBeenCalled();   // 不连带触发行→抽屉
});
```
> 实现期按各组件现状精确编写（SignalBoardGroup 用 `useNavigate`；抽屉/ReportOverview 用 `Link`/`useNavigate`）。

- [ ] **Step 2: 跑验证失败**。
- [ ] **Step 3: 实现**：
  - `SignalBoardGroup`：行内加一个小 `button`「工作台↗」，`onClick={(e)=>{ e.stopPropagation(); navigate('/stock/'+e.code) }}`（`useNavigate`）。行原 `onClick` 开抽屉不变。
  - `KLineDrawer`：标题区加 `Link to={'/stock/'+stockCode} onClick={onClose}`「在工作台打开 ↗」。
  - `ReportOverview`：用 `meta.stockCode` 加 `Link to={'/stock/'+meta.stockCode}`「在工作台打开 ↗」。
- [ ] **Step 4: 跑验证通过 + eslint + tsc**（含被改组件既有测试）。
- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/components/board/SignalBoardGroup.tsx apps/dsa-web/src/components/kline/KLineDrawer.tsx apps/dsa-web/src/components/report/ReportOverview.tsx apps/dsa-web/src/components/board/__tests__/ apps/dsa-web/src/components/report/__tests__/ apps/dsa-web/src/components/kline/__tests__/
git commit -m "feat: 看板行/K线抽屉/首页报告新增「在工作台打开」入口(P5,容器B)"
```

### Task P5.2：全量门禁 + CHANGELOG + 专题文档

**Files**
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平追加一行）
- Create: `docs/stock-workstation.md`（专题）

- [ ] **Step 1: 前端全量门禁（无空格 worktree）**

Run：`cd /root/dsa-stock-workstation/apps/dsa-web && npx vitest run && npx eslint . && npm run build` → 全绿、build 产出（含 `StockWorkstationPage` chunk）。后端无改动 → 免 ci_gate（如 P2 采纳错误边界抽取也仅前端）。

- [ ] **Step 2: CHANGELOG `[Unreleased]` 追加一行（扁平、无类目标题）**

```
- [新功能] 新增「个股工作台」(容器B)：可常驻可导航的单股深度页 /stock/:code，汇聚 K 线+量价信号+LLM 报告+历史，并提供页内动作(加自选/刷新分析/为该股建告警/复制链接)；从信号看板行、K 线抽屉、首页报告可「在工作台打开」。复用现有组件与端点，新增前端 stocksApi.getQuote 客户端，无新增后端端点
```

- [ ] **Step 3: 专题文档 `docs/stock-workstation.md`**：定位（单股深度页/动作枢纽）、路由与入口、布局（图常驻+4 tab）、各 tab 数据来源与懒取、报告 2 跳、刷新分析异步+轮询、建告警 lockedTarget、与 A(抽屉)/C(看板)/首页的边界（纯新增共存）、已知局限（无独立新闻 tab 因含于报告、quote 为薄客户端、命中率沿用 L3 口径）。

- [ ] **Step 4: Commit**

```bash
git add docs/CHANGELOG.md docs/stock-workstation.md
git commit -m "docs: 个股工作台(容器B) CHANGELOG 与专题文档(P5)"
```

---

## 验证矩阵（交付说明）
- **改了什么**：新增 `/stock/:code` 工作台页（行情头 + 复用 KLineChartPanel + 信号/报告/历史/告警 tab）；4 页内动作（自选/刷新分析/建告警/复制链接，全复用端点）；`stocksApi.getQuote`+`StockQuote` 类型；`AlertRuleForm` 增可选 `lockedTarget`；看板行/抽屉/首页报告加「在工作台打开」入口。
- **为什么**：兑现容器 B（终态）——把散落的单股 surface 汇聚为可导航深度页 + 动作枢纽，复用 A/C/首页，不造平行链、不动现有行为。
- **验证情况**：前端 `vitest`（页 + 4 面板 + header + api + AlertRuleForm 回归）+ `eslint .` + `npm run build`；后端无改动。无空格 worktree。
- **未验证项**：真实后端下 quote/分析轮询/告警创建端到端需 `npm run dev` 人工目检（沿用 A/C verify）。
- **风险点**：报告 2 跳延迟（懒取缓解）；刷新分析消耗 LLM token（force refresh，进行中态/防重复）；`AlertRuleForm` 改动（可选 prop，默认行为不变 + 回归测试守住）；首个参数路由（已核实 Shell/RouteOutletBoundary 支持）。
- **回滚方式**：纯前端增量——回退工作台页/面板/路由/入口链接即可；`getQuote` 与 `lockedTarget` 为 additive，可保留或一并回退。

---

## 执行方式
沿用 A/C：**Subagent-Driven**（每任务 fresh 实现 subagent + 两段 review），无空格**持久** worktree（`/root/dsa-stock-workstation`，前端 `npm ci` + 全量门禁；勿用 /tmp）。
