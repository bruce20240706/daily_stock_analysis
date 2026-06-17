import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
const { createRule, listRules } = vi.hoisted(() => ({ createRule: vi.fn(), listRules: vi.fn() }));
vi.mock('../../../api/alerts', () => ({ alertsApi: { createRule, listRules } }));
import { StockAlertsPanel } from '../StockAlertsPanel';

const wrap = (ui: React.ReactNode) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe('StockAlertsPanel', () => {
  beforeEach(() => { createRule.mockClear(); listRules.mockClear(); });
  afterEach(() => { createRule.mockReset(); listRules.mockReset(); });

  it('lists existing rules for the code', async () => {
    listRules.mockResolvedValueOnce({ items: [{ id: 1, name: '600519 价格上穿', target: '600519', alertType: 'price_cross', targetScope: 'single_symbol', parameters: {}, severity: 'info', enabled: true, source: 'manual' }], total: 1, page: 1, pageSize: 20 });
    wrap(<StockAlertsPanel code="600519" />);
    expect(await screen.findByText('600519 价格上穿')).toBeInTheDocument();
    expect(listRules).toHaveBeenCalledWith({ target: '600519', targetScope: 'single_symbol' });
  });

  it('creates a rule prefilled to the code and reloads list', async () => {
    listRules.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
    createRule.mockResolvedValueOnce({ id: 9 });
    wrap(<StockAlertsPanel code="600519" />);
    // price_cross is the default alertType and requires a price > 0 before submit
    const priceInput = await screen.findByLabelText('价格阈值');
    fireEvent.change(priceInput, { target: { value: '1800' } });
    fireEvent.click(screen.getByRole('button', { name: /创建/ }));
    await waitFor(() => expect(createRule).toHaveBeenCalled());
    expect(createRule.mock.calls[0][0].target).toBe('600519');
    // mount load + post-create reload = 2
    await waitFor(() => expect(listRules).toHaveBeenCalledTimes(2));
  });

  it('shows error banner and keeps form on createRule failure', async () => {
    listRules.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
    createRule.mockRejectedValueOnce(new Error('boom'));
    wrap(<StockAlertsPanel code="600519" />);
    const priceInput = await screen.findByLabelText('价格阈值');
    fireEvent.change(priceInput, { target: { value: '1800' } });
    fireEvent.click(screen.getByRole('button', { name: /创建/ }));
    expect(await screen.findByText('创建告警失败，请重试')).toBeInTheDocument();
  });

  it('shows 告警规则加载失败 warning when listRules rejects', async () => {
    listRules.mockRejectedValueOnce(new Error('boom'));
    wrap(<StockAlertsPanel code="600519" />);
    expect(await screen.findByText('告警规则加载失败')).toBeInTheDocument();
  });
});
