import type React from 'react';
import { Component } from 'react';
import type { ErrorInfo } from 'react';

type ChartErrorBoundaryProps = {
  children: React.ReactNode;
};

type ChartErrorBoundaryState = {
  hasError: boolean;
};

/**
 * 工作台 K 线区专用错误边界：图表渲染或懒加载失败时降级为静音占位，
 * 不影响页面其余区域。仅供工作台内部使用。
 */
export class ChartErrorBoundary extends Component<ChartErrorBoundaryProps, ChartErrorBoundaryState> {
  override state: ChartErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ChartErrorBoundaryState {
    return { hasError: true };
  }

  override componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('Workstation K-line chart failed to render or load', error, errorInfo);
  }

  override render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    return (
      <div className="flex h-64 items-center justify-center rounded text-sm text-secondary-text">
        图表加载失败
      </div>
    );
  }
}

export default ChartErrorBoundary;
