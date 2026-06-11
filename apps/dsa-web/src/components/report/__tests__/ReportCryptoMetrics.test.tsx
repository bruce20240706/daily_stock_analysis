import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { CryptoContracts } from '../../../types/analysis';
import { ReportCryptoMetrics } from '../ReportCryptoMetrics';

const FULL: CryptoContracts = {
  fundingRate: 0.0000059888,
  markPrice: 62669.5,
  openInterest: 2861888.58,
  openInterestUsd: 1793545573.08,
  source: 'okx',
};

describe('ReportCryptoMetrics', () => {
  it('renders funding rate / mark price / OI when present', () => {
    render(<ReportCryptoMetrics contracts={FULL} />);
    expect(screen.getByText(/0\.0006%/)).toBeInTheDocument();      // 0.0000059888*100 toFixed(4)
    expect(screen.getByText(/62669\.5/)).toBeInTheDocument();
    expect(screen.getByText(/OKX/i)).toBeInTheDocument();
  });

  it('omits a missing field (presence-only)', () => {
    render(<ReportCryptoMetrics contracts={{ markPrice: 100 }} />);
    expect(screen.getByText(/100/)).toBeInTheDocument();
    expect(screen.queryByText(/资金费率/)).not.toBeInTheDocument();
  });

  it('renders nothing when contracts is empty/undefined', () => {
    const { container } = render(<ReportCryptoMetrics contracts={undefined} />);
    expect(container).toBeEmptyDOMElement();
    const { container: c2 } = render(<ReportCryptoMetrics contracts={{}} />);
    expect(c2).toBeEmptyDOMElement();
  });

  it('renders fundingRate of exactly 0 (not dropped as falsy)', () => {
    render(<ReportCryptoMetrics contracts={{ fundingRate: 0 }} />);
    expect(screen.getByText(/0\.0000%/)).toBeInTheDocument();
  });

  it('renders open interest USD alone when contract count is absent', () => {
    render(<ReportCryptoMetrics contracts={{ openInterestUsd: 1793545573.08 }} />);
    expect(screen.getByText(/\$1,793,545,573/)).toBeInTheDocument();
    expect(screen.queryByText(/张/)).not.toBeInTheDocument();
  });

  it('renders long/short ratio rows when present', () => {
    render(<ReportCryptoMetrics contracts={{ fundingRate: 0.0001, longShortRatio: 1.23, longShortRatioTop: 0.85 }} language="zh" />);
    expect(screen.getByText('多空比(全市场)')).toBeInTheDocument();
    expect(screen.getByText('1.23')).toBeInTheDocument();
    expect(screen.getByText('多空比(大户)')).toBeInTheDocument();
    expect(screen.getByText('0.85')).toBeInTheDocument();
  });

  it('omits long/short ratio rows when absent', () => {
    render(<ReportCryptoMetrics contracts={{ fundingRate: 0.0001 }} language="zh" />);
    expect(screen.queryByText('多空比(全市场)')).not.toBeInTheDocument();
  });
});
