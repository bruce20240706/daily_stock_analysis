import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import BacktestPage from '../BacktestPage';

const {
  mockGetResults,
  mockGetOverallPerformance,
  mockGetStockPerformance,
  mockRun,
} = vi.hoisted(() => ({
  mockGetResults: vi.fn(),
  mockGetOverallPerformance: vi.fn(),
  mockGetStockPerformance: vi.fn(),
  mockRun: vi.fn(),
}));

vi.mock('../../api/backtest', () => ({
  backtestApi: {
    getResults: mockGetResults,
    getOverallPerformance: mockGetOverallPerformance,
    getStockPerformance: mockGetStockPerformance,
    run: mockRun,
  },
}));

const basePerformance = {
  scope: 'overall',
  evalWindowDays: 10,
  engineVersion: 'test-engine',
  totalEvaluations: 3,
  completedCount: 2,
  insufficientCount: 1,
  longCount: 2,
  cashCount: 1,
  winCount: 1,
  lossCount: 1,
  neutralCount: 0,
  directionAccuracyPct: 66.7,
  winRatePct: 50,
  neutralRatePct: 0,
  avgStockReturnPct: 2.4,
  avgSimulatedReturnPct: 1.2,
  stopLossTriggerRate: 10,
  takeProfitTriggerRate: 20,
  ambiguousRate: 0,
  avgDaysToFirstHit: 3.5,
  adviceBreakdown: {},
  diagnostics: {},
};

const perpRowBase = {
  analysisHistoryId: 201,
  code: 'BTC/USDT:PERP',
  stockName: 'BTC 永续',
  analysisDate: '2026-05-20',
  evalWindowDays: 10,
  engineVersion: 'test-engine',
  evalStatus: 'completed',
  operationAdvice: '卖出',
  trendPrediction: '看空',
  actualMovement: 'down',
  actualReturnPct: -5.2,
  directionExpected: 'down',
  directionCorrect: true,
  outcome: 'win',
};

function mockSingleRow(row: Record<string, unknown>) {
  mockGetResults.mockResolvedValue({ total: 1, page: 1, limit: 20, items: [row] });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetOverallPerformance.mockResolvedValue(basePerformance);
  mockGetStockPerformance.mockResolvedValue(null);
  mockGetResults.mockResolvedValue({
    total: 1,
    page: 1,
    limit: 20,
    items: [
      {
        analysisHistoryId: 101,
        code: '600519',
        stockName: '贵州茅台',
        analysisDate: '2026-03-20',
        evalWindowDays: 10,
        engineVersion: 'test-engine',
        evalStatus: 'completed',
        operationAdvice: '继续持有',
        trendPrediction: '震荡偏多',
        actualMovement: 'up',
        actualReturnPct: 3.8,
        directionExpected: 'long',
        directionCorrect: true,
        outcome: 'win',
        simulatedReturnPct: 3.8,
      },
    ],
  });
  mockRun.mockResolvedValue({
    processed: 1,
    saved: 1,
    completed: 1,
    insufficient: 0,
    errors: 0,
  });
});

