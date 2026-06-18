import type React from 'react';
import type { ResonanceLevel, SignalMarker } from '../../types/kline';
import { cn } from '../../utils/cn';
import { formatCi, formatExcess, formatHitRate, verifiedLabel } from '../../utils/credibility';
import { resonanceLabel, resonanceTooltip } from '../../utils/resonance';

interface SignalDrilldownPanelProps {
  markers: SignalMarker[];
  onClose: () => void;
  resonance?: ResonanceLevel;
}

const formatNumber = (value: number | null): string =>
  value === null || Number.isNaN(value) ? '—' : String(value);

const formatAsOf = (asOf: number | null): string => {
  if (asOf === null) return '';
  return new Date(asOf).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
};

const directionLabel: Record<SignalMarker['direction'], string> = {
  bullish: '看多',
  bearish: '看空',
  neutral: '中性',
};

const MarkerCard: React.FC<{ marker: SignalMarker }> = ({ marker }) => {
  const isRule = marker.source === 'rule';
  return (
    <div
      data-testid="drilldown-marker"
      className={cn(
        'rounded-xl border border-border/60 bg-card/80 p-4',
        marker.isDailyApprox && 'opacity-70',
      )}
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-foreground">
          {isRule ? '规则信号' : 'LLM 结论'} · {directionLabel[marker.direction]}
        </span>
        <span className="text-xs text-secondary-text">{marker.confidence}</span>
      </div>

      <p className="mt-2 text-sm text-foreground">{marker.reason}</p>

      {isRule ? (
        <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-secondary-text">
          <span data-testid="drilldown-observed-value">
            观测值：{formatNumber(marker.observedValue)}
          </span>
          <span data-testid="drilldown-threshold">阈值：{formatNumber(marker.threshold)}</span>
        </div>
      ) : (
        <div className="mt-3 text-xs text-secondary-text" data-testid="drilldown-as-of">
          结论时间：{formatAsOf(marker.asOf) || '—'}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        <span data-testid="drilldown-hit-rate" className="text-secondary-text">
          {formatHitRate(marker)}
        </span>
        {formatCi(marker) !== null && (
          <span data-testid="drilldown-ci" className="text-secondary-text">
            {formatCi(marker)}
          </span>
        )}
        {formatExcess(marker.baselineExcess) !== null && (
          <span
            data-testid="drilldown-excess"
            className={marker.baselineExcess! > 0 ? 'text-success' : 'text-secondary-text'}
          >
            {formatExcess(marker.baselineExcess)}
          </span>
        )}
        <span
          data-testid="drilldown-verified"
          className={cn(marker.verified ? 'text-success' : 'text-secondary-text')}
        >
          {verifiedLabel(marker.verified)}
        </span>
      </div>
    </div>
  );
};

export const SignalDrilldownPanel: React.FC<SignalDrilldownPanelProps> = ({ markers, onClose, resonance = 'none' }) => (
  <div className="flex flex-col gap-3" role="group" aria-label="信号依据">
    <div className="flex items-center justify-between">
      <span className="label-uppercase">SIGNAL EVIDENCE</span>
      <div className="flex items-center gap-2">
        {resonanceLabel(resonance) && (
          <span
            data-testid="drilldown-resonance"
            aria-label={resonanceTooltip(resonance)}
            className="rounded bg-accent/15 px-1.5 py-0.5 text-xs text-accent"
          >{resonanceLabel(resonance)}</span>
        )}
        <button
          type="button"
          onClick={onClose}
          className="home-surface-button rounded-lg px-3 py-1 text-xs text-secondary-text"
        >
          关闭依据
        </button>
      </div>
    </div>
    {markers.map((marker) => (
      <MarkerCard key={`${marker.timestamp}:${marker.source}:${marker.signalType}`} marker={marker} />
    ))}
  </div>
);
