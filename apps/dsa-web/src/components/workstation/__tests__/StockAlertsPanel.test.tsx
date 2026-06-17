import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
const { createRule, listRules } = vi.hoisted(() => ({ createRule: vi.fn(), listRules: vi.fn() }));
vi.mock('../../../api/alerts', () => ({ alertsApi: { createRule, listRules } }));
import { StockAlertsPanel } from '../StockAlertsPanel';

const wrap = (ui: React.ReactNode) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe('StockAlertsPanel', () => {
  afterEach(() => { createRule.mockReset(); listRules.mockReset(); });

  it('lists existing rules for the code', async () => {
    listRules.mockResolvedValueOnce({ items: [{ id: 1, name: '600519 价格上穿', target: '600519', alertType: 'price_cross', targetScope: 'single_symbol', parameters: {}, severity: 'info', enabled: true, source: 'manual' }], total: 1, page: 1, pageSize: 20 });
    wrap(<StockAlertsPanel code="600519" />);
    expect(await screen.findByText('600519 价格上穿')).toBeInTheDocument();
    expect(listRules).toHaveBeenCalledWith({ target: '600519', targetScope: 'single_symbol' });
  });

  it('creates a rule prefilled to the code', async () => {
    listRules.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
    createRule.mockResolvedValueOnce({ id: 9 });
    wrap(<StockAlertsPanel code="600519" />);
    // price_cross is the default alertType and requires a price > 0 before submit
    const priceInput = await screen.findByLabelText('价格阈值');
    fireEvent.change(priceInput, { target: { value: '1800' } });
    fireEvent.click(screen.getByRole('button', { name: /创建/ }));
    await waitFor(() => expect(createRule).toHaveBeenCalled());
    expect(createRule.mock.calls[0][0].target).toBe('600519');
  });
});
