# -*- coding: utf-8 -*-
import pandas as pd
import pytest
from unittest.mock import patch
from data_provider.fundamental_adapter import AkshareFundamentalAdapter, _ggt_key
import data_provider.fundamental_adapter as fa


@pytest.fixture(autouse=True)
def _clear_ggt_cache():
    fa._GGT_LIST_CACHE.clear()
    yield
    fa._GGT_LIST_CACHE.clear()


def test_ggt_key_normalizes_three_writings_to_same_hk_key():
    # 裸 5 位 / HK 前缀 / .HK 后缀 三写法同键(Blocker F1)
    assert _ggt_key("00700") == "HK00700"
    assert _ggt_key("hk00700") == "HK00700"
    assert _ggt_key("0700.HK") == "HK00700"
    assert _ggt_key("01810") == "HK01810"


def _components_df(codes):
    return pd.DataFrame({"代码": codes, "名称": [f"n{c}" for c in codes]})


def test_eligibility_set_built_with_ggt_key_from_bare_codes():
    # 成份表用裸 "00700" 建 set,查询侧三写法均命中(禁用已归一 fixture)
    df = _components_df([f"{i:05d}" for i in range(60)] + ["00700"])  # >=50 行
    with patch.object(AkshareFundamentalAdapter, "_fetch_ggt_components_df", return_value=df):
        s = AkshareFundamentalAdapter().get_ggt_eligibility_set()
    assert s is not None
    assert _ggt_key("hk00700") in s and _ggt_key("0700.HK") in s
    assert _ggt_key("99999") not in s


def test_eligibility_set_truncated_table_returns_none():
    # R4:行数 < 50 视为表不可得 → None(而非小而错的 set)
    df = _components_df(["00700", "01810"])  # 仅 2 行
    with patch.object(AkshareFundamentalAdapter, "_fetch_ggt_components_df", return_value=df):
        assert AkshareFundamentalAdapter().get_ggt_eligibility_set() is None


def test_eligibility_set_valid_key_count_below_floor_returns_none():
    # R4 第二道守卫:原始行数 >=50 过第一道,但归一去重后有效 HK 键 <50 → None
    # (端点结构漂移/大量重复码时,不返回小而错的 set)
    df = _components_df(["%05d" % (i % 5) for i in range(60)])  # 60 行但仅 5 个不同码
    with patch.object(AkshareFundamentalAdapter, "_fetch_ggt_components_df", return_value=df):
        assert AkshareFundamentalAdapter().get_ggt_eligibility_set() is None


def test_eligibility_set_endpoint_failure_returns_none_and_negative_cached():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise RuntimeError("eastmoney unreachable")

    with patch.object(AkshareFundamentalAdapter, "_fetch_ggt_components_df", side_effect=boom):
        a = AkshareFundamentalAdapter()
        assert a.get_ggt_eligibility_set() is None
        assert a.get_ggt_eligibility_set() is None   # 负缓存 300s 内不重打
    assert calls["n"] == 1
