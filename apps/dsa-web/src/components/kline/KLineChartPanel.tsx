import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { dispose, init, registerOverlay } from 'klinecharts';
import type {
  Chart,
  OverlayCreateFiguresCallbackParams,
  OverlayEvent,
  OverlayFigure,
} from 'klinecharts';
import { stocksApi, KLINE_DEFAULT_DAYS } from '../../api/stocks';
import type { KLine, SignalMarker } from '../../types/kline';
import { buildSignalGlyphs, type SignalGlyph } from './klineOverlays';
import { SignalDrilldownPanel } from './SignalDrilldownPanel';

interface KLineChartPanelProps {
  stockCode: string;
  market?: string;
  stockName?: string;
  days?: number;
}

type LoadState = 'loading' | 'ready' | 'empty' | 'error';

/** 中式红涨绿跌（默认）与绿涨红跌两套配色。 */
const UP_RED = '#ef4444';
const DOWN_GREEN = '#22c55e';

const buildCandleStyles = (upRedDownGreen: boolean) => {
  const upColor = upRedDownGreen ? UP_RED : DOWN_GREEN;
  const downColor = upRedDownGreen ? DOWN_GREEN : UP_RED;
  return {
    candle: {
      bar: {
        upColor,
        downColor,
        noChangeColor: '#888888',
        upBorderColor: upColor,
        downBorderColor: downColor,
        noChangeBorderColor: '#888888',
        upWickColor: upColor,
        downWickColor: downColor,
        noChangeWickColor: '#888888',
      },
    },
  };
};

/** 依据 glyph 方向与弱化透明度生成 rgba 颜色：bullish 红、bearish 绿、neutral 灰。 */
const rgbaForGlyph = (glyph: SignalGlyph): string => {
  // 中式：bullish 红、bearish 绿；neutral 灰；alpha 由 B 类弱化决定
  const base =
    glyph.direction === 'bullish'
      ? '239, 68, 68'
      : glyph.direction === 'bearish'
        ? '34, 197, 94'
        : '148, 163, 184';
  return `rgba(${base}, ${glyph.opacity})`;
};

/**
 * 注册自绘 signalGlyph overlay 模板：实心/空心三角与圆点由 glyph.shape/filled 决定，
 * 多轨冲突按 offsetSlot 横向错开；点击通过 extendData.glyph 钻取到对应 markers。
 */
const registerSignalGlyphTemplate = (onPick: (markers: SignalMarker[]) => void): void => {
  registerOverlay({
    name: 'signalGlyph',
    totalStep: 1,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: (params: OverlayCreateFiguresCallbackParams): OverlayFigure[] => {
      const point = params.coordinates[0];
      const glyph = (params.overlay.extendData as { glyph?: SignalGlyph } | null)?.glyph;
      if (!point || !glyph) return [];
      // klinecharts canvas y 向下增长：apex 固定在 (x, point.y)，base 两点在 apex ± dir*size。
      // 看多 triangle-up 需 apex 在上、base 在下（dir=+1，朝上 ▲）；
      // 看空 triangle-down 需 apex 在下、base 在上（dir=-1，朝下 ▼）。
      const dir = glyph.shape === 'triangle-down' ? -1 : 1;
      const x = point.x + glyph.offsetSlot * 10;
      const size = 6;
      const color = rgbaForGlyph(glyph);
      if (glyph.shape === 'dot') {
        return [{ type: 'circle', attrs: { x, y: point.y, r: 3 }, styles: { color } }];
      }
      return [
        {
          type: 'polygon',
          attrs: {
            coordinates: [
              { x, y: point.y },
              { x: x - size, y: point.y + dir * size * 1.6 },
              { x: x + size, y: point.y + dir * size * 1.6 },
            ],
          },
          styles: glyph.filled
            ? { style: 'fill', color }
            : { style: 'stroke', borderColor: color, color: 'transparent' },
        },
      ];
    },
    onClick: (event: OverlayEvent): boolean => {
      const glyph = (event.overlay.extendData as { glyph?: SignalGlyph } | null)?.glyph;
      if (glyph) onPick(glyph.drilldown.markers);
      return true;
    },
  });
};

/** 为单个 glyph 创建 overlay：锚定 (timestamp, price)，extendData 携带 glyph 供钻取。 */
const drawGlyphOverlay = (chart: Chart, glyph: SignalGlyph): void => {
  chart.createOverlay({
    name: 'signalGlyph',
    points: [{ timestamp: glyph.timestamp, value: glyph.price }],
    extendData: { glyph },
    styles: { polygon: { color: rgbaForGlyph(glyph) } },
  });
};

