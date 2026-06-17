// apps/dsa-web/src/components/workstation/StockHistoryPanel.tsx
import { useEffect, useState } from 'react';
import type React from 'react';
import { historyApi } from '../../api/history';
import type { HistoryItem } from '../../types/analysis';
import { Loading } from '../common';

export const StockHistoryPanel: React.FC<{ code: string; onSelect: (recordId: number) => void }> = ({ code, onSelect }) => {
  const [items, setItems] = useState<HistoryItem[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    void (async () => {
      setError(false); setItems(null);
      try { const r = await historyApi.getList({ stockCode: code, limit: 20 }); if (alive) setItems(r.items); }
      catch { if (alive) setError(true); }
    })();
    return () => { alive = false; };
  }, [code]);

  if (error) return <div className="text-sm text-secondary-text">历史加载失败</div>;
  if (items === null) return <Loading label="正在加载历史" />;
  if (items.length === 0) return <div className="text-sm text-secondary-text">尚无分析记录，点上方「刷新分析」生成。</div>;

  return (
    <ul className="divide-y divide-border/60 text-sm">
      {items.map((it) => (
        <li key={it.id}>
          <button type="button" onClick={() => onSelect(it.id)}
            className="flex w-full items-center justify-between py-2 text-left hover:bg-hover">
            <span className="text-foreground">{it.operationAdvice ?? '—'}</span>
            <span className="text-xs text-secondary-text">{it.createdAt.slice(0, 10)}{it.sentimentScore != null ? ` · 情绪 ${it.sentimentScore}` : ''}</span>
          </button>
        </li>
      ))}
    </ul>
  );
};
