import type React from 'react';
import { useParams } from 'react-router-dom';
import { AppPage, InlineAlert } from '../components/common';
import { StockWorkstationHeader } from '../components/workstation/StockWorkstationHeader';
import { useWatchlist } from '../hooks/useWatchlist';

const StockWorkstationPage: React.FC = () => {
  const params = useParams<{ code: string }>();
  const code = params.code ?? '';
  const watchlist = useWatchlist();

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
    </AppPage>
  );
};

export default StockWorkstationPage;
