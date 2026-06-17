// apps/dsa-web/src/components/workstation/__tests__/StockSignalsPanel.test.tsx
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
const { getSignals } = vi.hoisted(() => ({ getSignals: vi.fn() }));
vi.mock('../../../api/stocks', () => ({ stocksApi: { getSignals } }));
import { StockSignalsPanel } from '../StockSignalsPanel';

const sig = (over = {}) => ({ status: 'ok', consistency: 'consistent', degradedReason: null,
  priceLines: { entry: 1700, stop: 1620, target: 1850 },
  markers: [{ timestamp: 1, price: 1700, anchor: 'low', direction: 'bullish', signalType: 'volume_breakout',
    source: 'rule', confidence: 'high', isDailyApprox: false, isAnomalous: false, reason: '放量突破',
    threshold: null, observedValue: null, hitRate: 0.62, hitSample: 18, verified: true, asOf: null }],
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
});
