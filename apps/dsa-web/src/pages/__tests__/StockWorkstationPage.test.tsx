import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../components/kline/KLineChartPanel', () => ({ default: () => <div data-testid="chart" /> }));
vi.mock('../../components/workstation/StockWorkstationHeader', () => ({ StockWorkstationHeader: (p: { code: string; onRefreshAnalysis: () => void; onBuildAlert: () => void }) => <div data-testid="ws-header">{p.code}<button onClick={p.onRefreshAnalysis}>刷新分析</button><button onClick={p.onBuildAlert}>建告警</button></div> }));
vi.mock('../../hooks/useWatchlist', () => ({ useWatchlist: () => ({ isInWatchlist: () => false, toggleWatchlist: vi.fn(), isActioning: false, actionMessage: null, watchlistCodes: [], isLoading: false, addToWatchlist: vi.fn(), removeFromWatchlist: vi.fn(), refresh: vi.fn() }) }));
vi.mock('../../components/workstation/StockSignalsPanel', () => ({ StockSignalsPanel: () => <div data-testid="sig-panel" /> }));
vi.mock('../../components/workstation/StockHistoryPanel', () => ({ StockHistoryPanel: (p: { onSelect: (id: number) => void }) => <button data-testid="hist-row" onClick={() => p.onSelect(7)}>hist</button> }));
vi.mock('../../components/report/ReportSummary', () => ({ ReportSummary: (p: { data: { meta: { id?: number } } }) => <div data-testid="report">{p.data.meta.id}</div> }));
vi.mock('../../components/workstation/StockAlertsPanel', () => ({ StockAlertsPanel: () => <div data-testid="alerts-panel" /> }));
const { getList, getDetail } = vi.hoisted(() => ({ getList: vi.fn(), getDetail: vi.fn() }));
vi.mock('../../api/history', () => ({ historyApi: { getList, getDetail } }));
const { analyzeAsync, getStatus } = vi.hoisted(() => ({ analyzeAsync: vi.fn(), getStatus: vi.fn() }));
vi.mock('../../api/analysis', () => ({ analysisApi: { analyzeAsync, getStatus }, DuplicateTaskError: class extends Error { existingTaskId = 'T1'; } }));

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

  it('renders the K-line chart for the code', () => {
    renderAt('/stock/600519');
    expect(screen.getByTestId('chart')).toBeInTheDocument();
  });
});

