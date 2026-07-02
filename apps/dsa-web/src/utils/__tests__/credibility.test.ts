import { describe, it, expect } from 'vitest';
import { formatHitRate, formatCi, formatExcess, verifiedLabel, formatHorizon, markerStatusLabel, planQualityLabel, unverifiedExcessNote } from '../credibility';

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
  it('labels plan quality', () => {
    expect(planQualityLabel('high')).toBe('高');
    expect(planQualityLabel('medium')).toBe('中');
    expect(planQualityLabel('low')).toBe('低');
    expect(planQualityLabel(null)).toBeNull();
  });
  it('unverified excess note explains correction, silent for legacy/verified', () => {
    // 矛盾态:raw 超额>0 但 verified=false 且有校正值 → 给出数字解释
    expect(unverifiedExcessNote({
      verified: false, baselineExcess: 0.05, ciLowCorrected: 0.48, familySize: 20,
    })).toBe('20 组同检校正后下界 48%,未超基准');
    // familySize 缺失但有校正值 → 泛化措辞
    expect(unverifiedExcessNote({
      verified: false, baselineExcess: 0.05, ciLowCorrected: 0.48, familySize: null,
    })).toBe('多重检验校正后下界 48%,未超基准');
    // legacy 行(无校正值)→ 不注解,维持升级前展示(M-5:不显示 N=0)
    expect(unverifiedExcessNote({
      verified: false, baselineExcess: 0.05, ciLowCorrected: null, familySize: null,
    })).toBeNull();
    // verified=true / 无超额 / excess 缺失 → 无矛盾,不注解
    expect(unverifiedExcessNote({
      verified: true, baselineExcess: 0.05, ciLowCorrected: 0.52, familySize: 20,
    })).toBeNull();
    expect(unverifiedExcessNote({
      verified: false, baselineExcess: -0.02, ciLowCorrected: 0.4, familySize: 5,
    })).toBeNull();
    expect(unverifiedExcessNote({
      verified: false, baselineExcess: null, ciLowCorrected: 0.4, familySize: 5,
    })).toBeNull();
  });
});
