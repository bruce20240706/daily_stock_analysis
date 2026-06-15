import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const renderDrawer = async (overrides?: { onClose?: () => void; isOpen?: boolean }) => {
  const { KLineDrawer } = await import('../KLineDrawer');
  const onClose = overrides?.onClose ?? vi.fn();
  render(
    <KLineDrawer
      stockCode="600519"
      stockName="贵州茅台"
      isOpen={overrides?.isOpen ?? true}
      onClose={onClose}
    />,
  );
  return onClose;
};

describe('KLineDrawer', () => {
  afterEach(() => {
    vi.doUnmock('../KLineChartPanel');
    vi.resetModules();
  });

  it('renders nothing when closed', async () => {
    vi.resetModules();
    vi.doMock('../KLineChartPanel', () => ({
      default: () => <div data-testid="kline-panel" />,
    }));

    await renderDrawer({ isOpen: false });

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('loads the lazy chart panel inside the drawer when open', async () => {
    vi.resetModules();
    vi.doMock('../KLineChartPanel', () => ({
      default: () => <div data-testid="kline-panel">chart here</div>,
    }));

    await renderDrawer();

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(await screen.findByTestId('kline-panel')).toBeInTheDocument();
  });

  it('keeps a rejected lazy import inside the drawer (chunk failure fallback)', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.resetModules();
    vi.doMock('../KLineChartPanel', () => Promise.reject(new Error('chunk load failed')));

    try {
      await renderDrawer();

      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(await screen.findByText('K 线加载失败')).toBeInTheDocument();
    } finally {
      consoleError.mockRestore();
    }
  });

  it('keeps panel render errors inside the drawer and closes via the handler', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const onClose = vi.fn();
    vi.resetModules();
    vi.doMock('../KLineChartPanel', () => ({
      default: () => {
        throw new Error('panel render failed');
      },
    }));

    try {
      await renderDrawer({ onClose });

      expect(await screen.findByText('K 线加载失败')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: '关闭' }));

      await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    } finally {
      consoleError.mockRestore();
    }
  });
});