describe('StockWorkstationPage tabs', () => {
  beforeEach(() => {
    getList.mockClear();
    getDetail.mockClear();
    analyzeAsync.mockClear();
    getStatus.mockClear();
  });

  it('shows signals tab by default and switches to report (lazy 2-hop fetch)', async () => {
    getList.mockResolvedValueOnce({ total: 1, page: 1, limit: 1, items: [{ id: 7 }] });
    getDetail.mockResolvedValueOnce({ meta: { id: 7, stockCode: '600519', stockName: '贵州茅台', queryId: 'q', reportType: 'detailed', createdAt: 'x' }, summary: {} });
    renderAt('/stock/600519');
    expect(screen.getByTestId('sig-panel')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /报告/ }));
    expect(await screen.findByTestId('report')).toHaveTextContent('7');
    expect(getList).toHaveBeenCalledWith({ stockCode: '600519', limit: 1 });
    expect(getDetail).toHaveBeenCalledWith(7);
  });

  it('shows empty state when no analysis records exist, without infinite re-fetch', async () => {
    getList.mockResolvedValueOnce({ total: 0, page: 1, limit: 1, items: [] });
    renderAt('/stock/600519');
    fireEvent.click(screen.getByRole('tab', { name: /报告/ }));
    expect(await screen.findByTestId('report-empty')).toBeInTheDocument();
    // loop guard: getList called exactly once, getDetail never called
    expect(getList).toHaveBeenCalledTimes(1);
    expect(getDetail).not.toHaveBeenCalled();
  });

  it('loading a history item opens its report', async () => {
    getDetail.mockResolvedValueOnce({ meta: { id: 7, stockCode: '600519', stockName: 'x', queryId: 'q', reportType: 'detailed', createdAt: 'x' }, summary: {} });
    renderAt('/stock/600519');
    fireEvent.click(screen.getByRole('tab', { name: /历史/ }));
    fireEvent.click(screen.getByTestId('hist-row'));
    expect(await screen.findByTestId('report')).toHaveTextContent('7');
    expect(getDetail).toHaveBeenCalledWith(7);
  });

  it('refresh analysis triggers analyze + polls + reloads report', async () => {
    analyzeAsync.mockResolvedValueOnce({ taskId: 'T1', status: 'processing' });
    getStatus.mockResolvedValueOnce({ taskId: 'T1', status: 'completed', result: { report: { meta: { id: 9, stockCode: '600519', stockName: 'x', queryId: 'q', reportType: 'detailed', createdAt: 'x' }, summary: {} } } });
    renderAt('/stock/600519');
    fireEvent.click(screen.getByRole('button', { name: /刷新分析/ }));
    await waitFor(() => expect(analyzeAsync).toHaveBeenCalledWith(expect.objectContaining({ stockCode: '600519', forceRefresh: true })));
    await waitFor(() => expect(getStatus).toHaveBeenCalledWith('T1'));
    expect(await screen.findByTestId('report')).toHaveTextContent('9');
  });

  it('alerts tab renders the alerts panel', () => {
    renderAt('/stock/600519');
    fireEvent.click(screen.getByRole('tab', { name: /告警/ }));
    expect(screen.getByTestId('alerts-panel')).toBeInTheDocument();
  });

  it('failed status surfaces error message', async () => {
    analyzeAsync.mockResolvedValueOnce({ taskId: 'T1', status: 'processing' });
    getStatus.mockResolvedValueOnce({ taskId: 'T1', status: 'failed', error: '引擎错误' });
    renderAt('/stock/600519');
    fireEvent.click(screen.getByRole('button', { name: /刷新分析/ }));
    expect(await screen.findByText('引擎错误')).toBeInTheDocument();
  });

  it('completed without report shows no-report message', async () => {
    analyzeAsync.mockResolvedValueOnce({ taskId: 'T1', status: 'processing' });
    getStatus.mockResolvedValueOnce({ taskId: 'T1', status: 'completed', result: undefined });
    renderAt('/stock/600519');
    fireEvent.click(screen.getByRole('button', { name: /刷新分析/ }));
    expect(await screen.findByText('分析完成但未返回报告内容')).toBeInTheDocument();
  });

  it('DuplicateTaskError polls existing task and shows its report', async () => {
    const { DuplicateTaskError } = await import('../../api/analysis');
    analyzeAsync.mockRejectedValueOnce(new DuplicateTaskError('600519', 'T1'));
    getStatus.mockResolvedValueOnce({ taskId: 'T1', status: 'completed', result: { report: { meta: { id: 5, stockCode: '600519', stockName: 'x', queryId: 'q', reportType: 'detailed', createdAt: 'x' }, summary: {} } } });
    renderAt('/stock/600519');
    fireEvent.click(screen.getByRole('button', { name: /刷新分析/ }));
    expect(await screen.findByTestId('report')).toHaveTextContent('5');
    expect(getStatus).toHaveBeenCalledWith('T1');
  });

  // T1 — 换股触发 key 重挂，重置全部页面状态（finding #1）。
  // 在同一个 Router 内从 /stock/600519 导航到 /stock/000001（命中 React Router 实例复用 → key 重挂）。
  it('remounts and resets page state when the code changes (key={code})', async () => {
    getList.mockResolvedValue({ total: 0, page: 1, limit: 1, items: [] });
    const Nav = () => {
      const navigate = useNavigate();
      return <button onClick={() => navigate('/stock/000001')}>go-000001</button>;
    };
    render(
      <MemoryRouter initialEntries={['/stock/600519']}>
        <Nav />
        <Routes><Route path="/stock/:code" element={<StockWorkstationPage />} /></Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId('ws-header')).toHaveTextContent('600519');
    // 切到「报告」tab，使 tab 状态偏离默认值
    fireEvent.click(screen.getByRole('tab', { name: /报告/ }));
    expect(await screen.findByTestId('report-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('sig-panel')).not.toBeInTheDocument();
    // 换股
    fireEvent.click(screen.getByRole('button', { name: 'go-000001' }));
    // header 反映新 code，且页面回到默认「信号」tab（report 区不再渲染）→ 证明状态被重置
    expect(screen.getByTestId('ws-header')).toHaveTextContent('000001');
    expect(screen.getByTestId('sig-panel')).toBeInTheDocument();
    expect(screen.queryByTestId('report')).not.toBeInTheDocument();
    expect(screen.queryByTestId('report-empty')).not.toBeInTheDocument();
  });

  // T2 — 多轮轮询后完成（finding #3）。
  it('polls multiple rounds before completed', async () => {
    vi.useFakeTimers();
    try {
      analyzeAsync.mockResolvedValueOnce({ taskId: 'T1', status: 'processing' });
      getStatus
        .mockResolvedValueOnce({ taskId: 'T1', status: 'processing' })
        .mockResolvedValueOnce({ taskId: 'T1', status: 'completed', result: { report: { meta: { id: 11, stockCode: '600519', stockName: 'x', queryId: 'q', reportType: 'detailed', createdAt: 'x' }, summary: {} } } });
      renderAt('/stock/600519');
      fireEvent.click(screen.getByRole('button', { name: /刷新分析/ }));
      // 第 1 轮 getStatus（processing）后进入 2s sleep。
      // 用 fake timers 时不能用 findBy*（其内部依赖真实定时器会挂死），
      // advanceTimersByTimeAsync 会刷掉微任务与 React 更新，断言用同步 getBy*。
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(getStatus).toHaveBeenCalledTimes(1);
      // 驱动 2s sleep → 第 2 轮 getStatus（completed）
      await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
      expect(getStatus).toHaveBeenCalledTimes(2);
      expect(screen.getByTestId('report')).toHaveTextContent('11');
    } finally {
      vi.useRealTimers();
    }
  });

  // T3 — 轮询耗尽超时（finding #3）。
  it('surfaces timeout message when polling budget is exhausted', async () => {
    vi.useFakeTimers();
    try {
      analyzeAsync.mockResolvedValueOnce({ taskId: 'T1', status: 'processing' });
      getStatus.mockResolvedValue({ taskId: 'T1', status: 'processing' });
      renderAt('/stock/600519');
      fireEvent.click(screen.getByRole('button', { name: /刷新分析/ }));
      // 驱动满 120 轮（每轮 1 次 getStatus + 2s sleep）；fake timers 下用同步 getBy*。
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
        for (let i = 0; i < 120; i += 1) {
          await vi.advanceTimersByTimeAsync(2000);
        }
      });
      expect(getStatus).toHaveBeenCalledTimes(120);
      expect(screen.getByText('分析超时，请稍后重试')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  // T4 — header「建告警」→ 切到告警 tab（finding #13）。
  it('header 建告警 button switches to the alerts tab', () => {
    renderAt('/stock/600519');
    fireEvent.click(screen.getByRole('button', { name: '建告警' }));
    expect(screen.getByTestId('alerts-panel')).toBeInTheDocument();
  });
});
