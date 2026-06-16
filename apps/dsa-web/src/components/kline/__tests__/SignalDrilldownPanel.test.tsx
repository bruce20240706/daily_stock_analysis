import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { SignalMarker } from '../../../types/kline';
import { SignalDrilldownPanel } from '../SignalDrilldownPanel';

const ruleMarker: SignalMarker = {
  timestamp: 1718323200000,
  price: 1700,
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
};

const llmMarker: SignalMarker = {
  ...ruleMarker,
  source: 'llm',
  signalType: 'llm_advice',
  anchor: 'high',
  confidence: 'medium',
  reason: '建议买入，回踩不破支撑',
  threshold: null,
  observedValue: null,
  hitRate: null,
  hitSample: null,
  verified: false,
  asOf: 1718236800000,
};

describe('SignalDrilldownPanel', () => {
  it('shows indicator value, threshold and hit rate for a rule marker', () => {
    render(<SignalDrilldownPanel markers={[ruleMarker]} onClose={vi.fn()} />);

    expect(screen.getByText('放量突破20日高')).toBeInTheDocument();
    expect(screen.getByTestId('drilldown-observed-value')).toHaveTextContent('2.4');
    expect(screen.getByTestId('drilldown-threshold')).toHaveTextContent('2');
    expect(screen.getByTestId('drilldown-hit-rate')).toHaveTextContent('62%');
    expect(screen.getByTestId('drilldown-hit-rate')).toHaveTextContent('18');
    expect(screen.getByTestId('drilldown-verified')).toHaveTextContent('已验证');
  });

  it('shows advice and as_of for an llm marker and marks unverified hit rate', () => {
    render(<SignalDrilldownPanel markers={[llmMarker]} onClose={vi.fn()} />);

    expect(screen.getByText('建议买入，回踩不破支撑')).toBeInTheDocument();
    expect(screen.getByTestId('drilldown-as-of')).toBeInTheDocument();
    expect(screen.getByTestId('drilldown-hit-rate')).toHaveTextContent('暂无样本');
    expect(screen.getByTestId('drilldown-verified')).toHaveTextContent('未验证');
  });

  it('lists both markers when a merged/conflict glyph is opened and closes via the handler', () => {
    const onClose = vi.fn();
    render(<SignalDrilldownPanel markers={[ruleMarker, llmMarker]} onClose={onClose} />);

    expect(screen.getAllByTestId('drilldown-marker')).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: '关闭依据' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
