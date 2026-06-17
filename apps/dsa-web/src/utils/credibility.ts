/** Shared credibility format utilities — pure functions, no external deps. */

interface HitRateInput {
  hitRate: number | null;
  hitSample: number | null;
}

/** Unified hit-rate口径: no sample → '暂无样本'; otherwise 'X% · n' */
export function formatHitRate({ hitRate, hitSample }: HitRateInput): string {
  if (hitRate === null || hitSample === null || hitSample <= 0) {
    return '暂无样本';
  }
  return `${Math.round(hitRate * 100)}% · ${hitSample}`;
}

interface CiInput {
  ciLow: number | null;
  ciHigh: number | null;
}

/** Formats CI band as '[a%–b%]' or null when data absent. */
export function formatCi({ ciLow, ciHigh }: CiInput): string | null {
  if (ciLow === null || ciHigh === null) return null;
  return `[${Math.round(ciLow * 100)}%–${Math.round(ciHigh * 100)}%]`;
}

/** Formats baseline excess as '超额 +Δpp' / '无超额' / null when data absent. */
export function formatExcess(baselineExcess: number | null): string | null {
  if (baselineExcess === null) return null;
  if (baselineExcess <= 0) return '无超额';
  return `超额 +${Math.round(baselineExcess * 100)}pp`;
}

/** Returns verified/unverified label string. */
export function verifiedLabel(verified: boolean): string {
  return verified ? '已验证' : '未验证';
}
