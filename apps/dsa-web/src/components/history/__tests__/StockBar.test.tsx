import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { StockBar } from '../StockBar';
import type { StockBarItem } from '../../../types/analysis';

const items: StockBarItem[] = [
  {
    id: 1,
    stockCode: '600519',
    stockName: '贵州茅台',
    sentimentScore: 60,
    operationAdvice: '观望',
    analysisCount: 1,
    lastAnalysisTime: '2026-05-31T04:52:00Z',
  },
];

describe('StockBar onViewKline wiring', () => {
  it('forwards onViewKline from the item button to the parent handler', () => {
    const onViewKline = vi.fn();
    render(
      <StockBar
        items={items}
        isLoading={false}
        onItemClick={vi.fn()}
        onViewKline={onViewKline}
      />,
    );

    const actions = screen.getByTestId('history-card-actions');
    fireEvent.click(within(actions).getByRole('button', { name: /查看 .* K 线/ }));

    expect(onViewKline).toHaveBeenCalledWith('600519', '贵州茅台');
  });

  it('omits the K线 button when no onViewKline is provided', () => {
    render(
      <StockBar
        items={items}
        isLoading={false}
        onItemClick={vi.fn()}
      />,
    );

    expect(
      within(screen.getByTestId('history-card-actions')).queryByRole('button', { name: /K 线/ }),
    ).not.toBeInTheDocument();
  });
});
