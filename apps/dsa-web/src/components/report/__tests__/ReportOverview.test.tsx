import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { ReportOverview } from '../ReportOverview';

const baseMeta = {
  queryId: 'q-1',
  stockCode: '600519',
  stockName: '贵州茅台',
  reportType: 'detailed' as const,
  reportLanguage: 'zh' as const,
  createdAt: '2026-03-21T08:00:00Z',
};

const baseSummary = {
  analysisSummary: '趋势维持强势',
  operationAdvice: '继续观察买点',
  trendPrediction: '短线震荡偏强',
  sentimentScore: 78,
};

describe('ReportOverview', () => {
  it('renders final market phase and partial-bar labels from report metadata', () => {
    render(
      <MemoryRouter>
        <ReportOverview
          meta={{
            ...baseMeta,
            marketPhaseSummary: {
              market: 'cn',
              phase: 'intraday',
              marketLocalTime: '2026-03-21T10:30:00+08:00',
              sessionDate: '2026-03-21',
              effectiveDailyBarDate: '2026-03-20',
              isTradingDay: true,
              isMarketOpenNow: true,
              isPartialBar: true,
              minutesToOpen: null,
              minutesToClose: 150,
              triggerSource: 'api',
              analysisIntent: 'auto',
              warnings: [],
            },
          }}
          summary={baseSummary}
        />
      </MemoryRouter>,
    );

    expect(screen.getByLabelText('市场阶段: CN · 盘中')).toBeInTheDocument();
    expect(screen.getByText('市场阶段: CN · 盘中')).toBeVisible();
    expect(screen.getByLabelText('日线未完成')).toBeInTheDocument();
  });

  it('renders English final market phase and partial-bar labels', () => {
    render(
      <MemoryRouter>
        <ReportOverview
          meta={{
            ...baseMeta,
            reportLanguage: 'en',
            marketPhaseSummary: {
              market: 'us',
              phase: 'postmarket',
              marketLocalTime: '2026-03-21T16:30:00-04:00',
              sessionDate: '2026-03-21',
              effectiveDailyBarDate: '2026-03-21',
              isTradingDay: true,
              isMarketOpenNow: false,
              isPartialBar: true,
              minutesToOpen: null,
              minutesToClose: null,
              triggerSource: 'api',
              analysisIntent: 'auto',
              warnings: [],
            },
          }}
          summary={baseSummary}
        />
      </MemoryRouter>,
    );

    expect(screen.getByLabelText('Market phase: US · Post-market')).toBeInTheDocument();
    expect(screen.getByLabelText('Partial bar')).toBeInTheDocument();
  });

  it('renders unknown final phase without partial-bar label', () => {
    render(
      <MemoryRouter>
        <ReportOverview
          meta={{
            ...baseMeta,
            marketPhaseSummary: {
              market: null,
              phase: 'unknown',
              marketLocalTime: null,
              sessionDate: null,
              effectiveDailyBarDate: null,
              isTradingDay: null,
              isMarketOpenNow: null,
              isPartialBar: false,
              minutesToOpen: null,
              minutesToClose: null,
              triggerSource: 'api',
              analysisIntent: 'auto',
              warnings: ['calendar_unavailable'],
            },
          }}
          summary={baseSummary}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText('市场阶段: 阶段未知')).toBeVisible();
    expect(screen.queryByText('日线未完成')).not.toBeInTheDocument();
  });

  it('does not render a market phase placeholder for legacy reports', () => {
    render(<MemoryRouter><ReportOverview meta={baseMeta} summary={baseSummary} /></MemoryRouter>);

    expect(screen.queryByText(/市场阶段/)).not.toBeInTheDocument();
    expect(screen.queryByText('日线未完成')).not.toBeInTheDocument();
  });

  it('renders related boards with leading and lagging markers', () => {
    render(
      <MemoryRouter>
        <ReportOverview
          meta={baseMeta}
          summary={baseSummary}
          details={{
            belongBoards: [
              { name: ' 白酒 ', type: '行业' },
              { name: '消费', type: '概念' },
              { name: '新能源' },
            ],
            sectorRankings: {
              top: [{ name: '白酒', changePct: 2.31 }],
              bottom: [{ name: '消费', changePct: -1.2 }],
            },
          }}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText('关联板块')).toBeInTheDocument();
    expect(screen.getByText('白酒')).toBeInTheDocument();
    expect(screen.getByText('行业')).toBeInTheDocument();
    expect(screen.getByText('领涨')).toBeInTheDocument();
    expect(screen.getByText('+2.31%')).toBeInTheDocument();
    expect(screen.getByText('领跌')).toBeInTheDocument();
    expect(screen.getByText('-1.20%')).toBeInTheDocument();
    expect(screen.queryByText('中性')).not.toBeInTheDocument();
  });

  it('places related boards below action advice and renders more than three on one row', () => {
    const { container } = render(
      <MemoryRouter>
        <ReportOverview
          meta={baseMeta}
          summary={baseSummary}
          details={{
            belongBoards: [
              { name: '白酒', type: '行业' },
              { name: '消费', type: '概念' },
              { name: '高端制造' },
              { name: '沪股通' },
            ],
          }}
        />
      </MemoryRouter>,
    );

    const actionAdviceTitle = screen.getByText('操作建议');
    const relatedBoardsRegion = screen.getByRole('region', { name: '关联板块' });
    const boardList = container.querySelector('.home-related-board-list');

    expect(actionAdviceTitle.compareDocumentPosition(relatedBoardsRegion) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText('沪股通')).toBeInTheDocument();
    expect(boardList).toHaveClass('flex-nowrap', 'overflow-x-auto');
  });

  it('shows board list when rankings are unavailable', () => {
    render(
      <MemoryRouter>
        <ReportOverview
          meta={baseMeta}
          summary={baseSummary}
          details={{
            belongBoards: [{ name: '半导体', type: '行业' }],
          }}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText('关联板块')).toBeInTheDocument();
    expect(screen.getByText('半导体')).toBeInTheDocument();
    expect(screen.queryByText('中性')).not.toBeInTheDocument();
    expect(screen.queryByText('领涨')).not.toBeInTheDocument();
    expect(screen.queryByText('领跌')).not.toBeInTheDocument();
  });

  it('hides related boards section when no boards are available', () => {
    render(<MemoryRouter><ReportOverview meta={baseMeta} summary={baseSummary} details={{ belongBoards: [] }} /></MemoryRouter>);

    expect(screen.queryByText('关联板块')).not.toBeInTheDocument();
  });

  it('fails open on malformed ranking payloads', () => {
    render(
      <MemoryRouter>
        <ReportOverview
          meta={baseMeta}
          summary={baseSummary}
          details={{
            belongBoards: [{ name: ' 白酒 ' }],
            sectorRankings: {
              top: {} as unknown as never[],
              bottom: [{ name: '白酒', changePct: '-2.5%' as unknown as number }],
            },
          }}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText('关联板块')).toBeInTheDocument();
    expect(screen.getByText('白酒')).toBeInTheDocument();
    expect(screen.getByText('领跌')).toBeInTheDocument();
    expect(screen.getByText('-2.50%')).toBeInTheDocument();
  });

  it('shows a workstation link pointing to /stock/<code>', () => {
    render(
      <MemoryRouter>
        <ReportOverview meta={baseMeta} summary={baseSummary} />
      </MemoryRouter>,
    );
    const link = screen.getByRole('link', { name: /在工作台打开/ });
    expect(link).toHaveAttribute('href', '/stock/600519');
  });

  it('encodes crypto code (containing /) in workstation link href', () => {
    render(
      <MemoryRouter>
        <ReportOverview meta={{ ...baseMeta, stockCode: 'BTC/USDT' }} summary={baseSummary} />
      </MemoryRouter>,
    );
    const link = screen.getByRole('link', { name: /在工作台打开/ });
    expect(link).toHaveAttribute('href', '/stock/BTC%2FUSDT');
  });
});
