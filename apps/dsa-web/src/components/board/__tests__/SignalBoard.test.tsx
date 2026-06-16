import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { BoardEntry } from '../../../types/kline';
import { SignalBoard } from '../SignalBoard';

const mk = (over: Partial<BoardEntry>): BoardEntry => ({
  code: 'X', name: 'X名', market: 'CN', actionGroup: 'buy', ruleDirection: 'bullish',
  llmDirection: 'bullish', consistency: 'consistent', keySignals: ['volume_breakout'],
  priceLines: { entry: 1700, stop: 1620, target: 1850 }, latestClose: 1660,
  hitRate: 0.62, hitSample: 18, verified: true, status: 'ok', degradedReason: null, ...over,
});

describe('SignalBoard', () => {
  it('renders the action groups with titles and rows', () => {
    const entries = [
      mk({ code: '600519', name: '贵州茅台', actionGroup: 'buy' }),
      mk({ code: 'BAD', name: null, actionGroup: 'unavailable', status: 'degraded',
          ruleDirection: null, consistency: 'unknown', verified: false }),
    ];
    render(<SignalBoard entries={entries} onRowClick={vi.fn()} />);
    // group headers render as "标题（n）" → use substring matchers
    expect(screen.getByText(/买入候选/)).toBeInTheDocument();
    expect(screen.getByText(/数据不可用/)).toBeInTheDocument();
    expect(screen.getByText('贵州茅台')).toBeInTheDocument();
    // exactly one verified badge (only the buy row is verified)
    expect(screen.getByText('已验证')).toBeInTheDocument();
  });

  it('calls onRowClick with code+name when a row is clicked', () => {
    const onRowClick = vi.fn();
    render(<SignalBoard entries={[mk({ code: '600519', name: '贵州茅台' })]} onRowClick={onRowClick} />);
    fireEvent.click(screen.getByText('贵州茅台'));
    expect(onRowClick).toHaveBeenCalledWith('600519', '贵州茅台');
  });

  it('sorts a group by hit rate descending within group', () => {
    const entries = [
      mk({ code: 'LOW', name: 'L', hitRate: 0.3 }),
      mk({ code: 'HIGH', name: 'H', hitRate: 0.9 }),
    ];
    render(<SignalBoard entries={entries} onRowClick={vi.fn()} />);
    const buyGroup = screen.getByTestId('group-buy');
    const rows = within(buyGroup).getAllByTestId('board-row');
    expect(rows[0]).toHaveTextContent('H');   // higher hit rate first
  });

  it('hides empty action groups (renders nothing for a group with no entries)', () => {
    render(<SignalBoard entries={[mk({ code: '600519', name: '贵州茅台', actionGroup: 'buy' })]} onRowClick={vi.fn()} />);
    expect(screen.queryByTestId('group-sell')).not.toBeInTheDocument();
    expect(screen.queryByTestId('group-hold')).not.toBeInTheDocument();
  });
});
