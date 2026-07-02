import { beforeEach, describe, expect, it, vi } from 'vitest';
import { stocksApi } from '../stocks';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('../index', () => ({
  default: { get },
}));

describe('stocksApi.getSignals', () => {
  beforeEach(() => {
    get.mockReset();
  });

  it('requests the signals endpoint with days query and maps snake_case markers to camelCase', async () => {
    get.mockResolvedValueOnce({
      data: {
        status: 'ok',
        consistency: 'consistent',
        degraded_reason: null,
        price_lines: { entry: 1700.5, stop: 1620, target: 1850 },
        markers: [
          {
            timestamp: 1718323200000,
            price: 1700.5,
            anchor: 'low',
            direction: 'bullish',
            signal_type: 'volume_breakout',
            source: 'rule',
            confidence: 'high',
            is_daily_approx: false,
            is_anomalous: false,
            reason: '放量突破20日高',
            threshold: 2.0,
            observed_value: 2.4,
            hit_rate: 0.62,
            hit_sample: 18,
            verified: true,
            as_of: null,
          },
          {
            timestamp: 1718323200000,
            price: 1705,
            anchor: 'high',
            direction: 'bullish',
            signal_type: 'llm_advice',
            source: 'llm',
            confidence: 'medium',
            is_daily_approx: false,
            is_anomalous: false,
            reason: '建议买入',
            threshold: null,
            observed_value: null,
            hit_rate: null,
            hit_sample: null,
            verified: false,
            as_of: 1718236800000,
          },
        ],
      },
    });

    const result = await stocksApi.getSignals('600519', 120);

    expect(get).toHaveBeenCalledWith('/api/v1/stocks/600519/signals', {
      params: { days: 120 },
    });
    expect(result.status).toBe('ok');
    expect(result.consistency).toBe('consistent');
    expect(result.priceLines).toEqual({ entry: 1700.5, stop: 1620, target: 1850 });
    expect(result.markers).toHaveLength(2);
    expect(result.markers[0].signalType).toBe('volume_breakout');
    expect(result.markers[0].isDailyApprox).toBe(false);
    expect(result.markers[0].observedValue).toBe(2.4);
    expect(result.markers[0].hitRate).toBe(0.62);
    expect(result.markers[0].hitSample).toBe(18);
    expect(result.markers[0].verified).toBe(true);
    expect(result.markers[1].source).toBe('llm');
    expect(result.markers[1].asOf).toBe(1718236800000);
  });

  it('omits the days param when not provided and preserves degraded shape', async () => {
    get.mockResolvedValueOnce({
      data: {
        status: 'degraded',
        consistency: 'unknown',
        degraded_reason: '无可用历史数据',
        price_lines: { entry: null, stop: null, target: null },
        markers: [],
      },
    });

    const result = await stocksApi.getSignals('hk00700');

    expect(get).toHaveBeenCalledWith('/api/v1/stocks/hk00700/signals', { params: {} });
    expect(result.status).toBe('degraded');
    expect(result.consistency).toBe('unknown');
    expect(result.degradedReason).toBeTruthy();
    expect(result.priceLines).toEqual({ entry: null, stop: null, target: null });
    expect(result.markers).toEqual([]);
  });

  it('maps credibility CI and baseline excess (snake→camel)', async () => {
    get.mockResolvedValueOnce({ data: { status: 'ok', consistency: 'consistent', degraded_reason: null,
      price_lines: { entry: 1, stop: 0.9, target: 1.2 },
      markers: [{ timestamp: 1, price: 1, anchor: 'low', direction: 'bullish', signal_type: 'volume_breakout',
        source: 'rule', confidence: 'high', is_daily_approx: false, is_anomalous: false, reason: 'x',
        threshold: null, observed_value: null, hit_rate: 0.68, hit_sample: 20, verified: true, as_of: null,
        ci_low: 0.55, ci_high: 0.8, baseline_excess: 0.05 }] } });
    const res = await stocksApi.getSignals('600519');
    expect(res.markers[0].ciLow).toBe(0.55);
    expect(res.markers[0].ciHigh).toBe(0.8);
    expect(res.markers[0].baselineExcess).toBe(0.05);
  });

  it('maps ci_low_corrected/family_size to camelCase, null when absent', async () => {
    get.mockResolvedValueOnce({ data: { status: 'ok', consistency: 'consistent', degraded_reason: null,
      price_lines: { entry: 1, stop: 0.9, target: 1.2 },
      markers: [{ timestamp: 1, price: 1, anchor: 'low', direction: 'bullish', signal_type: 'volume_breakout',
        source: 'rule', confidence: 'high', is_daily_approx: false, is_anomalous: false, reason: 'x',
        threshold: null, observed_value: null, hit_rate: 0.68, hit_sample: 20, verified: false, as_of: null,
        ci_low: 0.55, ci_high: 0.8, baseline_excess: 0.05,
        ci_low_corrected: 0.48, family_size: 20 }] } });
    const res = await stocksApi.getSignals('600519');
    expect(res.markers[0].ciLowCorrected).toBe(0.48);
    expect(res.markers[0].familySize).toBe(20);
  });

  it('defaults ci_low_corrected/family_size to null for legacy backend payloads without the fields', async () => {
    get.mockResolvedValueOnce({ data: { status: 'ok', consistency: 'consistent', degraded_reason: null,
      price_lines: { entry: 1, stop: 0.9, target: 1.2 },
      markers: [{ timestamp: 1, price: 1, anchor: 'low', direction: 'bullish', signal_type: 'volume_breakout',
        source: 'rule', confidence: 'high', is_daily_approx: false, is_anomalous: false, reason: 'x',
        threshold: null, observed_value: null, hit_rate: 0.68, hit_sample: 20, verified: true, as_of: null,
        ci_low: 0.55, ci_high: 0.8, baseline_excess: 0.05 }] } });
    const res = await stocksApi.getSignals('600519');
    expect(res.markers[0].ciLowCorrected).toBeNull();
    expect(res.markers[0].familySize).toBeNull();
  });

  it('maps resonance, defaulting to none when absent', async () => {
    get.mockResolvedValueOnce({ data: { status: 'ok', consistency: 'consistent', degraded_reason: null,
      price_lines: { entry: 1700, stop: 1620, target: 1850 }, markers: [],
      resonance: 'weekly' } });
    const res = await stocksApi.getSignals('600519', 120);
    expect(res.resonance).toBe('weekly');

    get.mockResolvedValueOnce({ data: { status: 'ok', consistency: 'consistent', degraded_reason: null,
      price_lines: { entry: 1700, stop: 1620, target: 1850 }, markers: [] } });
    const res2 = await stocksApi.getSignals('600519', 120);
    expect(res2.resonance).toBe('none');
  });

  it('maps horizon_bars/status/plan_quality snake→camel', async () => {
    get.mockResolvedValueOnce({ data: { status: 'ok', consistency: 'consistent',
      degraded_reason: null, resonance: 'none', plan_quality: 'high',
      price_lines: { entry: null, stop: null, target: null },
      markers: [{ timestamp: 1, price: 1, anchor: 'low', direction: 'bullish',
        signal_type: 'a', source: 'rule', confidence: 'high', is_daily_approx: false,
        is_anomalous: false, reason: 'r', threshold: null, observed_value: null,
        hit_rate: null, hit_sample: null, verified: false, ci_low: null, ci_high: null,
        baseline_excess: null, as_of: null, horizon_bars: 10, status: 'active' }] } });
    const res = await stocksApi.getSignals('600519');
    expect(res.planQuality).toBe('high');
    expect(res.markers[0].horizonBars).toBe(10);
    expect(res.markers[0].status).toBe('active');
  });

  it('encodes crypto codes containing a slash for the {code:path} route', async () => {
    get.mockResolvedValueOnce({
      data: {
        status: 'ok',
        consistency: 'unknown',
        degraded_reason: null,
        price_lines: { entry: null, stop: null, target: null },
        markers: [],
      },
    });

    await stocksApi.getSignals('BTC/USDT', 120);

    expect(get).toHaveBeenCalledWith('/api/v1/stocks/BTC%2FUSDT/signals', {
      params: { days: 120 },
    });
  });
});
