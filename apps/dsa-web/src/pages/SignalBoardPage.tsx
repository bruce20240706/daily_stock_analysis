import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { stocksApi } from '../api/stocks';
import type { SignalsBoardResponse } from '../types/kline';
import { SignalBoard } from '../components/board/SignalBoard';
import { KLineDrawer } from '../components/kline';
import { AppPage, Button, InlineAlert, Loading } from '../components/common';

const SignalBoardPage: React.FC = () => {
  const [data, setData] = useState<SignalsBoardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [klineTarget, setKlineTarget] = useState<{ stockCode: string; stockName?: string } | null>(null);

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    setError(null);
    try {
      setData(await stocksApi.getBoard(undefined, refresh));
    } catch {
      setError('信号看板加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  const onRowClick = useCallback(
    (stockCode: string, stockName?: string) => setKlineTarget({ stockCode, stockName }),
    [],
  );

  return (
    <AppPage className="space-y-4 pb-12 pt-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-normal text-foreground">信号看板</h1>
          <p className="mt-1 text-sm text-secondary-text">自选股近实时量价信号，点击任一行查看 K 线详情。</p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => void load(true)} disabled={loading}>
          刷新
        </Button>
      </div>

      {error ? <InlineAlert variant="danger" message={error} /> : null}

      {loading && !data ? <Loading label="正在加载信号看板" /> : null}

      {data && data.entries.length === 0 ? (
        <div data-testid="board-empty" className="text-sm text-secondary-text">
          自选为空，去「首页」添加自选股后再来看信号看板。
        </div>
      ) : null}

      {data && data.entries.length > 0 ? <SignalBoard entries={data.entries} onRowClick={onRowClick} /> : null}

      {klineTarget ? (
        <KLineDrawer
          stockCode={klineTarget.stockCode}
          stockName={klineTarget.stockName}
          isOpen
          onClose={() => setKlineTarget(null)}
        />
      ) : null}
    </AppPage>
  );
};

export default SignalBoardPage;
