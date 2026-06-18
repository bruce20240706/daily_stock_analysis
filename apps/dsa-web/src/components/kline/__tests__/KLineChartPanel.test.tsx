import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { KLine } from '../../../types/kline';

const init = vi.hoisted(() => vi.fn());
const dispose = vi.hoisted(() => vi.fn());
const applyNewData = vi.hoisted(() => vi.fn());
const setStyles = vi.hoisted(() => vi.fn());
const createIndicator = vi.hoisted(() => vi.fn());
const getKlineHistory = vi.hoisted(() => vi.fn());

vi.mock('klinecharts', () => ({
  init: (...args: unknown[]) => {
    init(...args);
    return { applyNewData, setStyles, createIndicator, dispose };
  },
  dispose: (...args: unknown[]) => dispose(...args),
}));

vi.mock('../../../api/stocks', () => ({
  KLINE_DEFAULT_DAYS: 120,
  stocksApi: {
    getKlineHistory: (...args: unknown[]) => getKlineHistory(...args),
  },
}));

const sampleKlines: KLine[] = [
  { timestamp: 1, open: 10, high: 11, low: 9, close: 10.5, volume: 100, turnover: 1000 },
  { timestamp: 2, open: 10.5, high: 12, low: 10, close: 11.8, volume: 200, turnover: 2300 },
];

const renderPanel = async (props?: { market?: string; days?: number }) => {
  const { KLineChartPanel } = await import('../KLineChartPanel');
  render(<KLineChartPanel stockCode="600519" {...props} />);
};

describe('KLineChartPanel', () => {
  beforeEach(() => {
    init.mockReset();
    dispose.mockReset();
    applyNewData.mockReset();
    setStyles.mockReset();
    createIndicator.mockReset();
    getKlineHistory.mockReset();
  });

  afterEach(() => {
    vi.resetModules();
  });

  it('fetches with default days, inits the chart and applies fetched klines', async () => {
    getKlineHistory.mockResolvedValueOnce(sampleKlines);

    await renderPanel();

    await waitFor(() => expect(applyNewData).toHaveBeenCalledWith(sampleKlines));
    expect(getKlineHistory).toHaveBeenCalledWith('600519', 120, 'daily');
    expect(init).toHaveBeenCalledTimes(1);
    expect(createIndicator).toHaveBeenCalledWith('VOL', false, expect.any(Object));
  });

  it('shows empty state when backend returns no bars', async () => {
    getKlineHistory.mockResolvedValueOnce([]);

    await renderPanel();

    expect(await screen.findByText('暂无 K 线数据')).toBeInTheDocument();
    expect(applyNewData).not.toHaveBeenCalled();
  });

  it('shows error state when the fetch fails', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    getKlineHistory.mockRejectedValueOnce(new Error('network down'));

    await renderPanel();

    expect(await screen.findByText('K 线加载失败')).toBeInTheDocument();
    consoleError.mockRestore();
  });

  it('toggles up/down colors via setStyles when the color switch is clicked', async () => {
    getKlineHistory.mockResolvedValueOnce(sampleKlines);

    await renderPanel();

    await waitFor(() => expect(applyNewData).toHaveBeenCalled());
    setStyles.mockClear();

    fireEvent.click(screen.getByRole('button', { name: '切换涨跌颜色' }));

    expect(setStyles).toHaveBeenCalledTimes(1);
    const styleArg = setStyles.mock.calls[0][0] as { candle: { bar: { upColor: string } } };
    // 切到绿涨红跌后，up 应为绿色族（非默认红）。
    expect(styleArg.candle.bar.upColor).not.toBe('#ef4444');
  });
});
