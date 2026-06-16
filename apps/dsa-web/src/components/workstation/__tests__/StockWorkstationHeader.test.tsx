import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const { getQuote } = vi.hoisted(() => ({ getQuote: vi.fn() }));
vi.mock('../../../api/stocks', () => ({ stocksApi: { getQuote } }));

import { StockWorkstationHeader } from '../StockWorkstationHeader';

const watchlist = { isInWatchlist: (c: string) => c === '600519', toggleWatchlist: vi.fn(async () => {}), isActioning: false };
const baseProps = () => ({ code: '600519', watchlist, onRefreshAnalysis: vi.fn(), onBuildAlert: vi.fn() });
const fullQuote = (over = {}) => ({ stockCode: '600519', stockName: '贵州茅台', currentPrice: 1660, change: 20, changePercent: 1.22, open: null, high: null, low: null, prevClose: null, volume: null, amount: null, updateTime: null, ...over });

describe('StockWorkstationHeader', () => {
  afterEach(() => { getQuote.mockReset(); vi.restoreAllMocks(); });

  it('renders code and fetched quote (name + price + change%)', async () => {
    getQuote.mockResolvedValueOnce(fullQuote());
    render(<StockWorkstationHeader {...baseProps()} />);
    expect(screen.getByText('600519')).toBeInTheDocument();
    expect(await screen.findByText('贵州茅台')).toBeInTheDocument();
    expect(await screen.findByText(/1660/)).toBeInTheDocument();
    expect(await screen.findByText(/1\.22%/)).toBeInTheDocument();
  });

  it('toggles watchlist on star click', async () => {
    getQuote.mockResolvedValueOnce(fullQuote({ stockName: null, change: null, changePercent: null }));
    render(<StockWorkstationHeader {...baseProps()} />);
    fireEvent.click(screen.getByRole('button', { name: /自选/ }));
    await waitFor(() => expect(watchlist.toggleWatchlist).toHaveBeenCalledWith('600519'));
  });

  it('copies the workstation URL on copy click', async () => {
    getQuote.mockResolvedValueOnce(fullQuote({ stockName: null }));
    const writeText = vi.fn(async () => {});
    Object.assign(navigator, { clipboard: { writeText } });
    render(<StockWorkstationHeader {...baseProps()} />);
    fireEvent.click(screen.getByRole('button', { name: /复制链接/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(expect.stringContaining('/stock/600519')));
  });

  it('invokes refresh + build-alert callbacks', async () => {
    getQuote.mockResolvedValueOnce(fullQuote({ stockName: null }));
    const props = baseProps();
    render(<StockWorkstationHeader {...props} />);
    fireEvent.click(screen.getByRole('button', { name: /刷新分析/ }));
    fireEvent.click(screen.getByRole('button', { name: /建告警/ }));
    expect(props.onRefreshAnalysis).toHaveBeenCalled();
    expect(props.onBuildAlert).toHaveBeenCalled();
  });
});
