import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../components/kline/KLineChartPanel', () => ({ KLineChartPanel: () => <div data-testid="chart" /> }));
vi.mock('../../components/workstation/StockWorkstationHeader', () => ({ StockWorkstationHeader: (p: { code: string; onRefreshAnalysis: () => void }) => <div data-testid="ws-header">{p.code}<button onClick={p.onRefreshAnalysis}>刷新分析</button></div> }));
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
});
