import type React from 'react';
import { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { historyApi } from '../api/history';
import { AppPage, InlineAlert, Loading } from '../components/common';
import { KLineChartPanel } from '../components/kline/KLineChartPanel';
import { ReportSummary } from '../components/report/ReportSummary';
import { ChartErrorBoundary } from '../components/workstation/ChartErrorBoundary';
import { StockHistoryPanel } from '../components/workstation/StockHistoryPanel';
import { StockSignalsPanel } from '../components/workstation/StockSignalsPanel';
import { StockWorkstationHeader } from '../components/workstation/StockWorkstationHeader';
import { useWatchlist } from '../hooks/useWatchlist';
import type { AnalysisReport } from '../types/analysis';
import { cn } from '../utils/cn';

type TabKey = 'signals' | 'report' | 'history' | 'alerts';
const TABS: { key: TabKey; label: string }[] = [
  { key: 'signals', label: '信号' },
  { key: 'report', label: '报告' },
  { key: 'history', label: '历史' },
  { key: 'alerts', label: '告警' },
];

const StockWorkstationPage: React.FC = () => {
  const params = useParams<{ code: string }>();
  const code = params.code ?? '';
  const watchlist = useWatchlist();

  const [tab, setTab] = useState<TabKey>('signals');
  const [report, setReport] = useState<AnalysisReport | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);

  // 记录已为哪个 code 完成过自动取报告，避免空记录死循环
  const reportLoadedForRef = useRef<string | null>(null);

  const loadReport = useCallback(async (recordId?: number) => {
    reportLoadedForRef.current = code;
    setReportLoading(true);
    setReportError(null);
    try {
      let id = recordId;
      if (id === undefined) {
        const list = await historyApi.getList({ stockCode: code, limit: 1 });
        id = list.items[0]?.id;
      }
      if (id === undefined) {
        setReport(null);
        return;
      }
      setReport(await historyApi.getDetail(id));
    } catch {
      setReportError('报告加载失败');
    } finally {
      setReportLoading(false);
    }
  }, [code]);

  // 切到报告 tab 且当前 code 尚未取过 → 取最新（每个 code 最多触发一次）
  useEffect(() => {
    if (tab === 'report' && reportLoadedForRef.current !== code) void loadReport();
  }, [tab, code, loadReport]);

  const onSelectHistory = useCallback((recordId: number) => {
    setTab('report');
    void loadReport(recordId);
  }, [loadReport]);

  if (!code) {
    return (
      <AppPage className="space-y-4 pb-12 pt-6">
        <InlineAlert variant="danger" message="未找到该标的（缺少代码）。" />
      </AppPage>
    );
  }

  return (
    <AppPage className="space-y-4 pb-12 pt-6">
      <StockWorkstationHeader code={code} watchlist={watchlist} onRefreshAnalysis={() => {}} onBuildAlert={() => {}} />
      <section className="rounded-lg border border-border bg-card p-2">
        <ChartErrorBoundary>
          <Suspense fallback={<div className="h-64 animate-pulse rounded bg-hover" />}>
            <KLineChartPanel stockCode={code} />
          </Suspense>
        </ChartErrorBoundary>
      </section>

      {/* Tab 栏 */}
      <div role="tablist" className="flex gap-2 border-b border-border">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={cn(
              'px-3 py-2 text-sm',
              tab === t.key ? 'border-b-2 border-foreground text-foreground' : 'text-secondary-text',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab 内容 */}
      <div className="pt-3">
        {tab === 'signals' && <StockSignalsPanel code={code} />}
        {tab === 'report' && (
          reportLoading
            ? <Loading label="正在加载报告" />
            : reportError
              ? <InlineAlert variant="danger" message={reportError} />
              : report
                ? (
                  <ReportSummary
                    data={report}
                    isHistory
                    watchlist={{
                      isInWatchlist: watchlist.isInWatchlist,
                      onToggle: watchlist.toggleWatchlist,
                      isActioning: watchlist.isActioning,
                      actionMessage: watchlist.actionMessage,
                    }}
                  />
                )
                : <div data-testid="report-empty" className="text-sm text-secondary-text">尚无分析，点上方「刷新分析」生成。</div>
        )}
        {tab === 'history' && <StockHistoryPanel code={code} onSelect={onSelectHistory} />}
        {tab === 'alerts' && <div data-testid="alerts-slot" />}
      </div>
    </AppPage>
  );
};

export default StockWorkstationPage;
