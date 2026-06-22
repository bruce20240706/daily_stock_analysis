import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import type { BoardEntry } from '../../../types/kline';
import { SignalBoard } from '../SignalBoard';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

const mk = (over: Partial<BoardEntry>): BoardEntry => ({
  code: 'X', name: 'X名', market: 'CN', actionGroup: 'buy', ruleDirection: 'bullish',
  llmDirection: 'bullish', consistency: 'consistent', keySignals: ['volume_breakout'],
  priceLines: { entry: 1700, stop: 1620, target: 1850 }, latestClose: 1660,
  hitRate: 0.62, hitSample: 18, verified: true,
  ciLow: null, ciHigh: null, baselineExcess: null,
  status: 'ok', degradedReason: null, resonance: 'none',
  horizonBars: null, signalStatus: null, planQuality: null,
  ...over,
});

describe('SignalBoard', () => {
  it('renders the action groups with titles and rows', () => {
    const entries = [
      mk({ code: '600519', name: '贵州茅台', actionGroup: 'buy' }),
      mk({ code: 'BAD', name: null, actionGroup: 'unavailable', status: 'degraded',
          ruleDirection: null, consistency: 'unknown', verified: false }),
    ];
    render(<MemoryRouter><SignalBoard entries={entries} onRowClick={vi.fn()} /></MemoryRouter>);
    // group headers render as "标题（n）" → use substring matchers
    expect(screen.getByText(/买入候选/)).toBeInTheDocument();
    expect(screen.getByText(/数据不可用/)).toBeInTheDocument();
    expect(screen.getByText('贵州茅台')).toBeInTheDocument();
    // exactly one verified badge (only the buy row is verified)
    expect(screen.getByText('已验证')).toBeInTheDocument();
  });

  it('calls onRowClick with code+name when a row is clicked', () => {
    const onRowClick = vi.fn();
    render(<MemoryRouter><SignalBoard entries={[mk({ code: '600519', name: '贵州茅台' })]} onRowClick={onRowClick} /></MemoryRouter>);
    fireEvent.click(screen.getByText('贵州茅台'));
    expect(onRowClick).toHaveBeenCalledWith('600519', '贵州茅台');
  });

  it('sorts a group by hit rate descending within group', () => {
    const entries = [
      mk({ code: 'LOW', name: 'L', hitRate: 0.3 }),
      mk({ code: 'HIGH', name: 'H', hitRate: 0.9 }),
    ];
    render(<MemoryRouter><SignalBoard entries={entries} onRowClick={vi.fn()} /></MemoryRouter>);
    const buyGroup = screen.getByTestId('group-buy');
    const rows = within(buyGroup).getAllByTestId('board-row');
    expect(rows[0]).toHaveTextContent('H');   // higher hit rate first
  });

  it('sinks no-sample (null hit-rate) rows to the bottom of a group', () => {
    const entries = [
      mk({ code: 'NOSAMPLE', name: 'NS', hitRate: null, hitSample: null }),
      mk({ code: 'HASRATE', name: 'HR', hitRate: 0.5, hitSample: 10 }),
    ];
    render(<MemoryRouter><SignalBoard entries={entries} onRowClick={vi.fn()} /></MemoryRouter>);
    const buyGroup = screen.getByTestId('group-buy');
    const rows = within(buyGroup).getAllByTestId('board-row');
    expect(rows[0]).toHaveTextContent('HR');   // has a hit rate → on top
    expect(rows[1]).toHaveTextContent('NS');   // no sample → sinks
  });

  it('surfaces degradedReason text for a degraded row', () => {
    const entries = [
      mk({ code: 'BAD', name: 'BAD名', actionGroup: 'unavailable', status: 'degraded',
           ruleDirection: null, consistency: 'unknown', verified: false,
           keySignals: [], degradedReason: '无可用历史数据' }),
    ];
    render(<MemoryRouter><SignalBoard entries={entries} onRowClick={vi.fn()} /></MemoryRouter>);
    expect(screen.getByText('无可用历史数据')).toBeInTheDocument();
  });

  it('hides empty action groups (renders nothing for a group with no entries)', () => {
    render(<MemoryRouter><SignalBoard entries={[mk({ code: '600519', name: '贵州茅台', actionGroup: 'buy' })]} onRowClick={vi.fn()} /></MemoryRouter>);
    expect(screen.queryByTestId('group-sell')).not.toBeInTheDocument();
    expect(screen.queryByTestId('group-hold')).not.toBeInTheDocument();
  });

  it('opens workstation without triggering the row drawer', () => {
    mockNavigate.mockClear();
    const onRowClick = vi.fn();
    render(
      <MemoryRouter>
        <SignalBoard entries={[mk({ code: '600519', name: '贵州茅台' })]} onRowClick={onRowClick} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: /工作台/ }));
    expect(mockNavigate).toHaveBeenCalledWith('/stock/600519');
    expect(onRowClick).not.toHaveBeenCalled();
  });

  it('shows CI band and excess in the board hit cell', () => {
    render(
      <MemoryRouter>
        <SignalBoard entries={[mk({ code: '600519', name: '贵州茅台', ciLow: 0.55, ciHigh: 0.8, baselineExcess: 0.05 })]} onRowClick={vi.fn()} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId('board-ci')).toHaveTextContent('[55%–80%]');
    expect(screen.getByTestId('board-excess')).toHaveTextContent('超额 +5pp');
  });

  it('omits CI and excess spans in the board when values are null', () => {
    render(
      <MemoryRouter>
        <SignalBoard entries={[mk({ code: '600519', name: '贵州茅台' })]} onRowClick={vi.fn()} />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId('board-ci')).not.toBeInTheDocument();
    expect(screen.queryByTestId('board-excess')).not.toBeInTheDocument();
  });

  it('encodes crypto code (containing /) in workstation navigate call', () => {
    mockNavigate.mockClear();
    render(
      <MemoryRouter>
        <SignalBoard entries={[mk({ code: 'BTC/USDT', name: 'BTC/USDT' })]} onRowClick={vi.fn()} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: /工作台/ }));
    expect(mockNavigate).toHaveBeenCalledWith('/stock/BTC%2FUSDT');
  });

  it('shows resonance badge for weekly_monthly entry', () => {
    render(
      <MemoryRouter>
        <SignalBoard entries={[mk({ code: '600519', name: '贵州茅台', resonance: 'weekly_monthly' })]} onRowClick={vi.fn()} />
      </MemoryRouter>,
    );
    const badge = screen.getByTestId('board-resonance');
    expect(badge).toHaveTextContent('共振·周月');
    expect(badge).toHaveAttribute('aria-label', expect.stringContaining('共振'));
  });

  it('omits resonance badge when resonance is none', () => {
    render(
      <MemoryRouter>
        <SignalBoard entries={[mk({ code: '600519', name: '贵州茅台', resonance: 'none' })]} onRowClick={vi.fn()} />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId('board-resonance')).not.toBeInTheDocument();
  });

  it('shows plan quality, horizon and signal status columns when set', () => {
    render(
      <MemoryRouter>
        <SignalBoard
          entries={[mk({ code: '600519', name: '贵州茅台', planQuality: 'high', signalStatus: 'aging', horizonBars: 10 })]}
          onRowClick={vi.fn()}
        />
      </MemoryRouter>,
    );
    expect(screen.getByTestId('board-plan-quality')).toHaveTextContent('high');
    expect(screen.getByTestId('board-horizon')).toHaveTextContent('窗口 10 根');
    expect(screen.getByTestId('board-signal-status')).toHaveTextContent('窗口内');
  });

  it('omits plan quality, horizon and signal status spans when null', () => {
    render(
      <MemoryRouter>
        <SignalBoard
          entries={[mk({ code: '600519', name: '贵州茅台', planQuality: null, signalStatus: null, horizonBars: null })]}
          onRowClick={vi.fn()}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId('board-plan-quality')).not.toBeInTheDocument();
    expect(screen.queryByTestId('board-horizon')).not.toBeInTheDocument();
    expect(screen.queryByTestId('board-signal-status')).not.toBeInTheDocument();
  });
});
