import { describe, expect, it } from 'vitest';
import { mapKLineDataToKLine } from '../stocks';
import type { KLine } from '../../types/kline';

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
    expect(result.timestamp).toBe(Date.UTC(2026, 2, 15, 16, 0, 0)); // 2026-03-16 00:00 +08:00
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
