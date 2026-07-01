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


from data_provider.base import BaseFetcher
from data_provider.binance_fetcher import BinanceFetcher
from data_provider.okx_fetcher import OkxFetcher
from data_provider.coinbase_fetcher import CoinbaseFetcher
from data_provider.okx_perpetual_fetcher import OkxPerpetualFetcher
from data_provider.tushare_fetcher import TushareFetcher
from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.yfinance_fetcher import YfinanceFetcher


def test_base_fetcher_intraday_markets_empty_by_default():
    # 纯日线源(Efinance/Pytdx/Baostock/Longbridge/Finnhub/AlphaVantage)继承此空集 → 不入任何分钟路由
    # (纯日线源被排除已由 Task 1 golden 的 cn=[Akshare]/hk=[Akshare,Yfinance] 间接锁定)
    assert BaseFetcher.intraday_markets == frozenset()


@pytest.mark.parametrize("cls,expected", [
    (BinanceFetcher, frozenset({"crypto"})),
    (OkxFetcher, frozenset({"crypto"})),
    (CoinbaseFetcher, frozenset({"crypto"})),
    (OkxPerpetualFetcher, frozenset({"crypto_perp"})),
    (TushareFetcher, frozenset({"cn"})),
    (AkshareFetcher, frozenset({"cn", "hk"})),
    (YfinanceFetcher, frozenset({"us", "hk"})),
])
def test_fetcher_declares_intraday_markets(cls, expected):
    assert cls.intraday_markets == expected


def test_okx_perpetual_overrides_not_inherits_crypto():
    # OkxPerpetualFetcher(OkxFetcher(CryptoExchangeBase)):漏覆写会经 MRO 继承 {crypto}
    assert OkxPerpetualFetcher.intraday_markets == frozenset({"crypto_perp"})
    assert "crypto" not in OkxPerpetualFetcher.intraday_markets
