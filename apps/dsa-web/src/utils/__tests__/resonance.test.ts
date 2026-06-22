import { describe, it, expect } from 'vitest';
import { resonanceLabel, resonanceTooltip } from '../resonance';

describe('resonance labels', () => {
  it('labels by level', () => {
    expect(resonanceLabel('none')).toBeNull();
    expect(resonanceLabel('weekly')).toBe('共振·周');
    expect(resonanceLabel('weekly_monthly')).toBe('共振·周月');
  });
  it('tooltips are non-empty for active levels', () => {
    expect(resonanceTooltip('weekly').length).toBeGreaterThan(0);
    expect(resonanceTooltip('weekly_monthly').length).toBeGreaterThan(0);
  });
});
