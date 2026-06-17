import type React from 'react';
import type { SignalMarker } from '../../types/kline';
import { cn } from '../../utils/cn';
import { formatHitRate } from '../../utils/credibility';

interface SignalDrilldownPanelProps {
  markers: SignalMarker[];
  onClose: () => void;
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

      <div className="mt-3 flex items-center justify-between text-xs">
        <span data-testid="drilldown-hit-rate" className="text-secondary-text">
          {formatHitRate(marker)}
        </span>
        <span
          data-testid="drilldown-verified"
          className={cn(marker.verified ? 'text-success' : 'text-secondary-text')}
        >
          {marker.verified ? '已验证' : '未验证'}
        </span>
      </div>
    </div>
  );
};

export const SignalDrilldownPanel: React.FC<SignalDrilldownPanelProps> = ({ markers, onClose }) => (
  <div className="flex flex-col gap-3" role="group" aria-label="信号依据">
    <div className="flex items-center justify-between">
      <span className="label-uppercase">SIGNAL EVIDENCE</span>
      <button
        type="button"
        onClick={onClose}
        className="home-surface-button rounded-lg px-3 py-1 text-xs text-secondary-text"
      >
        关闭依据
      </button>
    </div>
    {markers.map((marker) => (
      <MarkerCard key={`${marker.timestamp}:${marker.source}:${marker.signalType}`} marker={marker} />
    ))}
  </div>
);
