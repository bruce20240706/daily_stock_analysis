import { beforeEach, describe, expect, it, vi } from 'vitest';
import { stocksApi } from '../stocks';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../index', () => ({ default: { get } }));

describe('stocksApi.getQuote', () => {
  beforeEach(() => { get.mockReset(); });

  it('maps snake_case quote to camelCase and encodes the code', async () => {
    get.mockResolvedValueOnce({ data: {
      stock_code: 'BTC/USDT', stock_name: 'Bitcoin', current_price: 65000,
      change: 1200, change_percent: 1.88, open: 64000, high: 66000, low: 63500,
      prev_close: 63800, volume: 1234, amount: 80000000, update_time: '2026-06-16T15:00:00',
    }});
    const res = await stocksApi.getQuote('BTC/USDT');
    expect(get).toHaveBeenCalledWith('/api/v1/stocks/BTC%2FUSDT/quote');
    expect(res.currentPrice).toBe(65000);
    expect(res.changePercent).toBe(1.88);
    expect(res.prevClose).toBe(63800);
    expect(res.stockName).toBe('Bitcoin');
  });

  it('tolerates null optional fields', async () => {
    get.mockResolvedValueOnce({ data: {
      stock_code: '600519', stock_name: null, current_price: 1660, change: null,
      change_percent: null, open: null, high: null, low: null, prev_close: null,
      volume: null, amount: null, update_time: null,
    }});
    const res = await stocksApi.getQuote('600519');
    expect(res.currentPrice).toBe(1660);
    expect(res.change).toBeNull();
    expect(res.stockName).toBeNull();
  });
});