describe('BacktestPage', () => {
  it('renders shared surface inputs and prediction tracking outputs', async () => {
    render(<BacktestPage />);

    const filterInput = await screen.findByPlaceholderText('按股票代码筛选（留空表示全部）');
    const windowInput = screen.getByPlaceholderText('10');

    expect(filterInput).toHaveClass('input-surface');
    expect(filterInput).toHaveClass('input-focus-glow');
    expect(windowInput).toHaveClass('input-surface');
    expect(windowInput).toHaveClass('input-focus-glow');

    expect(await screen.findByText('盈利')).toBeInTheDocument();
    expect(screen.getByText('已完成')).toBeInTheDocument();
    expect(screen.getByText('600519')).toBeInTheDocument();
    expect(screen.getByText('贵州茅台')).toBeInTheDocument();
    expect(screen.getByText('震荡偏多')).toBeInTheDocument();
    expect(screen.getByText('上涨')).toBeInTheDocument();
    expect(screen.getByText('窗口收益')).toBeInTheDocument();
    expect(screen.getByText('方向匹配')).toBeInTheDocument();
    expect(screen.getByText('做多')).toBeInTheDocument();
    expect(screen.getAllByLabelText('是').length).toBeGreaterThan(0);
    expect(screen.getByText('方向准确率')).toBeInTheDocument();
    expect(screen.getByText('平均模拟收益')).toBeInTheDocument();
  });

  it('filters results with stock code, window, phase, and analysis date range when clicking Filter', async () => {
    render(<BacktestPage />);

    const filterInput = await screen.findByPlaceholderText('按股票代码筛选（留空表示全部）');
    const windowInput = screen.getByPlaceholderText('10');
    const phaseSelect = screen.getByDisplayValue('全部阶段');
    const fromInput = screen.getByLabelText('分析开始日期');
    const toInput = screen.getByLabelText('分析结束日期');

    fireEvent.change(filterInput, { target: { value: 'aapl' } });
    fireEvent.change(windowInput, { target: { value: '20' } });
    fireEvent.change(phaseSelect, { target: { value: 'intraday' } });
    fireEvent.change(fromInput, { target: { value: '2026-03-01' } });
    fireEvent.change(toInput, { target: { value: '2026-03-31' } });
    fireEvent.click(screen.getByRole('button', { name: '筛选' }));

    await waitFor(() => {
      expect(mockGetResults).toHaveBeenLastCalledWith({
        code: 'AAPL',
        evalWindowDays: 20,
        analysisDateFrom: '2026-03-01',
        analysisDateTo: '2026-03-31',
        analysisPhase: 'intraday',
        page: 1,
        limit: 20,
      });
      expect(mockGetStockPerformance).toHaveBeenLastCalledWith('AAPL', {
        evalWindowDays: 20,
        analysisDateFrom: '2026-03-01',
        analysisDateTo: '2026-03-31',
        analysisPhase: 'intraday',
      });
    });
  });

  it('runs a backtest and refreshes results using the shared filter values', async () => {
    render(<BacktestPage />);

    const filterInput = await screen.findByPlaceholderText('按股票代码筛选（留空表示全部）');
    const windowInput = screen.getByPlaceholderText('10');

    fireEvent.change(filterInput, { target: { value: 'tsla' } });
    fireEvent.change(windowInput, { target: { value: '15' } });
    fireEvent.click(screen.getByRole('button', { name: '运行回测' }));

    await waitFor(() => {
      expect(mockRun).toHaveBeenCalledWith({
        code: 'TSLA',
        force: undefined,
        minAgeDays: undefined,
        evalWindowDays: 15,
      });
    });

    await waitFor(() => {
      expect(mockGetResults).toHaveBeenLastCalledWith({
        code: 'TSLA',
        evalWindowDays: 15,
        analysisDateFrom: undefined,
        analysisDateTo: undefined,
        analysisPhase: undefined,
        page: 1,
        limit: 20,
      });
      expect(mockGetStockPerformance).toHaveBeenLastCalledWith('TSLA', {
        evalWindowDays: 15,
        analysisDateFrom: undefined,
        analysisDateTo: undefined,
        analysisPhase: undefined,
      });
    });

    expect(await screen.findByText('已处理:')).toBeInTheDocument();
    expect(screen.getByText('已保存:')).toBeInTheDocument();
  });

  it('switches to next-day validation with the 1D shortcut', async () => {
    render(<BacktestPage />);

    await screen.findByPlaceholderText('按股票代码筛选（留空表示全部）');
    fireEvent.click(screen.getByRole('button', { name: '1 日验证' }));

    await waitFor(() => {
      expect(mockGetResults).toHaveBeenLastCalledWith({
        code: undefined,
        evalWindowDays: 1,
        analysisDateFrom: undefined,
        analysisDateTo: undefined,
        analysisPhase: undefined,
        page: 1,
        limit: 20,
      });
      expect(mockGetOverallPerformance).toHaveBeenLastCalledWith({
        evalWindowDays: 1,
        analysisDateFrom: undefined,
        analysisDateTo: undefined,
        analysisPhase: undefined,
      });
    });

    expect(screen.getByText('实际表现')).toBeInTheDocument();
    expect(screen.getByText('准确性')).toBeInTheDocument();
    expect(screen.getByText('1 日验证模式会用下一个交易日收盘表现校验 AI 预测。')).toBeInTheDocument();
  });

  it('renders short position with funding-adjusted simulated return and exit reason', async () => {
    mockSingleRow({ ...perpRowBase, positionRecommendation: 'short', simulatedReturnPct: 5.4, simulatedExitReason: 'window_end_short' });
    render(<BacktestPage />);

    expect(await screen.findByText('做空')).toBeInTheDocument();
    expect(screen.getByText('仓位/模拟')).toBeInTheDocument();      // 新表头
    const ret = screen.getByText('5.4%');                           // 模拟收益（正，绿色）
    expect(ret).toHaveClass('text-success');
    expect(screen.getByText('窗口期满(空)')).toBeInTheDocument();
    expect(screen.getByText('-5.2%')).toBeInTheDocument();          // 价格列红负与结果绿共存的核心反差场景
  });

  it('renders long position with take-profit exit', async () => {
    mockSingleRow({ ...perpRowBase, directionExpected: 'up', actualMovement: 'up', actualReturnPct: 8.0, positionRecommendation: 'long', simulatedReturnPct: 10.5, simulatedExitReason: 'take_profit' });
    render(<BacktestPage />);

    expect(await screen.findByText('做多')).toBeInTheDocument();    // 此 fixture 中唯一（方向列为 '看涨'）
    expect(screen.getByText('10.5%')).toBeInTheDocument();
    expect(screen.getByText('止盈')).toBeInTheDocument();
  });

  it('renders cash position with zero return and no-trade reason', async () => {
    mockSingleRow({ ...perpRowBase, directionExpected: 'flat', actualMovement: 'flat', actualReturnPct: 0.3, positionRecommendation: 'cash', simulatedReturnPct: 0.0, simulatedExitReason: 'cash' });
    render(<BacktestPage />);

    expect(await screen.findByText('空仓')).toBeInTheDocument();
    const ret = screen.getByText('0.0%');
    expect(ret).toHaveClass('text-secondary-text');                 // 0 中性色：真实零收益，非缺数据
    expect(screen.getByText('无交易')).toBeInTheDocument();         // 引擎 cash 行出场原因为 'cash'
  });

  it('renders -- without badge when positionRecommendation is absent', async () => {
    mockSingleRow({ ...perpRowBase });                              // 无 positionRecommendation
    render(<BacktestPage />);

    await screen.findByText('BTC/USDT:PERP');
    expect(screen.queryByText('做空')).not.toBeInTheDocument();
    expect(screen.queryByText('做多')).not.toBeInTheDocument();
    expect(screen.queryByText('空仓')).not.toBeInTheDocument();
    expect(screen.queryByText('无交易')).not.toBeInTheDocument();
  });

  it('renders badge but -- return when simulatedReturnPct is missing', async () => {
    mockSingleRow({ ...perpRowBase, directionExpected: 'up', positionRecommendation: 'long', simulatedExitReason: 'window_end' });
    render(<BacktestPage />);

    expect(await screen.findByText('做多')).toBeInTheDocument();
    expect(screen.getByText('窗口期满')).toBeInTheDocument();       // badge 照渲、收益位为 pct(null)='--'
    expect(screen.getByText('窗口期满').closest('td')).toHaveTextContent('--');  // 收益位缺值渲染 --（锁同一单元格）
  });

  it('echoes unknown exit reason verbatim', async () => {
    mockSingleRow({ ...perpRowBase, positionRecommendation: 'short', simulatedReturnPct: 1.0, simulatedExitReason: 'some_future_reason' });
    render(<BacktestPage />);

    expect(await screen.findByText('做空')).toBeInTheDocument();
    expect(screen.getByText('some_future_reason')).toBeInTheDocument();  // labelFromMap 未知值原样回显
  });
});
