import { describe, it, expect } from 'vitest';
import { formatHitRate, formatCi, formatExcess, verifiedLabel, formatHorizon, markerStatusLabel } from '../credibility';

describe('credibility format', () => {
  it('formats hit rate with sample, empty when no sample', () => {
    expect(formatHitRate({ hitRate: 0.62, hitSample: 18 })).toBe('62% · 18');
    expect(formatHitRate({ hitRate: null, hitSample: null })).toBe('暂无样本');
    expect(formatHitRate({ hitRate: 0.62, hitSample: 0 })).toBe('暂无样本');
  });
  it('formats CI band and excess', () => {
    expect(formatCi({ ciLow: 0.55, ciHigh: 0.8 })).toBe('[55%–80%]');
    expect(formatCi({ ciLow: null, ciHigh: null })).toBeNull();
    expect(formatExcess(0.05)).toBe('超额 +5pp');
    expect(formatExcess(-0.03)).toBe('无超额');
    expect(formatExcess(null)).toBeNull();
  });
  it('verified label', () => {
    expect(verifiedLabel(true)).toBe('已验证');
    expect(verifiedLabel(false)).toBe('未验证');
  });
  it('formats horizon bars', () => {
    expect(formatHorizon(10)).toBe('窗口 10 根');
    expect(formatHorizon(null)).toBeNull();
  });
  it('labels marker status', () => {
    expect(markerStatusLabel('active')).toBe('最新');
    expect(markerStatusLabel('aging')).toBe('窗口内');
    expect(markerStatusLabel('expired')).toBe('已过窗');
    expect(markerStatusLabel(null)).toBeNull();
  });
});
