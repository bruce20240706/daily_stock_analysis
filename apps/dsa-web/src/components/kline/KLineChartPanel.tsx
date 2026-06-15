import type React from 'react';
import { useEffect, useRef, useState } from 'react';
import { dispose, init } from 'klinecharts';
import type { Chart } from 'klinecharts';
import { stocksApi, KLINE_DEFAULT_DAYS } from '../../api/stocks';
import type { KLine } from '../../types/kline';

interface KLineChartPanelProps {
  stockCode: string;
  market?: string;
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
  }, [stockCode, days]);

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
      </div>
    </div>
  );
};

export default KLineChartPanel;
