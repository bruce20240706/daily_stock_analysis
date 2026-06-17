import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../components/kline/KLineChartPanel', () => ({ KLineChartPanel: () => <div data-testid="chart" /> }));
vi.mock('../../components/workstation/StockWorkstationHeader', () => ({ StockWorkstationHeader: (p: { code: string }) => <div data-testid="ws-header">{p.code}</div> }));
vi.mock('../../hooks/useWatchlist', () => ({ useWatchlist: () => ({ isInWatchlist: () => false, toggleWatchlist: vi.fn(), isActioning: false, actionMessage: null, watchlistCodes: [], isLoading: false, addToWatchlist: vi.fn(), removeFromWatchlist: vi.fn(), refresh: vi.fn() }) }));
vi.mock('../../components/workstation/StockSignalsPanel', () => ({ StockSignalsPanel: () => <div data-testid="sig-panel" /> }));
vi.mock('../../components/workstation/StockHistoryPanel', () => ({ StockHistoryPanel: (p: { onSelect: (id: number) => void }) => <button data-testid="hist-row" onClick={() => p.onSelect(7)}>hist</button> }));
vi.mock('../../components/report/ReportSummary', () => ({ ReportSummary: (p: { data: { meta: { id?: number } } }) => <div data-testid="report">{p.data.meta.id}</div> }));
const { getList, getDetail } = vi.hoisted(() => ({ getList: vi.fn(), getDetail: vi.fn() }));
vi.mock('../../api/history', () => ({ historyApi: { getList, getDetail } }));

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
});
