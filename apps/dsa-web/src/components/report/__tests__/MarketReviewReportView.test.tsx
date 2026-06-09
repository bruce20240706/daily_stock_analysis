import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { AnalysisReport, MarketReviewPayload } from '../../../types/analysis';
import { MarketReviewReportView } from '../MarketReviewReportView';

vi.mock('../../../api/history', () => ({
  historyApi: {
    getMarkdown: vi.fn(),
  },
}));

const englishMarketReviewReport: AnalysisReport = {
  meta: {
    queryId: 'market-review-q-1',
    stockCode: 'MARKET',
    stockName: 'Market Review',
    reportType: 'market_review',
    reportLanguage: 'en',
    createdAt: '2026-03-18T08:00:00Z',
  },
  summary: {
    analysisSummary: '',
    operationAdvice: '',
    trendPrediction: '',
    sentimentScore: undefined as unknown as number,
  },
};

const combinedMarketReviewPayload: MarketReviewPayload = {
  version: 1,
  kind: 'market_review',
  region: 'cn,hk',
  language: 'zh',
  rootTitle: '大盘复盘',
  markets: {
    cn: {
      title: 'A股市场',
      breadth: {
        upCount: 3120,
        downCount: 1420,
        limitUpCount: 72,
        limitDownCount: 4,
        totalAmount: 9600,
        turnoverUnit: '亿元',
      },
      indices: [{
        code: '000300',
        name: '沪深300',
        current: 3920.2,
        changePct: 1.2,
        high: 3940.5,
        low: 3860.1,
      }],
    },
    hk: {
      title: '港股市场',
      breadth: {
        upCount: 680,
        downCount: 410,
        limitUpCount: 0,
        limitDownCount: 0,
        totalAmount: 1180,
        turnoverUnit: '亿港元',
      },
      indices: [{
        code: 'HSI',
        name: '恒生指数',
        current: 18920.4,
        changePct: -0.5,
        high: 19050.2,
        low: 18780.3,
      }],
    },
  },
};

const noBreadthMarketReviewPayload: MarketReviewPayload = {
  version: 1,
  kind: 'market_review',
  region: 'us',
  language: 'en',
  title: 'Market Review',
  rootTitle: 'Market Review',
  indices: [{
    code: 'SPX',
    name: 'S&P 500',
    current: 5200,
    changePct: 0.68,
    high: 5235.2,
    low: 5170.4,
  }],
  sectors: {
    top: [{ name: 'Technology', changePct: 1.9 }],
    bottom: [{ name: 'Energy', changePct: -0.8 }],
  },
  news: [],
  sections: [],
};

