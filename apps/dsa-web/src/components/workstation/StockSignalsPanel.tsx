// apps/dsa-web/src/components/workstation/StockSignalsPanel.tsx
import { useCallback, useEffect, useState } from 'react';
import type React from 'react';
import { stocksApi } from '../../api/stocks';
import type { SignalsResponse } from '../../types/kline';
import { Loading } from '../common';
import { cn } from '../../utils/cn';

const consistencyLabel: Record<string, string> = {
  consistent: '一致', divergent: '分歧', conflict: '冲突', unknown: '未知', stale: '过期',
};
const dirLabel: Record<string, string> = { bullish: '看多', bearish: '看空', neutral: '中性' };
const fmt = (v: number | null) => (v === null ? '—' : String(v));

export const StockSignalsPanel: React.FC<{ code: string }> = ({ code }) => {
  const [data, setData] = useState<SignalsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      setData(await stocksApi.getSignals(code));
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [code]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <Loading label="正在加载信号" />;
  if (error || !data) return <div className="text-sm text-secondary-text">信号加载失败</div>;
  const pl = data.priceLines;
  return (
    <div className="space-y-3 text-sm">
      <div className="text-secondary-text">一致性：{consistencyLabel[data.consistency] ?? data.consistency}
        {' · '}入/损/标：{fmt(pl.entry)}/{fmt(pl.stop)}/{fmt(pl.target)}</div>
      <table className="w-full">
        <thead><tr className="text-xs text-secondary-text"><th className="text-left">信号</th><th>方向</th><th>来源</th><th>命中率</th></tr></thead>
        <tbody>
          {data.markers.map((m, i) => (
            <tr key={`${m.signalType}-${m.timestamp}-${i}`} className="border-t border-border/60">
              <td className="py-1 text-left">{m.signalType}</td>
              <td className={cn(m.direction === 'bullish' && 'text-danger', m.direction === 'bearish' && 'text-success')}>{dirLabel[m.direction]}</td>
              <td className="text-secondary-text">{m.source === 'llm' ? 'LLM' : '规则'}</td>
              <td className="text-secondary-text">{m.hitRate === null || m.hitSample === null || m.hitSample <= 0 ? '暂无样本' : `${Math.round(m.hitRate * 100)}% · ${m.verified ? '已验证' : '未验证'}`}</td>
            </tr>
          ))}
          {data.markers.length === 0 && <tr><td colSpan={4} className="py-2 text-secondary-text">暂无量价信号</td></tr>}
        </tbody>
      </table>
    </div>
  );
};
