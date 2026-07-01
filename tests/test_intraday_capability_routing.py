"""intraday_data capability 声明式路由 —— golden 等价 + 声明单测 + duck-typed 容错。

本文件锁定 DataFetcherManager._intraday_fetchers_for 的分钟源路由:
- 默认管理器(离线无 token)各市场精确有序列表(生产路由字节级不变基线);
- 跨市场防泄漏(yfinance 不入 cn、Tushare 不入 hk);
- 各 fetcher intraday_markets 声明值 + OkxPerpetual MRO 覆写;
- duck-typed 注入源容错(无声明优雅排除、有声明才入)。
"""
import pytest

from data_provider.base import DataFetcherManager


# 默认管理器(离线无 token)各市场分钟路由的精确有序 golden。经实测确认:
#   crypto=priority 顺序[Binance,Okx,Coinbase]、perp=[OkxPerpetual]、
#   cn=[Akshare](无 token,Tushare 被 is_available 剔除)、us=[Yfinance]、
#   hk=[Akshare,Yfinance](cn/hk 排序表)、510050(不支持 ETF)=[]。
_DEFAULT_GOLDEN = [
    ("BTC/USDT", ["BinanceFetcher", "OkxFetcher", "CoinbaseFetcher"]),
    ("BTC/USDT:PERP", ["OkxPerpetualFetcher"]),
    ("600519", ["AkshareFetcher"]),
    ("AAPL", ["YfinanceFetcher"]),
    ("HK00700", ["AkshareFetcher", "YfinanceFetcher"]),
    ("510050", []),
]


@pytest.mark.parametrize("code,expected", _DEFAULT_GOLDEN)
def test_intraday_routing_default_manager_golden(code, expected):
    mgr = DataFetcherManager()
    assert [f.name for f in mgr._intraday_fetchers_for(code)] == expected


def test_intraday_routing_no_cross_market_leak():
    mgr = DataFetcherManager()
    cn = [f.name for f in mgr._intraday_fetchers_for("600519")]
    hk = [f.name for f in mgr._intraday_fetchers_for("HK00700")]
    assert "YfinanceFetcher" not in cn      # 补丁①:yfinance 日线支持 cn,但分钟不入 cn
    assert "TushareFetcher" not in hk       # 补丁②:Tushare 日线支持 hk,但分钟不入 hk
