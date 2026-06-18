import type { ResonanceLevel } from '../types/kline';

/** 共振徽标文案：none -> null（不渲染）；weekly -> '共振·周'；weekly_monthly -> '共振·周月'。 */
export function resonanceLabel(level: ResonanceLevel): string | null {
  if (level === 'weekly') return '共振·周';
  if (level === 'weekly_monthly') return '共振·周月';
  return null;
}

/** 徽标 tooltip 文案。 */
export function resonanceTooltip(level: ResonanceLevel): string {
  if (level === 'weekly') return '周线趋势与日线信号同向（多周期共振）';
  if (level === 'weekly_monthly') return '周线与月线趋势均与日线信号同向（强共振）';
  return '无多周期共振';
}
