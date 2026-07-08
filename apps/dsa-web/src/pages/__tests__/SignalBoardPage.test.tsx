import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { BoardEntry, SignalsBoardResponse } from '../../types/kline';

interface DrawerStubProps {
  stockCode: string;
  stockName?: string;
  isOpen: boolean;
  onClose: () => void;
}

const { getBoard, drawerProps } = vi.hoisted(() => ({
  getBoard: vi.fn(),
  drawerProps: [] as DrawerStubProps[],
}));
vi.mock('../../api/stocks', () => ({ stocksApi: { getBoard } }));
vi.mock('../../components/kline', () => ({
  KLineDrawer: (p: DrawerStubProps) => {
    drawerProps.push(p);
    return p.isOpen ? <div data-testid="kline-drawer">{p.stockCode}</div> : null;
  },
}));

import SignalBoardPage from '../SignalBoardPage';

const entry = (over: Partial<BoardEntry> = {}): BoardEntry => ({
  code: '600519', name: '贵州茅台', market: 'CN', actionGroup: 'buy', ruleDirection: 'bullish',
  llmDirection: 'bullish', consistency: 'consistent', keySignals: ['volume_breakout'],
  priceLines: { entry: 1700, stop: 1620, target: 1850 }, latestClose: 1660,
  hitRate: 0.62, hitSample: 18, verified: true, ciLow: null, ciHigh: null, baselineExcess: null,
  ciLowCorrected: null, familySize: null,
  status: 'ok', degradedReason: null, resonance: 'none',
  horizonBars: null, signalStatus: null, planQuality: null,
  ggtEligible: null, ...over });

const board = (entries: BoardEntry[]): SignalsBoardResponse => ({
  asOf: 1,
  entries,
  counts: { buy: entries.length, hold: 0, sell: 0, unavailable: 0 },
  degradedCodes: [],
});

describe('SignalBoardPage', () => {
  afterEach(() => { getBoard.mockReset(); drawerProps.length = 0; });

  it('renders board rows after fetch', async () => {
    getBoard.mockResolvedValueOnce(board([entry()]));
    render(<MemoryRouter><SignalBoardPage /></MemoryRouter>);
    expect(await screen.findByText('贵州茅台')).toBeInTheDocument();
  });

  it('shows empty-watchlist guidance', async () => {
    getBoard.mockResolvedValueOnce(board([]));
    render(<MemoryRouter><SignalBoardPage /></MemoryRouter>);
    expect(await screen.findByTestId('board-empty')).toBeInTheDocument();
  });

  it('shows an error message when the fetch fails', async () => {
    getBoard.mockRejectedValueOnce(new Error('boom'));
    render(<MemoryRouter><SignalBoardPage /></MemoryRouter>);
    expect(await screen.findByText(/加载失败/)).toBeInTheDocument();
  });

  it('opens KLineDrawer on row click', async () => {
    getBoard.mockResolvedValueOnce(board([entry()]));
    render(<MemoryRouter><SignalBoardPage /></MemoryRouter>);
    fireEvent.click(await screen.findByText('贵州茅台'));
    expect(await screen.findByTestId('kline-drawer')).toHaveTextContent('600519');
  });
});
