import type { SignalDirection, SignalMarker, SignalSource } from '../../types/kline';

/** Full-strength opacity for rule (A-class) glyphs. */
export const RULE_OPACITY = 1;
/** Reduced opacity for B-class (is_daily_approx) low-confidence glyphs. */
export const B_CLASS_OPACITY = 0.45;

export type GlyphShape = 'triangle-up' | 'triangle-down' | 'dot';
export type GlyphMode = 'single' | 'merged' | 'conflict';

export interface SignalDrilldownPayload {
  timestamp: number;
  markers: SignalMarker[];
}

export interface SignalGlyph {
  /** Stable id for klinecharts overlay (one overlay per glyph). */
  id: string;
  timestamp: number;
  /** Anchor price of the representative marker. */
  price: number;
  shape: GlyphShape;
  /** rule => filled triangle; llm => hollow triangle. */
  filled: boolean;
  source: SignalSource;
  direction: SignalDirection;
  opacity: number;
  mode: GlyphMode;
  /** Horizontal slot for side-by-side conflict rendering (0,1,...). */
  offsetSlot: number;
  /** Every marker represented by this glyph (1 for single, 2+ for merged). */
  markers: SignalMarker[];
  drilldown: SignalDrilldownPayload;
}

const shapeForDirection = (direction: SignalDirection): GlyphShape => {
  if (direction === 'bullish') return 'triangle-up';
  if (direction === 'bearish') return 'triangle-down';
  return 'dot';
};

const opacityForMarkers = (markers: SignalMarker[]): number =>
  markers.some((m) => m.isDailyApprox) ? B_CLASS_OPACITY : RULE_OPACITY;

const makeGlyph = (
  markers: SignalMarker[],
  mode: GlyphMode,
  offsetSlot: number,
): SignalGlyph => {
  const lead = markers[0];
  return {
    id: `${lead.timestamp}:${lead.source}:${lead.signalType}:${offsetSlot}`,
    timestamp: lead.timestamp,
    price: lead.price,
    shape: shapeForDirection(lead.direction),
    filled: lead.source === 'rule',
    source: lead.source,
    direction: lead.direction,
    opacity: opacityForMarkers(markers),
    mode,
    offsetSlot,
    markers,
    drilldown: { timestamp: lead.timestamp, markers },
  };
};

/**
 * Build dual-track glyph descriptors from signal markers.
 * - rule => filled triangle; llm => hollow triangle
 * - same bar + same direction across both tracks => one merged glyph
 * - same bar + conflicting directions => side-by-side glyphs (distinct offsetSlot)
 * - B-class (is_daily_approx) => reduced opacity
 */
export const buildSignalGlyphs = (markers: SignalMarker[]): SignalGlyph[] => {
  const byBar = new Map<number, SignalMarker[]>();
  for (const marker of markers) {
    const bucket = byBar.get(marker.timestamp);
    if (bucket) {
      bucket.push(marker);
    } else {
      byBar.set(marker.timestamp, [marker]);
    }
  }

  const glyphs: SignalGlyph[] = [];
  for (const [, barMarkers] of byBar) {
    if (barMarkers.length === 1) {
      glyphs.push(makeGlyph(barMarkers, 'single', 0));
      continue;
    }

    const directions = new Set(barMarkers.map((m) => m.direction));
    const hasRule = barMarkers.some((m) => m.source === 'rule');
    const hasLlm = barMarkers.some((m) => m.source === 'llm');

    if (directions.size === 1 && hasRule && hasLlm) {
      glyphs.push(makeGlyph(barMarkers, 'merged', 0));
      continue;
    }

    barMarkers.forEach((marker, idx) => {
      glyphs.push(makeGlyph([marker], 'conflict', idx));
    });
  }

  return glyphs;
};
