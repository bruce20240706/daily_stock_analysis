// apps/dsa-web/src/components/workstation/__tests__/StockSignalsPanel.test.tsx
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
const { getSignals } = vi.hoisted(() => ({ getSignals: vi.fn() }));
vi.mock('../../../api/stocks', () => ({ stocksApi: { getSignals } }));
import { StockSignalsPanel } from '../StockSignalsPanel';

const baseMarkerFull = { timestamp: 1, price: 1700, anchor: 'low', direction: 'bullish', signalType: 'volume_breakout',
  source: 'rule', confidence: 'high', isDailyApprox: false, isAnomalous: false, reason: '放量突破',
  threshold: null, observedValue: null, hitRate: 0.62, hitSample: 18, verified: true,
  ciLow: null, ciHigh: null, baselineExcess: null, ciLowCorrected: null, familySize: null, asOf: null };

const sig = (over = {}) => ({ status: 'ok', consistency: 'consistent', degradedReason: null,
  priceLines: { entry: 1700, stop: 1620, target: 1850 },
  markers: [baseMarkerFull],
  ...over });

describe('StockSignalsPanel', () => {
  afterEach(() => { getSignals.mockReset(); });
  it('renders consistency, price lines and markers after fetch', async () => {
    getSignals.mockResolvedValueOnce(sig());
    render(<StockSignalsPanel code="600519" />);
    expect(await screen.findByText('volume_breakout')).toBeInTheDocument();
    expect(screen.getByText(/一致/)).toBeInTheDocument();
    expect(screen.getByText(/1700/)).toBeInTheDocument();
  });
  it('shows an error state when signals fetch fails', async () => {
    getSignals.mockRejectedValueOnce(new Error('boom'));
    render(<StockSignalsPanel code="600519" />);
    expect(await screen.findByText(/信号加载失败/)).toBeInTheDocument();
  });
  it('renders empty state when markers array is empty', async () => {
    getSignals.mockResolvedValueOnce(sig({ markers: [] }));
    render(<StockSignalsPanel code="600519" />);
    expect(await screen.findByText(/暂无量价信号/)).toBeInTheDocument();
  });
  it('renders 暂无样本 when hitRate and hitSample are null', async () => {
    getSignals.mockResolvedValueOnce(sig({ markers: [{ ...baseMarkerFull, hitRate: null, hitSample: null }] }));
    render(<StockSignalsPanel code="600519" />);
    expect(await screen.findByText('暂无样本')).toBeInTheDocument();
  });

  it('shows CI band and excess in the hit cell', async () => {
    getSignals.mockResolvedValueOnce(sig({ markers: [{ ...baseMarkerFull, ciLow: 0.55, ciHigh: 0.8, baselineExcess: 0.05 }] }));
    render(<StockSignalsPanel code="600519" />);
    expect(await screen.findByTestId('signals-ci')).toHaveTextContent('[55%–80%]');
    expect(screen.getByTestId('signals-excess')).toHaveTextContent('超额 +5pp');
  });

  it('shows verified indicator in the hit cell', async () => {
    getSignals.mockResolvedValueOnce(sig());
    render(<StockSignalsPanel code="600519" />);
    expect(await screen.findByTestId('signals-verified')).toHaveTextContent('已验证');
  });

  it('omits CI and excess spans when values are null', async () => {
    getSignals.mockResolvedValueOnce(sig());
    render(<StockSignalsPanel code="600519" />);
    await screen.findByText('volume_breakout');
    expect(screen.queryByTestId('signals-ci')).not.toBeInTheDocument();
    expect(screen.queryByTestId('signals-excess')).not.toBeInTheDocument();
  });
});
