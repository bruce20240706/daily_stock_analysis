import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SignalsResponse } from '../../../types/kline';
import { buildSignalGlyphs } from '../klineOverlays';

const okSignals: SignalsResponse = {
  status: 'ok',
  consistency: 'consistent',
  degradedReason: null,
  priceLines: { entry: 1700.5, stop: 1620, target: 1850 },
  markers: [
    {
      timestamp: 1718323200000,
      price: 1700.5,
      anchor: 'low',
      direction: 'bullish',
      signalType: 'volume_breakout',
      source: 'rule',
      confidence: 'high',
      isDailyApprox: false,
      isAnomalous: false,
      reason: '放量突破20日高',
      threshold: 2.0,
      observedValue: 2.4,
      hitRate: 0.62,
      hitSample: 18,
      verified: true,
      asOf: null,
    },
  ],
};

const createOverlay = vi.fn().mockReturnValue('overlay-1');
const registerOverlay = vi.fn();
const setOverlayClickCallbacks: Array<(payload: unknown) => void> = [];

const setupChartMock = () => {
  const chart = {
    setStyles: vi.fn(),
    createIndicator: vi.fn(),
    applyNewData: vi.fn(),
    createOverlay: createOverlay,
    removeOverlay: vi.fn(),
    subscribeAction: vi.fn(),
    resize: vi.fn(),
  };
  vi.doMock('klinecharts', () => ({
    init: vi.fn(() => chart),
    dispose: vi.fn(),
    registerOverlay: (template: { name: string; onClick?: (e: unknown) => boolean }) => {
      registerOverlay(template);
      if (template.onClick) {
        setOverlayClickCallbacks.push(template.onClick as (payload: unknown) => void);
      }
    },
  }));
  return chart;
};

const renderPanel = async () => {
  const { KLineChartPanel } = await import('../KLineChartPanel');
  render(<KLineChartPanel stockCode="600519" stockName="贵州茅台" market="CN" />);
};

describe('KLineChartPanel signals layer', () => {
  afterEach(() => {
    setOverlayClickCallbacks.length = 0;
    createOverlay.mockClear();
    registerOverlay.mockClear();
    vi.doUnmock('klinecharts');
    vi.doUnmock('../../../api/stocks');
    vi.resetModules();
  });

  it('registers signal overlays and draws entry/stop/target price lines on ok response', async () => {
    vi.resetModules();
    setupChartMock();
    vi.doMock('../../../api/stocks', () => ({
      KLINE_DEFAULT_DAYS: 120,
      stocksApi: {
        getKlineHistory: vi.fn().mockResolvedValue([
          { timestamp: 1718323200000, open: 1690, high: 1710, low: 1680, close: 1700, volume: 1000, turnover: 0 },
        ]),
        getSignals: vi.fn().mockResolvedValue(okSignals),
      },
    }));

    await renderPanel();

    await waitFor(() => expect(registerOverlay).toHaveBeenCalled());
    expect(registerOverlay.mock.calls.some(([t]) => t.name === 'signalGlyph')).toBe(true);

    await waitFor(() => {
      const priceLineCalls = createOverlay.mock.calls.filter(([arg]) =>
        typeof arg === 'object' && arg !== null && (arg as { name?: string }).name === 'priceLine',
      );
      expect(priceLineCalls).toHaveLength(3); // entry + stop + target
    });
  });

  it('opens the drilldown panel when a signal overlay is clicked', async () => {
    vi.resetModules();
    setupChartMock();
    vi.doMock('../../../api/stocks', () => ({
      KLINE_DEFAULT_DAYS: 120,
      stocksApi: {
        getKlineHistory: vi.fn().mockResolvedValue([
          { timestamp: 1718323200000, open: 1690, high: 1710, low: 1680, close: 1700, volume: 1000, turnover: 0 },
        ]),
        getSignals: vi.fn().mockResolvedValue(okSignals),
      },
    }));

    await renderPanel();

    await waitFor(() => expect(setOverlayClickCallbacks.length).toBeGreaterThan(0));
    // klinecharts passes the created overlay whose extendData is what we set in createOverlay ({ glyph }).
    const [glyph] = buildSignalGlyphs(okSignals.markers);
    setOverlayClickCallbacks[0]({ overlay: { extendData: { glyph } } });

    expect(await screen.findByText('放量突破20日高')).toBeInTheDocument();
  });

  it('falls back to chart-without-annotations when /signals fails', async () => {
    vi.resetModules();
    setupChartMock();
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.doMock('../../../api/stocks', () => ({
      KLINE_DEFAULT_DAYS: 120,
      stocksApi: {
        getKlineHistory: vi.fn().mockResolvedValue([
          { timestamp: 1718323200000, open: 1690, high: 1710, low: 1680, close: 1700, volume: 1000, turnover: 0 },
        ]),
        getSignals: vi.fn().mockRejectedValue(new Error('signals 500')),
      },
    }));

    try {
      await renderPanel();

      expect(await screen.findByTestId('signals-unavailable')).toBeInTheDocument();
      await waitFor(() => {
        const priceLineCalls = createOverlay.mock.calls.filter(([arg]) =>
          typeof arg === 'object' && arg !== null && (arg as { name?: string }).name === 'priceLine',
        );
        expect(priceLineCalls).toHaveLength(0);
      });
    } finally {
      consoleError.mockRestore();
    }
  });

  it('weakens degraded markers visually but still renders the chart', async () => {
    vi.resetModules();
    setupChartMock();
    vi.doMock('../../../api/stocks', () => ({
      KLINE_DEFAULT_DAYS: 120,
      stocksApi: {
        getKlineHistory: vi.fn().mockResolvedValue([
          { timestamp: 1718323200000, open: 1690, high: 1710, low: 1680, close: 1700, volume: 1000, turnover: 0 },
        ]),
        getSignals: vi.fn().mockResolvedValue({
          ...okSignals,
          markers: [{ ...okSignals.markers[0], isDailyApprox: true, signalType: 'vsa_no_demand' }],
        }),
      },
    }));

    await renderPanel();

    await waitFor(() => {
      const glyphCalls = createOverlay.mock.calls.filter(([arg]) =>
        typeof arg === 'object' && arg !== null && (arg as { name?: string }).name === 'signalGlyph',
      );
      expect(glyphCalls).toHaveLength(1);
      const styles = (glyphCalls[0][0] as { styles?: { polygon?: { color?: string } } }).styles;
      expect(styles?.polygon?.color).toMatch(/0\.45\)$/);
    });
  });
});
