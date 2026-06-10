import type React from 'react';
import type { CryptoContracts, ReportLanguage } from '../../types/analysis';
import { Card } from '../common';
import { DashboardPanelHeader } from '../dashboard';
import { normalizeReportLanguage } from '../../utils/reportLanguage';

interface ReportCryptoMetricsProps {
  contracts?: CryptoContracts;
  language?: ReportLanguage;
}

const TEXT = {
  zh: {
    eyebrow: '永续合约',
    title: '合约市场指标',
    fundingRate: '资金费率',
    fundingHint: '正=多头付费 / 负=空头付费（约 8h 结算）',
    markPrice: '标记价',
    openInterest: '未平仓量(OI)',
    source: '来源',
  },
  en: {
    eyebrow: 'Perpetual',
    title: 'Contract Market Metrics',
    fundingRate: 'Funding Rate',
    fundingHint: 'positive = longs pay / negative = shorts pay (~8h settlement)',
    markPrice: 'Mark Price',
    openInterest: 'Open Interest',
    source: 'Source',
  },
} as const;

/** crypto 永续合约指标卡片 - presence-only；无任何字段则不渲染。 */
export const ReportCryptoMetrics: React.FC<ReportCryptoMetricsProps> = ({ contracts, language = 'zh' }) => {
  const t = TEXT[normalizeReportLanguage(language)];
  if (!contracts) return null;

  const rows: Array<{ label: string; value: string; hint?: string }> = [];
  if (typeof contracts.fundingRate === 'number') {
    rows.push({ label: t.fundingRate, value: `${(contracts.fundingRate * 100).toFixed(4)}%`, hint: t.fundingHint });
  }
  if (typeof contracts.markPrice === 'number') {
    rows.push({ label: t.markPrice, value: String(contracts.markPrice) });
  }
  if (typeof contracts.openInterest === 'number' || typeof contracts.openInterestUsd === 'number') {
    const parts: string[] = [];
    if (typeof contracts.openInterest === 'number') {
      parts.push(`${contracts.openInterest.toLocaleString('en-US')} 张`);
    }
    if (typeof contracts.openInterestUsd === 'number') {
      parts.push(`$${contracts.openInterestUsd.toLocaleString('en-US', { maximumFractionDigits: 0 })}`);
    }
    rows.push({ label: t.openInterest, value: parts.join(' / ') });
  }
  if (contracts.source) {
    rows.push({ label: t.source, value: contracts.source.toUpperCase() });
  }
  if (rows.length === 0) return null;

  return (
    <Card variant="bordered" padding="md" className="home-panel-card text-left">
      <DashboardPanelHeader eyebrow={t.eyebrow} title={t.title} className="mb-3" />
      <div className="space-y-2">
        {rows.map((row) => (
          <div key={row.label} className="flex items-baseline justify-between gap-3 text-sm">
            <span className="text-muted-text">{row.label}</span>
            <span className="text-right font-mono text-foreground">
              {row.value}
              {row.hint ? <span className="ml-2 block text-xs text-muted-text sm:inline">{row.hint}</span> : null}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
};
