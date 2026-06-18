import { beforeEach, describe, expect, it, vi } from 'vitest';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../index', () => ({ default: { get } }));

describe('getKlineHistory period', () => {
  beforeEach(() => {
    get.mockReset();
    get.mockResolvedValue({ data: { data: [] } });
  });

  it('passes explicit period and days to /history', async () => {
    const { stocksApi } = await import('../stocks');
    await stocksApi.getKlineHistory('600519', 365, 'weekly');
    expect(get).toHaveBeenCalledWith(
      '/api/v1/stocks/600519/history',
      { params: { days: 365, period: 'weekly' } },
    );
  });

  it('defaults period to daily when only code and days are given', async () => {
    const { stocksApi } = await import('../stocks');
    await stocksApi.getKlineHistory('600519', 120);
    expect(get.mock.calls[0][1]).toEqual({ params: { days: 120, period: 'daily' } });
  });

  it('defaults both days and period when only code is given', async () => {
    const { stocksApi } = await import('../stocks');
    await stocksApi.getKlineHistory('600519');
    expect(get.mock.calls[0][1]).toEqual({ params: { days: 120, period: 'daily' } });
  });
});
