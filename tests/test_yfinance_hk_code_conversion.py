# -*- coding: utf-8 -*-
"""离线单测: YfinanceFetcher._convert_stock_code 港股裸 5 位数字正确映射 .HK。

TDD: 先红(Item-1 fix 未加入时 bare-5-digit → .SZ)，修复后全绿。
无网络依赖，无外部 I/O。
"""
import pytest

from data_provider.yfinance_fetcher import YfinanceFetcher


@pytest.fixture(scope="module")
def fetcher():
    return YfinanceFetcher()


# ── Item-1: 裸 5 位港股码 ──────────────────────────────────────────────────
def test_bare_5digit_00700(fetcher):
    """裸 5 位港股 00700 → 0700.HK（Item-1 核心修复）"""
    assert fetcher._convert_stock_code("00700") == "0700.HK"


def test_bare_5digit_09988(fetcher):
    """裸 5 位港股 09988 → 9988.HK"""
    assert fetcher._convert_stock_code("09988") == "9988.HK"


# ── 回归: 既有港股前缀/后缀形式不变 ─────────────────────────────────────────
def test_hk_prefix_upper(fetcher):
    """HK 前缀（大写）仍走既有分支 → 0700.HK"""
    assert fetcher._convert_stock_code("HK00700") == "0700.HK"


def test_hk_prefix_lower(fetcher):
    """hk 前缀（小写）经 .upper() 走既有分支 → 0700.HK"""
    assert fetcher._convert_stock_code("hk00700") == "0700.HK"


def test_hk_suffix_passthrough(fetcher):
    """.HK 后缀直接透传，不做二次转换"""
    assert fetcher._convert_stock_code("0700.HK") == "0700.HK"


# ── 回归: 6 位 A 股不受影响 ───────────────────────────────────────────────
def test_ashare_sh_6digit(fetcher):
    """6 位沪市 A 股 600519 → 600519.SS（回归护卫）"""
    assert fetcher._convert_stock_code("600519") == "600519.SS"


def test_ashare_sz_6digit(fetcher):
    """6 位深市 A 股 000001 → 000001.SZ（回归护卫）"""
    assert fetcher._convert_stock_code("000001") == "000001.SZ"
