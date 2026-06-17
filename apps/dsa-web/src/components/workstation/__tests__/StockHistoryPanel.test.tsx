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
