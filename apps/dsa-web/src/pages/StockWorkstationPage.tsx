import type React from 'react';
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { analysisApi, DuplicateTaskError } from '../api/analysis';
import { historyApi } from '../api/history';
import { AppPage, InlineAlert, Loading } from '../components/common';
import { ReportSummary } from '../components/report/ReportSummary';
import { ChartErrorBoundary } from '../components/workstation/ChartErrorBoundary';
import { StockAlertsPanel } from '../components/workstation/StockAlertsPanel';
import { StockHistoryPanel } from '../components/workstation/StockHistoryPanel';
import { StockSignalsPanel } from '../components/workstation/StockSignalsPanel';
import { StockWorkstationHeader } from '../components/workstation/StockWorkstationHeader';
import { useWatchlist } from '../hooks/useWatchlist';
import type { AnalysisReport } from '../types/analysis';
import { cn } from '../utils/cn';

// klinecharts 是重依赖，仅在 lazy 的 KLineChartPanel 内 import，保证页面首屏不受影响（对齐 KLineDrawer）。
const KLineChartPanel = lazy(() => import('../components/kline/KLineChartPanel'));

// 轮询预算：120 次 × 2s ≈ 240s，对齐 HomePage；detailed LLM 分析常超 60s。
const POLL_MAX_ATTEMPTS = 120;

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
  if (!code) {
    return (
      <AppPage className="space-y-4 pb-12 pt-6">
        <InlineAlert variant="danger" message="未找到该标的（缺少代码）。" />
      </AppPage>
    );
  }
  // 用 key={code} 强制换股时整体重挂：重置全部页面状态并卸载子组件（取消其在途请求），
  // 消除 React Router 复用实例导致的跨股状态泄漏 / 轮询把 A 的结果写进 B。
  return <StockWorkstationView key={code} code={code} />;
};

const StockWorkstationView: React.FC<{ code: string }> = ({ code }) => {
  const watchlist = useWatchlist();

  const [tab, setTab] = useState<TabKey>('signals');
  const [report, setReport] = useState<AnalysisReport | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // 记录已为哪个 code 完成过自动取报告，避免空记录死循环
  const reportLoadedForRef = useRef<string | null>(null);

  // 卸载守卫：防止组件卸载后 setState 导致内存泄漏或状态污染
  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => { isMountedRef.current = false; };
  }, []);

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

  const pollUntilDone = useCallback(async (taskId: string) => {
    let consecutiveFailures = 0;
    for (let i = 0; i < POLL_MAX_ATTEMPTS; i += 1) {
      if (!isMountedRef.current) return;
      let st: Awaited<ReturnType<typeof analysisApi.getStatus>>;
      try {
        st = await analysisApi.getStatus(taskId);
      } catch {
        // 单次瞬断不应中断整个任务（后端任务仍在跑）；连续失败到阈值才报错。
        consecutiveFailures += 1;
        if (consecutiveFailures >= 5) {
          if (isMountedRef.current) {
            setReportError('分析状态获取失败，请稍后重试');
            setTab('report');
          }
          return;
        }
        await new Promise<void>((r) => { window.setTimeout(r, 2000); });
        continue;
      }
      consecutiveFailures = 0;
      if (!isMountedRef.current) return;
      if (st.status === 'completed') {
        if (st.result?.report) setReport(st.result.report);
        else setReportError('分析完成但未返回报告内容');
        setTab('report');
        return;
      }
      if (st.status === 'failed') {
        setReportError(st.error ?? '分析失败');
        setTab('report');
        return;
      }
      await new Promise<void>((r) => { window.setTimeout(r, 2000); });
    }
    // 轮询耗尽（~240s）仍未完成，提示超时
    if (isMountedRef.current) {
      setReportError('分析超时，请稍后重试');
      setTab('report');
    }
  }, []);

  const runAnalysis = useCallback(async () => {
    // 提前标记 sentinel，防止切到 report tab 后自动 2-hop fetch 覆盖分析结果
    reportLoadedForRef.current = code;
    setRefreshing(true);
    // 分析期间报告区也进入 loading，避免切到「报告」tab 看到空态（finding #6）
    setReportLoading(true);
    setReportError(null);
    try {
      const resp = await analysisApi.analyzeAsync({ stockCode: code, reportType: 'detailed', forceRefresh: true });
      const taskId = 'taskId' in resp ? resp.taskId : undefined;
      if (taskId) await pollUntilDone(taskId);
    } catch (e) {
      if (e instanceof DuplicateTaskError) {
        try { await pollUntilDone(e.existingTaskId); }
        catch { if (isMountedRef.current) { setReportError('分析失败'); setTab('report'); } }
      } else {
        setReportError('发起分析失败');
        setTab('report');
      }
    } finally {
      setRefreshing(false);
      setReportLoading(false);
    }
  }, [code, pollUntilDone]);

  return (
    <AppPage className="space-y-4 pb-12 pt-6">
      <StockWorkstationHeader
        code={code}
        watchlist={watchlist}
        onRefreshAnalysis={() => void runAnalysis()}
        onBuildAlert={() => setTab('alerts')}
        refreshing={refreshing}
      />
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
        {tab === 'alerts' && <StockAlertsPanel code={code} />}
      </div>
    </AppPage>
  );
};

export default StockWorkstationPage;
