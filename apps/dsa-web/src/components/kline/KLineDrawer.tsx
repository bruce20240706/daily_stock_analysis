import type React from 'react';
import { Component, lazy, Suspense, useCallback, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Drawer } from '../common/Drawer';

interface KLineDrawerProps {
  stockCode: string;
  stockName?: string;
  market?: string;
  isOpen: boolean;
  onClose: () => void;
}

interface KLineDrawerErrorBoundaryProps {
  resetKey: string;
  fallback: React.ReactNode;
  children: React.ReactNode;
}

interface KLineDrawerErrorBoundaryState {
  hasError: boolean;
}

class KLineDrawerErrorBoundary extends Component<
  KLineDrawerErrorBoundaryProps,
  KLineDrawerErrorBoundaryState
> {
  state: KLineDrawerErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): KLineDrawerErrorBoundaryState {
    return { hasError: true };
  }

  componentDidUpdate(prevProps: KLineDrawerErrorBoundaryProps) {
    if (prevProps.resetKey !== this.props.resetKey && this.state.hasError) {
      this.setState({ hasError: false });
    }
  }

  componentDidCatch(error: unknown) {
    console.error('KLine drawer failed:', error);
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback;
    }
    return this.props.children;
  }
}

const KLineLoadingState: React.FC = () => (
  <div className="flex h-64 flex-col items-center justify-center">
    <div className="home-spinner h-10 w-10 animate-spin border-[3px]" />
    <p className="mt-4 text-sm text-secondary-text">K 线加载中…</p>
  </div>
);

const KLineErrorState: React.FC<{ onRequestClose: () => void }> = ({ onRequestClose }) => (
  <div className="flex h-64 flex-col items-center justify-center">
    <p className="text-sm text-danger">K 线加载失败</p>
    <button
      type="button"
      onClick={onRequestClose}
      className="home-surface-button mt-4 rounded-lg px-4 py-2 text-sm text-secondary-text"
    >
      关闭
    </button>
  </div>
);

/**
 * K 线抽屉壳：Drawer + lazy 重面板 + ErrorBoundary，仿 ReportMarkdownDrawer。
 * klinecharts 仅在 lazy 的 KLineChartPanel 内 import，保证首屏不受影响；
 * 面板加载/渲染失败被错误边界兜底，不外溢、不影响其余页面（spec 6 降级护栏）。
 */
export const KLineDrawer: React.FC<KLineDrawerProps> = ({
  stockCode,
  stockName,
  market,
  isOpen,
  onClose,
}) => {
  const LazyKLineChartPanel = useMemo(
    () => lazy(() => import('./KLineChartPanel')),
    [],
  );

  const handleClose = useCallback(() => {
    onClose();
  }, [onClose]);

  if (!isOpen) return null;

  const title = stockName ? `${stockName} ${stockCode}` : stockCode;

  return (
    <Drawer
      isOpen={isOpen}
      onClose={handleClose}
      title={title}
      width="max-w-4xl"
      zIndex={100}
      backdropClassName="bg-background/56 backdrop-blur-[2px]"
    >
      <div className="flex h-[70vh] flex-col">
        <div className="flex justify-end pb-2">
          <Link
            to={'/stock/' + encodeURIComponent(stockCode)}
            onClick={handleClose}
            className="text-xs text-secondary-text hover:text-foreground"
          >
            在工作台打开 ↗
          </Link>
        </div>
        <KLineDrawerErrorBoundary
          resetKey={stockCode}
          fallback={<KLineErrorState onRequestClose={handleClose} />}
        >
          <Suspense fallback={<KLineLoadingState />}>
            <LazyKLineChartPanel stockCode={stockCode} market={market} />
          </Suspense>
        </KLineDrawerErrorBoundary>
      </div>
    </Drawer>
  );
};

export default KLineDrawer;
