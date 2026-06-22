import { beforeEach, describe, expect, it, vi } from 'vitest';
import { stocksApi } from '../stocks';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../index', () => ({ default: { get } }));

describe('stocksApi.getBoard', () => {
  beforeEach(() => { get.mockReset(); });

  it('maps snake_case board entries to camelCase and passes days/refresh', async () => {
    get.mockResolvedValueOnce({ data: {
      as_of: 111, counts: { buy: 1, hold: 0, sell: 0, unavailable: 1 }, degraded_codes: ['BAD'],
      entries: [
        { code: '600519', name: '贵州茅台', market: 'CN', action_group: 'buy',
          rule_direction: 'bullish', llm_direction: 'bullish', consistency: 'consistent',
          key_signals: ['volume_breakout'], price_lines: { entry: 1700, stop: 1620, target: 1850 },
          latest_close: 1660, hit_rate: 0.62, hit_sample: 18, verified: true,
          status: 'ok', degraded_reason: null },
        { code: 'BAD', name: null, market: 'CN', action_group: 'unavailable',
          rule_direction: null, llm_direction: null, consistency: 'unknown', key_signals: [],
          price_lines: { entry: null, stop: null, target: null }, latest_close: null,
          hit_rate: null, hit_sample: null, verified: false, status: 'degraded', degraded_reason: '信号计算失败' },
      ],
    }});

    const res = await stocksApi.getBoard(120, true);

    expect(get).toHaveBeenCalledWith('/api/v1/signals/board', { params: { days: 120, refresh: true } });
    expect(res.asOf).toBe(111);
    expect(res.counts.unavailable).toBe(1);
    expect(res.degradedCodes).toEqual(['BAD']);
    expect(res.entries[0].actionGroup).toBe('buy');
    expect(res.entries[0].keySignals).toEqual(['volume_breakout']);
    expect(res.entries[0].hitRate).toBe(0.62);
    expect(res.entries[0].priceLines).toEqual({ entry: 1700, stop: 1620, target: 1850 });
    expect(res.entries[1].actionGroup).toBe('unavailable');
    expect(res.entries[1].ruleDirection).toBeNull();
  });

  it('maps credibility CI and baseline excess (snake→camel) for board entries', async () => {
    get.mockResolvedValueOnce({ data: {
      as_of: 222, counts: { buy: 1, hold: 0, sell: 0, unavailable: 0 }, degraded_codes: [],
      entries: [
        { code: '600519', name: '贵州茅台', market: 'CN', action_group: 'buy',
          rule_direction: 'bullish', llm_direction: 'bullish', consistency: 'consistent',
          key_signals: ['volume_breakout'], price_lines: { entry: 1700, stop: 1620, target: 1850 },
          latest_close: 1660, hit_rate: 0.68, hit_sample: 20, verified: true,
          ci_low: 0.55, ci_high: 0.8, baseline_excess: 0.05,
          status: 'ok', degraded_reason: null },
      ],
    }});
    const res = await stocksApi.getBoard(120);
    expect(res.entries[0].ciLow).toBe(0.55);
    expect(res.entries[0].ciHigh).toBe(0.8);
    expect(res.entries[0].baselineExcess).toBe(0.05);
  });

  it('omits refresh when false-y and days when undefined', async () => {
    get.mockResolvedValueOnce({ data: { as_of: 1, counts: { buy:0,hold:0,sell:0,unavailable:0 }, degraded_codes: [], entries: [] }});
    await stocksApi.getBoard();
    expect(get).toHaveBeenCalledWith('/api/v1/signals/board', { params: {} });
  });

  it('maps resonance, defaulting to none when absent', async () => {
    get.mockResolvedValueOnce({ data: {
      as_of: 333, counts: { buy: 1, hold: 0, sell: 0, unavailable: 1 }, degraded_codes: [],
      entries: [
        { code: '600519', name: '贵州茅台', market: 'CN', action_group: 'buy',
          rule_direction: 'bullish', llm_direction: 'bullish', consistency: 'consistent',
          key_signals: [], price_lines: { entry: 1700, stop: 1620, target: 1850 },
          latest_close: 1660, hit_rate: 0.62, hit_sample: 18, verified: true,
          status: 'ok', degraded_reason: null, resonance: 'weekly_monthly' },
        { code: '000001', name: '平安银行', market: 'CN', action_group: 'hold',
          rule_direction: 'neutral', llm_direction: 'neutral', consistency: 'consistent',
          key_signals: [], price_lines: { entry: null, stop: null, target: null },
          latest_close: 10, hit_rate: null, hit_sample: null, verified: false,
          status: 'ok', degraded_reason: null },
      ],
    }});
    const res = await stocksApi.getBoard(120);
    expect(res.entries[0].resonance).toBe('weekly_monthly');
    expect(res.entries[1].resonance).toBe('none');
  });
});
