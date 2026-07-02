import type React from 'react';
import { useNavigate } from 'react-router-dom';
import type { BoardEntry } from '../../types/kline';
import { cn } from '../../utils/cn';
import { formatCi, formatExcess, formatHitRate, formatHorizon, markerStatusLabel, planQualityLabel, unverifiedExcessNote, verifiedLabel } from '../../utils/credibility';
import { resonanceLabel, resonanceTooltip } from '../../utils/resonance';

const dirLabel: Record<string, string> = { bullish: '看多', bearish: '看空', neutral: '中性' };
const consistencyLabel: Record<BoardEntry['consistency'], string> = {
  consistent: '一致', divergent: '分歧', conflict: '冲突', unknown: '未知', stale: '过期',
};
const fmt = (v: number | null) => (v === null || Number.isNaN(v) ? '—' : String(v));

interface GroupProps { groupKey: string; title: string; entries: BoardEntry[]; onRowClick: (c: string, n?: string) => void; }

export const SignalBoardGroup: React.FC<GroupProps> = ({ groupKey, title, entries, onRowClick }) => {
  const navigate = useNavigate();
  if (entries.length === 0) return null;
  // default sort: hit rate descending, no-sample rows sink to the bottom
  const sorted = [...entries].sort((a, b) => (b.hitRate ?? -1) - (a.hitRate ?? -1));
  return (
    <section data-testid={`group-${groupKey}`} className="mb-5">
      <div className="label-uppercase mb-2">{title}（{entries.length}）</div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-secondary-text">
            <th className="text-left">标的</th><th>规则</th><th>LLM</th><th>一致性</th>
            <th className="text-left">关键量价信号</th><th>入/损/标</th><th>命中率</th>
            <th>计划/窗口/状态</th><th>工作台</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((e) => {
            const note = unverifiedExcessNote({
              verified: e.verified, baselineExcess: e.baselineExcess,
              ciLowCorrected: e.ciLowCorrected, familySize: e.familySize,
            });
            return (
            <tr key={e.code} data-testid="board-row"
                onClick={() => onRowClick(e.code, e.name ?? undefined)}
                className="cursor-pointer border-t border-border/60 hover:bg-hover">
              <td className="py-1.5 text-left text-foreground">{e.name ?? e.code}</td>
              <td className={cn(e.ruleDirection === 'bullish' && 'text-danger', e.ruleDirection === 'bearish' && 'text-success')}>
                {e.ruleDirection ? dirLabel[e.ruleDirection] : '—'}</td>
              <td>{e.llmDirection ? dirLabel[e.llmDirection] : '—'}</td>
              <td className={cn(e.consistency === 'conflict' && 'text-danger')}>{consistencyLabel[e.consistency]}</td>
              <td className="text-left text-secondary-text">
                {e.status === 'degraded' && e.degradedReason
                  ? e.degradedReason
                  : (e.keySignals.join('·') || '—')}
              </td>
              <td className="text-secondary-text">{fmt(e.priceLines.entry)}/{fmt(e.priceLines.stop)}/{fmt(e.priceLines.target)}</td>
              <td>
                <span className="text-secondary-text">{formatHitRate(e)}</span>{' '}
                {formatCi(e) !== null && (
                  <span data-testid="board-ci" className="mr-1 text-secondary-text">{formatCi(e)}</span>
                )}
                {formatExcess(e.baselineExcess) !== null && (
                  <span
                    data-testid="board-excess"
                    className={cn('mr-1', e.baselineExcess! > 0 ? 'text-success' : 'text-secondary-text')}
                  >{formatExcess(e.baselineExcess)}</span>
                )}
                <span className={cn(e.verified ? 'text-success' : 'text-secondary-text')}>{verifiedLabel(e.verified)}</span>
                {note && (
                  <span data-testid="board-corrected-note" className="ml-1 text-secondary-text">
                    ({note})
                  </span>
                )}
                {resonanceLabel(e.resonance) && (
                  <span
                    data-testid="board-resonance"
                    aria-label={resonanceTooltip(e.resonance)}
                    className="ml-1 rounded bg-accent/15 px-1 text-accent"
                  >{resonanceLabel(e.resonance)}</span>
                )}
              </td>
              <td>
                {e.planQuality != null && (
                  <span data-testid="board-plan-quality">{planQualityLabel(e.planQuality)}</span>
                )}
                {e.horizonBars != null && (
                  <span data-testid="board-horizon">{formatHorizon(e.horizonBars)}</span>
                )}
                {e.signalStatus != null && (
                  <span data-testid="board-signal-status">{markerStatusLabel(e.signalStatus)}</span>
                )}
              </td>
              <td>
                <button
                  type="button"
                  onClick={(ev) => { ev.stopPropagation(); navigate('/stock/' + encodeURIComponent(e.code)); }}
                  className="text-xs text-secondary-text hover:text-foreground"
                >
                  工作台 ↗
                </button>
              </td>
            </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
};
