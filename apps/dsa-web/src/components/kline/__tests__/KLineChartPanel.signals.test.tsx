import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SignalsResponse } from '../../../types/kline';
import { buildSignalGlyphs } from '../klineOverlays';

const okSignals: SignalsResponse = {
  status: 'ok',
  consistency: 'consistent',
  degradedReason: null,
  resonance: 'none',
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
      ciLow: null,
      ciHigh: null,
      baselineExcess: null,
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

  it('orients glyph triangles correctly: bullish apex on top (▲), bearish apex on bottom (▼)', async () => {
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

    await waitFor(() =>
      expect(registerOverlay.mock.calls.some(([t]) => (t as { name: string }).name === 'signalGlyph')).toBe(true),
    );
    const template = registerOverlay.mock.calls.find(
      ([t]) => (t as { name: string }).name === 'signalGlyph',
    )![0] as {
      createPointFigures: (p: unknown) => Array<{ type: string; attrs: { coordinates: Array<{ y: number }> } }>;
    };

    const apexY = 200;
    const glyph = (shape: string) => ({
      shape,
      filled: true,
      offsetSlot: 0,
      direction: shape === 'triangle-up' ? 'bullish' : 'bearish',
      opacity: 1,
      source: 'rule',
      drilldown: { markers: [] },
    });
    const baseY = (figs: Array<{ attrs: { coordinates: Array<{ y: number }> } }>) =>
      figs[0].attrs.coordinates[1].y;

    const up = template.createPointFigures({
      overlay: { extendData: { glyph: glyph('triangle-up') } },
      coordinates: [{ x: 100, y: apexY }],
    });
    const down = template.createPointFigures({
      overlay: { extendData: { glyph: glyph('triangle-down') } },
      coordinates: [{ x: 100, y: apexY }],
    });

    // klinecharts canvas y 向下增长，apex 固定在 (x, apexY)。
    // 看多 triangle-up：base 在 apex 下方（baseY > apexY），apex 在顶 => ▲
    expect(up[0].type).toBe('polygon');
    expect(baseY(up)).toBeGreaterThan(apexY);
    // 看空 triangle-down：base 在 apex 上方（baseY < apexY），apex 在底 => ▼
    expect(down[0].type).toBe('polygon');
    expect(baseY(down)).toBeLessThan(apexY);
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

  it('falls back to 有图无标注 when /signals returns status=degraded with markers', async () => {
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
          status: 'degraded',
          degradedReason: 'rel_vol unavailable for all bars',
          markers: okSignals.markers,
          priceLines: { entry: 1700.5, stop: 1620, target: 1850 },
        }),
      },
    }));

    await renderPanel();

    expect(await screen.findByTestId('signals-unavailable')).toBeInTheDocument();
    await waitFor(() => {
      const glyphCalls = createOverlay.mock.calls.filter(([arg]) =>
        typeof arg === 'object' && arg !== null && (arg as { name?: string }).name === 'signalGlyph',
      );
      expect(glyphCalls).toHaveLength(0);
      const priceLineCalls = createOverlay.mock.calls.filter(([arg]) =>
        typeof arg === 'object' && arg !== null && (arg as { name?: string }).name === 'priceLine',
      );
      expect(priceLineCalls).toHaveLength(0);
    });
  });

  it('degradation banner is daily-only: disappears after switching to weekly', async () => {
    vi.resetModules();
    setupChartMock();
    vi.doMock('../../../api/stocks', () => ({
      KLINE_DEFAULT_DAYS: 120,
      stocksApi: {
        getKlineHistory: vi.fn().mockResolvedValue([
          { timestamp: 1718323200000, open: 1690, high: 1710, low: 1680, close: 1700, volume: 1000, turnover: 0 },
        ]),
        getSignals: vi.fn().mockRejectedValue(new Error('signals 500')),
      },
    }));

    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    try {
      await renderPanel();

      // 日视图下降级横幅应可见
      expect(await screen.findByTestId('signals-unavailable')).toBeInTheDocument();

      // 切换到周视图
      fireEvent.click(screen.getByRole('button', { name: '周' }));

      // 周视图下降级横幅应消失（仅日视图显示）
      await waitFor(() => {
        expect(screen.queryByTestId('signals-unavailable')).toBeNull();
      });
    } finally {
      consoleError.mockRestore();
    }
  });

  it('weakens B-class (is_daily_approx) markers via lower opacity', async () => {
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

  it('switches period: refetches kline with period, skips signals on non-daily', async () => {
    vi.resetModules();
    const chart = setupChartMock();
    const getSignals = vi.fn().mockResolvedValue(okSignals);
    const getKlineHistory = vi.fn().mockResolvedValue([
      { timestamp: 1718323200000, open: 1690, high: 1710, low: 1680, close: 1700, volume: 1000, turnover: 0 },
    ]);
    vi.doMock('../../../api/stocks', () => ({
      KLINE_DEFAULT_DAYS: 120,
      stocksApi: { getKlineHistory, getSignals },
    }));

    await renderPanel();

    // 初始 daily: getKlineHistory(code, 120, 'daily') + getSignals 均被调用
    await waitFor(() => expect(chart.applyNewData).toHaveBeenCalled());
    await waitFor(() => expect(getSignals).toHaveBeenCalledTimes(1));
    expect(getKlineHistory).toHaveBeenCalledWith('600519', 120, 'daily');

    // 点击「周」按钮
    getKlineHistory.mockResolvedValue([
      { timestamp: 1718323200000, open: 1690, high: 1710, low: 1680, close: 1700, volume: 1000, turnover: 0 },
    ]);
    fireEvent.click(screen.getByRole('button', { name: '周' }));

    // getKlineHistory 用 weekly 参数重新调用
    await waitFor(() =>
      expect(getKlineHistory).toHaveBeenCalledWith('600519', 365, 'weekly'),
    );
    // getSignals 不再额外调用（仍为 1 次）
    expect(getSignals).toHaveBeenCalledTimes(1);
  });

  it('passes resonance from signals to drilldown panel', async () => {
    vi.resetModules();
    setupChartMock();
    const resonanceSignals: SignalsResponse = {
      ...okSignals,
      resonance: 'weekly_monthly',
    };
    vi.doMock('../../../api/stocks', () => ({
      KLINE_DEFAULT_DAYS: 120,
      stocksApi: {
        getKlineHistory: vi.fn().mockResolvedValue([
          { timestamp: 1718323200000, open: 1690, high: 1710, low: 1680, close: 1700, volume: 1000, turnover: 0 },
        ]),
        getSignals: vi.fn().mockResolvedValue(resonanceSignals),
      },
    }));

    await renderPanel();

    // 等待 signalGlyph overlay 注册，然后触发点击
    await waitFor(() => expect(setOverlayClickCallbacks.length).toBeGreaterThan(0));
    const [glyph] = buildSignalGlyphs(resonanceSignals.markers);
    setOverlayClickCallbacks[0]({ overlay: { extendData: { glyph } } });

    // 钻取面板应出现，且显示共振·周月徽标
    expect(await screen.findByTestId('drilldown-resonance')).toHaveTextContent('共振·周月');
  });
});