describe('MarketReviewReportView', () => {
  it('uses localized summary card labels and fallbacks for English reports', () => {
    render(
      <MarketReviewReportView
        report={englishMarketReviewReport}
        content="# Market Review"
        reportLanguage="en"
      />,
    );

    expect(screen.getByText('Review Summary')).toBeInTheDocument();
    expect(screen.getByText('No review summary yet')).toBeInTheDocument();
    expect(screen.getByText('Market Sentiment')).toBeInTheDocument();
    expect(screen.getByText('No score yet')).toBeInTheDocument();
    expect(screen.getByText('Rotation & Funds')).toBeInTheDocument();
    expect(screen.getByText('No rotation view yet')).toBeInTheDocument();
    expect(screen.getByText('Risks & Watchlist')).toBeInTheDocument();
    expect(screen.getByText('No key observations yet')).toBeInTheDocument();
    expect(screen.queryByText('复盘摘要')).not.toBeInTheDocument();
    expect(screen.queryByText('暂无摘要')).not.toBeInTheDocument();
  });

  it('renders structured data for every market in a combined market review payload', () => {
    render(
      <MarketReviewReportView
        payload={combinedMarketReviewPayload}
        content="# 大盘复盘"
        reportLanguage="zh"
      />,
    );

    expect(screen.getByText('A股市场')).toBeInTheDocument();
    expect(screen.getByText('港股市场')).toBeInTheDocument();
    expect(screen.getByText('沪深300')).toBeInTheDocument();
    expect(screen.getByText('恒生指数')).toBeInTheDocument();
    expect(screen.getByText('3120')).toBeInTheDocument();
    expect(screen.getByText('680')).toBeInTheDocument();
  });

  it('localizes structured market data labels for Chinese reports', () => {
    render(
      <MarketReviewReportView
        payload={combinedMarketReviewPayload}
        content="# 大盘复盘"
        reportLanguage="zh"
      />,
    );

    expect(screen.getByText('结构化大盘数据')).toBeInTheDocument();
    expect(screen.getAllByText('上涨家数')).toHaveLength(2);
    expect(screen.getAllByText('下跌家数')).toHaveLength(2);
    expect(screen.getAllByText('涨停/跌停')).toHaveLength(2);
    expect(screen.getAllByText('成交额')).toHaveLength(2);
    expect(screen.getAllByText('指数')).toHaveLength(2);
    expect(screen.getAllByText('最新')).toHaveLength(2);
    expect(screen.getAllByText('涨跌幅')).toHaveLength(2);
    expect(screen.getAllByText('高/低')).toHaveLength(2);
    expect(screen.queryByText('Structured Market Data')).not.toBeInTheDocument();
    expect(screen.queryByText('Advancers')).not.toBeInTheDocument();
    expect(screen.queryByText('Index')).not.toBeInTheDocument();
  });

  it('shows "No data" when breadth is not available for a market review payload', () => {
    render(
      <MarketReviewReportView
        payload={noBreadthMarketReviewPayload}
        content="# Market Review"
        reportLanguage="en"
      />,
    );

    expect(screen.getByText('Structured Market Data')).toBeInTheDocument();
    expect(screen.getByText('No data')).toBeInTheDocument();
    expect(screen.getByText('S&P 500')).toBeInTheDocument();
    expect(screen.queryByText('Advancers')).not.toBeInTheDocument();
    expect(screen.queryByText('Decliners')).not.toBeInTheDocument();
  });

  it('crypto 渲染上新行情表，listedAt 缺失显示 —', () => {
    const payload = {
      version: 1, kind: 'market_review', region: 'crypto', language: 'zh',
      title: '加密货币大盘复盘', date: '2026-06-08',
      indices: [{ code: 'BTC/USDT', name: 'BTC/USDT', current: 64000, changePct: 1.0 }],
      newListings: [
        { base: 'NEW', exchanges: ['okx'], pairs: ['NEW-USDT'], listedAt: 1733616000000, price: 1.5, changePct: 5.0 },
        { base: 'NOQ', exchanges: ['binance'], pairs: ['NOQ-USDT'] },
      ],
      sections: [{ key: 'full_review', title: 'Review', markdown: '## 一、概览\n内容' }],
      markdownReport: '# 加密货币大盘复盘',
    };
    render(
      <MarketReviewReportView
        payload={payload as any}
        content="# 加密货币大盘复盘"
        reportLanguage="zh"
      />,
    );
    expect(screen.getByText('NEW')).toBeInTheDocument();
    expect(screen.getByText('NOQ')).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);  // NOQ 无 listedAt → —
  });

  it('非 crypto 或无 newListings 不渲染上新表', () => {
    const payload = {
      version: 1, kind: 'market_review', region: 'us', language: 'zh',
      title: 'US', date: '2026-06-08', indices: [],
      sections: [], markdownReport: '#',
    };
    render(
      <MarketReviewReportView
        payload={payload as any}
        content="#"
        reportLanguage="zh"
      />,
    );
    expect(screen.queryByText(/上新|New Listings/i)).toBeNull();
  });

  it('us 携带 newListings 也不渲染上新表（region 守卫）', () => {
    const payload = {
      version: 1, kind: 'market_review', region: 'us', language: 'zh',
      title: 'US', date: '2026-06-08',
      indices: [{ code: 'SPX', name: 'S&P 500', current: 5000, changePct: 0.5 }],
      newListings: [{ base: 'NEW', exchanges: ['okx'], pairs: ['NEW-USDT'], listedAt: 1733616000000 }],
      sections: [], markdownReport: '#',
    };
    render(<MarketReviewReportView payload={payload as any} reportLanguage="zh" />);
    expect(screen.queryByText(/上新|New Listings/i)).toBeNull();
    expect(screen.queryByText('NEW')).toBeNull();
  });

  it('crypto 市场不渲染 breadth 暂无数据占位', () => {
    const cryptoPayload: MarketReviewPayload = {
      version: 1,
      kind: 'market_review',
      region: 'crypto',
      language: 'zh',
      title: '数字货币市场',
      rootTitle: '大盘复盘',
      indices: [
        {
          code: 'BTCUSDT',
          name: 'BTC/USDT',
          current: 67800,
          changePct: 2.1,
          high: 68500,
          low: 66200,
        },
      ],
      sections: [],
    };

    render(
      <MarketReviewReportView
        payload={cryptoPayload}
        content="# 大盘复盘"
        reportLanguage="zh"
      />,
    );

    expect(screen.getByText('结构化大盘数据')).toBeInTheDocument();
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
    expect(screen.queryByText('No data')).not.toBeInTheDocument();
    expect(screen.getByText('BTC/USDT')).toBeInTheDocument();
    expect(screen.queryByText('上涨家数')).not.toBeInTheDocument();
    expect(screen.queryByText('涨停/跌停')).not.toBeInTheDocument();
  });
});

describe('MarketReviewReportView crypto market indicators', () => {
  const base = {
    version: 1, kind: 'market_review', region: 'crypto', title: '加密货币大盘复盘',
    indices: [{ code: 'BTC/USDT', name: 'BTC/USDT', current: 60000, changePct: 1.2, high: 61000, low: 59000 }],
  };

  it('renders indicators for crypto when present', () => {
    const payload = { ...base, marketIndicators: {
      btcDominance: 56.07, totalMarketCapUsd: 2241017397766,
      marketCapChange24hPct: -0.64,
      fearGreed: { value: 10, classification: 'Extreme Fear', timestamp: 1 },
    } };
    render(<MarketReviewReportView payload={payload as never} reportLanguage="zh" />);
    expect(screen.getByText(/56\.07/)).toBeInTheDocument();
    expect(screen.getByText(/Extreme Fear/)).toBeInTheDocument();
  });

  it('does not render indicators when absent', () => {
    render(<MarketReviewReportView payload={base as never} reportLanguage="zh" />);
    expect(screen.queryByText(/Extreme Fear/)).not.toBeInTheDocument();
  });
});
