import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../components/kline/KLineChartPanel', () => ({ KLineChartPanel: () => <div data-testid="chart" /> }));
vi.mock('../../components/workstation/StockWorkstationHeader', () => ({ StockWorkstationHeader: (p: { code: string }) => <div data-testid="ws-header">{p.code}</div> }));
vi.mock('../../hooks/useWatchlist', () => ({ useWatchlist: () => ({ isInWatchlist: () => false, toggleWatchlist: vi.fn(), isActioning: false, actionMessage: null, watchlistCodes: [], isLoading: false, addToWatchlist: vi.fn(), removeFromWatchlist: vi.fn(), refresh: vi.fn() }) }));

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