/** 绘制 entry/stop/target 价位线（内置 priceLine overlay），仅对非空值各画一条。 */
const drawPriceLines = (
  chart: Chart,
  priceLines: { entry: number | null; stop: number | null; target: number | null },
): void => {
  const lines: Array<[number | null, string]> = [
    [priceLines.entry, 'rgba(239, 68, 68, 0.9)'],
    [priceLines.stop, 'rgba(148, 163, 184, 0.9)'],
    [priceLines.target, 'rgba(34, 197, 94, 0.9)'],
  ];
  for (const [value, color] of lines) {
    if (value === null) continue;
    chart.createOverlay({
      name: 'priceLine',
      points: [{ value }],
      styles: { line: { color } },
    });
  }
};

/**
 * klinecharts 重面板：唯一 import 'klinecharts' 处（仅经 KLineDrawer lazy 加载，
 * 不进同步路径，保证首屏不受影响）。渲染蜡烛 + 成交量副图，
 * 十字光标/缩放为 klinecharts 默认能力；涨跌颜色默认中式红涨绿跌，可切换。
 */
export const KLineChartPanel: React.FC<KLineChartPanelProps> = ({
  stockCode,
  days = KLINE_DEFAULT_DAYS,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<Chart | null>(null);
  const [state, setState] = useState<LoadState>('loading');
  const [upRedDownGreen, setUpRedDownGreen] = useState(true);
  const [signalsAvailable, setSignalsAvailable] = useState(true);
  const [drilldownMarkers, setDrilldownMarkers] = useState<SignalMarker[] | null>(null);

  const applySignalsLayer = useCallback(() => {
    const chart = chartRef.current;
    if (!chart) return;
    stocksApi
      // 透传 days：保持 /signals 与 /history 同源同窗口（结构性保证，不依赖默认值巧合）
      .getSignals(stockCode, days)
      .then((signals) => {
        if (signals.status === 'degraded') {
          // 有图无标注：后端降级（任意非空 degraded_reason）一律按通用降级，不画任何标注。
          setSignalsAvailable(false);
          return;
        }
        setSignalsAvailable(true);
        registerSignalGlyphTemplate((markers) => setDrilldownMarkers(markers));
        for (const glyph of buildSignalGlyphs(signals.markers)) {
          drawGlyphOverlay(chart, glyph);
        }
        drawPriceLines(chart, signals.priceLines);
      })
      .catch((error) => {
        console.error('Failed to load signals overlay:', error);
        setSignalsAvailable(false);
      });
  }, [stockCode, days]);

  useEffect(() => {
    let disposed = false;
    const container = containerRef.current;
    if (!container) return;

    const chart = init(container);
    chartRef.current = chart ?? null;
    if (chart) {
      chart.setStyles(buildCandleStyles(true));
      chart.createIndicator('VOL', false, { id: 'vol_pane' });
    }

    (async () => {
      try {
        const klines = await stocksApi.getKlineHistory(stockCode, days);
        if (disposed) return;
        if (klines.length === 0) {
          setState('empty');
          return;
        }
        chartRef.current?.applyNewData(klines as KLine[]);
        setState('ready');
        // 信号层独立加载，其失败不影响纯 K 线渲染（catch 内仅降级标注）。
        applySignalsLayer();
      } catch (error) {
        if (disposed) return;
        console.error('KLine history load failed:', error);
        setState('error');
      }
    })();

    return () => {
      disposed = true;
      if (container) {
        dispose(container);
      }
      chartRef.current = null;
    };
  }, [stockCode, days, applySignalsLayer]);

  const toggleColors = () => {
    setUpRedDownGreen((prev) => {
      const next = !prev;
      chartRef.current?.setStyles(buildCandleStyles(next));
      return next;
    });
  };

  return (
    <div className="flex h-full flex-col">
      <div className="mb-2 flex items-center justify-end">
        <button
          type="button"
          onClick={toggleColors}
          aria-label="切换涨跌颜色"
          className="home-surface-button rounded-lg px-3 py-1.5 text-xs text-secondary-text"
        >
          {upRedDownGreen ? '红涨绿跌' : '绿涨红跌'}
        </button>
      </div>
      <div className="relative min-h-0 flex-1">
        <div ref={containerRef} className="h-full w-full" data-testid="kline-chart-container" />
        {state === 'loading' && (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="home-spinner h-10 w-10 animate-spin border-[3px]" />
          </div>
        )}
        {state === 'empty' && (
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-sm text-secondary-text">暂无 K 线数据</p>
          </div>
        )}
        {state === 'error' && (
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-sm text-danger">K 线加载失败</p>
          </div>
        )}
        {!signalsAvailable && (
          <div
            data-testid="signals-unavailable"
            className="absolute bottom-2 left-2 rounded-lg border border-border/50 bg-card/60 px-3 py-2 text-xs text-secondary-text"
          >
            信号标注暂不可用，已展示纯 K 线图。
          </div>
        )}
        {drilldownMarkers && (
          <div className="absolute right-2 top-2 z-10 w-72 max-w-[80%]">
            <SignalDrilldownPanel markers={drilldownMarkers} onClose={() => setDrilldownMarkers(null)} />
          </div>
        )}
      </div>
    </div>
  );
};

export default KLineChartPanel;
