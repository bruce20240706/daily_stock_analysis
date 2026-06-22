import { describe, expect, it } from 'vitest';
import type { SignalMarker } from '../../../types/kline';
import { buildSignalGlyphs, RULE_OPACITY, B_CLASS_OPACITY } from '../klineOverlays';

const ruleBull = (over: Partial<SignalMarker> = {}): SignalMarker => ({
  timestamp: 1718323200000,
  price: 1700,
  anchor: 'low',
  direction: 'bullish',
  signalType: 'volume_breakout',
  source: 'rule',
  confidence: 'high',
  isDailyApprox: false,
  isAnomalous: false,
  reason: '放量突破',
  threshold: 2.0,
  observedValue: 2.4,
  hitRate: 0.62,
  hitSample: 18,
  verified: true,
  ciLow: null,
  ciHigh: null,
  baselineExcess: null,
  asOf: null,
  horizonBars: null,
  status: null,
  ...over,
});

const llmBull = (over: Partial<SignalMarker> = {}): SignalMarker =>
  ruleBull({
    source: 'llm',
    signalType: 'llm_advice',
    anchor: 'high',
    price: 1705,
    confidence: 'medium',
    reason: '建议买入',
    threshold: null,
    observedValue: null,
    hitRate: null,
    hitSample: null,
    verified: false,
    asOf: 1718236800000,
    ...over,
  });

describe('buildSignalGlyphs', () => {
  it('renders a filled triangle for rule signals and a hollow triangle for llm signals', () => {
    const glyphs = buildSignalGlyphs([ruleBull(), llmBull({ timestamp: 1718409600000, price: 1720 })]);

    const rule = glyphs.find((g) => g.source === 'rule');
    const llm = glyphs.find((g) => g.source === 'llm');

    expect(rule?.shape).toBe('triangle-up');
    expect(rule?.filled).toBe(true);
    expect(llm?.shape).toBe('triangle-up');
    expect(llm?.filled).toBe(false);
  });

  it('uses triangle-down for bearish direction and neutral has no triangle direction', () => {
    const glyphs = buildSignalGlyphs([
      ruleBull({ direction: 'bearish', anchor: 'high' }),
      ruleBull({ timestamp: 1718409600000, direction: 'neutral' }),
    ]);

    expect(glyphs[0].shape).toBe('triangle-down');
    expect(glyphs[1].shape).toBe('dot');
  });

  it('merges a rule and llm signal on the same bar with same direction into one strong glyph', () => {
    const ts = 1718323200000;
    const glyphs = buildSignalGlyphs([ruleBull({ timestamp: ts }), llmBull({ timestamp: ts })]);

    expect(glyphs).toHaveLength(1);
    expect(glyphs[0].mode).toBe('merged');
    expect(glyphs[0].markers).toHaveLength(2);
  });

  it('places conflicting rule and llm signals on the same bar side by side', () => {
    const ts = 1718323200000;
    const glyphs = buildSignalGlyphs([
      ruleBull({ timestamp: ts, direction: 'bullish' }),
      llmBull({ timestamp: ts, direction: 'bearish' }),
    ]);

    expect(glyphs).toHaveLength(2);
    expect(glyphs.every((g) => g.mode === 'conflict')).toBe(true);
    expect(glyphs[0].offsetSlot).not.toBe(glyphs[1].offsetSlot);
  });

  it('weakens B-class (is_daily_approx) glyphs via lower opacity', () => {
    const glyphs = buildSignalGlyphs([
      ruleBull({ isDailyApprox: false }),
      ruleBull({ timestamp: 1718409600000, isDailyApprox: true, signalType: 'vsa_no_demand' }),
    ]);

    const aClass = glyphs.find((g) => !g.markers[0].isDailyApprox);
    const bClass = glyphs.find((g) => g.markers[0].isDailyApprox);

    expect(aClass?.opacity).toBe(RULE_OPACITY);
    expect(bClass?.opacity).toBe(B_CLASS_OPACITY);
    expect(B_CLASS_OPACITY).toBeLessThan(RULE_OPACITY);
  });

  it('builds a drilldown payload exposing every marker on the clicked glyph', () => {
    const ts = 1718323200000;
    const glyphs = buildSignalGlyphs([ruleBull({ timestamp: ts }), llmBull({ timestamp: ts })]);

    expect(glyphs[0].drilldown.timestamp).toBe(ts);
    expect(glyphs[0].drilldown.markers).toHaveLength(2);
  });
});
