import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { KLine } from '../../types/kline';

const get = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());

vi.mock('../index', () => ({
  default: { get, post },
}));

// 在 mock 之后再导入被测模块（其内部 import '../index' 命中上面的 mock）。
const { mapKLineDataToKLine, stocksApi } = await import('../stocks');

/** UTC+8 当日 00:00 的 epoch ms。 */
const shanghaiMidnightMs = (date: string): number =>
  Date.parse(`${date}T00:00:00+08:00`);

describe('mapKLineDataToKLine', () => {
  it('maps A-share daily bar with amount->turnover and Asia/Shanghai timestamp', () => {
    const result: KLine = mapKLineDataToKLine({
      date: '2026-01-02',
      open: 10,
      high: 11,
      low: 9.5,
      close: 10.5,
      volume: 12345,
      amount: 678900,
      change_percent: 1.2,
    });

    expect(result.timestamp).toBe(shanghaiMidnightMs('2026-01-02'));
    expect(result.open).toBe(10);
    expect(result.high).toBe(11);
    expect(result.low).toBe(9.5);
    expect(result.close).toBe(10.5);
    expect(result.volume).toBe(12345);
    expect(result.turnover).toBe(678900);
  });

  it('maps HK bar identically (date string anchored to Asia/Shanghai, not local TZ)', () => {
    const result = mapKLineDataToKLine({
      date: '2026-03-16',
      open: 300,
      high: 305,
      low: 298,
      close: 302,
      volume: 1000,
      amount: 302000,
      change_percent: -0.5,
    });

    expect(result.timestamp).toBe(shanghaiMidnightMs('2026-03-16'));
    expect(result.timestamp).toBe(Date.UTC(2026, 2, 15, 16, 0, 0));
  });

  it('maps US bar date string to the same Asia/Shanghai anchor as A/HK', () => {
    const result = mapKLineDataToKLine({
      date: '2026-06-15',
      open: 150,
      high: 152,
      low: 149,
      close: 151,
      volume: 5000,
      amount: 755000,
      change_percent: 0.7,
    });

    expect(result.timestamp).toBe(shanghaiMidnightMs('2026-06-15'));
  });

  it('defaults missing volume and amount to zero', () => {
    const result = mapKLineDataToKLine({
      date: '2026-01-02',
      open: 10,
      high: 11,
      low: 9.5,
      close: 10.5,
      volume: null,
      amount: null,
      change_percent: null,
    });

    expect(result.volume).toBe(0);
    expect(result.turnover).toBe(0);
  });
});

describe('stocksApi.getKlineHistory', () => {
  beforeEach(() => {
    get.mockReset();
  });

  it('requests /history with days and maps rows to KLine[]', async () => {
    get.mockResolvedValueOnce({
      data: {
        stock_code: '600519',
        stock_name: '贵州茅台',
        period: 'daily',
        data: [
          { date: '2026-01-02', open: 10, high: 11, low: 9.5, close: 10.5, volume: 100, amount: 1000, change_percent: 1.2 },
          { date: '2026-01-03', open: 10.5, high: 12, low: 10.4, close: 11.8, volume: 200, amount: 2300, change_percent: 12.3 },
        ],
      },
    });

    const result = await stocksApi.getKlineHistory('600519', 120);

    expect(get).toHaveBeenCalledWith('/api/v1/stocks/600519/history', { params: { days: 120 } });
    expect(result).toHaveLength(2);
    expect(result[0].timestamp).toBe(shanghaiMidnightMs('2026-01-02'));
    expect(result[0].turnover).toBe(1000);
    expect(result[1].close).toBe(11.8);
  });

  it('defaults days to 120 and encodes crypto codes containing slash', async () => {
    get.mockResolvedValueOnce({ data: { data: [] } });

    await stocksApi.getKlineHistory('BTC/USDT');

    expect(get).toHaveBeenCalledWith('/api/v1/stocks/BTC%2FUSDT/history', { params: { days: 120 } });
  });

  it('sorts returned bars ascending by timestamp', async () => {
    get.mockResolvedValueOnce({
      data: {
        data: [
          { date: '2026-01-05', open: 1, high: 1, low: 1, close: 1, volume: 1, amount: 1, change_percent: 0 },
          { date: '2026-01-02', open: 1, high: 1, low: 1, close: 1, volume: 1, amount: 1, change_percent: 0 },
        ],
      },
    });

    const result = await stocksApi.getKlineHistory('600519');

    expect(result.map((bar) => bar.timestamp)).toEqual([
      shanghaiMidnightMs('2026-01-02'),
      shanghaiMidnightMs('2026-01-05'),
    ]);
  });

  it('returns an empty array when backend payload has no data', async () => {
    get.mockResolvedValueOnce({ data: {} });

    const result = await stocksApi.getKlineHistory('600519');

    expect(result).toEqual([]);
  });
});
