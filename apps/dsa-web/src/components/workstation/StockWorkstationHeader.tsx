import { useCallback, useEffect, useState } from 'react';
import type React from 'react';
import { stocksApi } from '../../api/stocks';
import type { StockQuote } from '../../types/kline';
import { Button } from '../common';
import { cn } from '../../utils/cn';

interface StockWorkstationHeaderProps {
  code: string;
  watchlist: { isInWatchlist: (code: string) => boolean; toggleWatchlist: (code: string) => Promise<void>; isActioning: boolean; };
  onRefreshAnalysis: () => void;
  onBuildAlert: () => void;
  refreshing?: boolean;
}

const fmtPct = (v: number | null) => (v === null ? '' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`);

export const StockWorkstationHeader: React.FC<StockWorkstationHeaderProps> = ({ code, watchlist, onRefreshAnalysis, onBuildAlert, refreshing = false }) => {
  const [quote, setQuote] = useState<StockQuote | null>(null);
  const [copied, setCopied] = useState(false);
  const starred = watchlist.isInWatchlist(code);

  useEffect(() => {
    let alive = true;
    void stocksApi.getQuote(code).then((q) => { if (alive) setQuote(q); }).catch(() => { /* 行情失败不阻塞页面 */ });
    return () => { alive = false; };
  }, [code]);

  const up = (quote?.changePercent ?? 0) >= 0;
  const copyLink = useCallback(async () => {
    const url = `${window.location.origin}/stock/${encodeURIComponent(code)}`;
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    }
  }, [code]);

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
      <div className="flex items-baseline gap-3">
        <span className="text-2xl font-bold text-foreground">{quote?.stockName ?? code}</span>
        {quote?.stockName != null && <span className="text-sm text-secondary-text">{code}</span>}
        {quote && (
          <span className={cn('text-lg font-semibold', up ? 'text-danger' : 'text-success')}>
            {quote.currentPrice}
            {quote.changePercent !== null && <span className="ml-2 text-sm">{fmtPct(quote.changePercent)}</span>}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={() => void watchlist.toggleWatchlist(code)} disabled={watchlist.isActioning}>{starred ? '★ 已自选' : '☆ 自选'}</Button>
        <Button variant="ghost" size="sm" onClick={onRefreshAnalysis} disabled={refreshing} isLoading={refreshing} loadingText="分析中…">刷新分析</Button>
        <Button variant="ghost" size="sm" onClick={onBuildAlert}>建告警</Button>
        <Button variant="ghost" size="sm" onClick={() => void copyLink()}>{copied ? '已复制' : '复制链接'}</Button>
      </div>
    </header>
  );
};
