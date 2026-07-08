# -*- coding: utf-8 -*-
import math
from datetime import date

import pandas as pd
import pytest
from unittest.mock import patch
from data_provider.fundamental_adapter import AkshareFundamentalAdapter, _ggt_key
import data_provider.fundamental_adapter as fa


@pytest.fixture(autouse=True)
def _clear_ggt_cache():
    fa._GGT_LIST_CACHE.clear()
    fa._SB_HOLDING_CACHE.clear()
    fa._SB_FLOW_CACHE.clear()
    yield
    fa._GGT_LIST_CACHE.clear()
    fa._SB_HOLDING_CACHE.clear()
    fa._SB_FLOW_CACHE.clear()


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


def _holding_df(rows):
    # rows: list of (股票代码, 持股日期, 持股数量, 持股市值, 占比)
    return pd.DataFrame({
        "股票代码": [r[0] for r in rows],
        "股票简称": [f"n{r[0]}" for r in rows],
        "持股日期": [r[1] for r in rows],
        "持股数量": [r[2] for r in rows],
        "持股市值": [r[3] for r in rows],
        "持股数量占发行股百分比": [r[4] for r in rows],
        "持股市值变化-5日": [0.0 for r in rows],   # 存在但不取
    })


def test_holding_picks_latest_row_by_date_and_real_columns():
    # 同股多日期 → 取持股日期最大行;占比读真实列名(非"占A股")
    df = _holding_df([
        ("00700", "2026-06-30", 100, 1000.0, 5.0),
        ("00700", "2026-07-01", 200, 2200.0, 6.5),   # 最新
        ("01810", "2026-07-01", 50, 500.0, 2.0),
    ])
    with patch.object(AkshareFundamentalAdapter, "_fetch_ggt_holding_df", return_value=df):
        h = AkshareFundamentalAdapter().get_ggt_holding("hk00700")   # 三写法归一
    assert h["holding_shares"] == 200 and h["holding_value"] == 2200.0
    assert h["holding_ratio_pct"] == 6.5
    assert h["holding_trade_date"] == "2026-07-01"


def test_holding_stock_not_in_table_returns_none():
    df = _holding_df([("01810", "2026-07-01", 50, 500.0, 2.0)])
    with patch.object(AkshareFundamentalAdapter, "_fetch_ggt_holding_df", return_value=df):
        assert AkshareFundamentalAdapter().get_ggt_holding("00700") is None


def test_holding_endpoint_failure_returns_none():
    with patch.object(AkshareFundamentalAdapter, "_fetch_ggt_holding_df",
                      side_effect=RuntimeError("boom")):
        assert AkshareFundamentalAdapter().get_ggt_holding("00700") is None


def test_holding_picks_latest_row_with_real_date_objects():
    # 真实 akshare 端点 持股日期 列为 datetime.date 对象(pd.to_datetime(...).dt.date),
    # 非字符串;idxmax() 须对 date 对象与字符串两种 dtype 均正确择最新行(dtype 保真回归)。
    df = _holding_df([
        ("00700", date(2026, 6, 30), 100, 1000.0, 5.0),
        ("00700", date(2026, 7, 1), 200, 2200.0, 6.5),   # 最新
        ("01810", date(2026, 7, 1), 50, 500.0, 2.0),
    ])
    with patch.object(AkshareFundamentalAdapter, "_fetch_ggt_holding_df", return_value=df):
        h = AkshareFundamentalAdapter().get_ggt_holding("hk00700")
    assert h["holding_shares"] == 200 and h["holding_value"] == 2200.0
    assert h["holding_ratio_pct"] == 6.5
    assert h["holding_trade_date"] == "2026-07-01"


def test_holding_nan_numeric_values_pass_through_unchanged():
    # 真实端点 持股数量/持股市值 经 pd.to_numeric(errors="coerce") 对不可解析值产生 NaN。
    # 确认既有 _safe_float 行为(非本任务新增语义):NaN 是 float 实例,isinstance 分支
    # 直接 float(nan) 不抛异常、不映射为 None——原样以 NaN 传出。
    df = _holding_df([
        ("00700", "2026-07-01", float("nan"), float("nan"), 6.5),
    ])
    with patch.object(AkshareFundamentalAdapter, "_fetch_ggt_holding_df", return_value=df):
        h = AkshareFundamentalAdapter().get_ggt_holding("00700")
    assert math.isnan(h["holding_shares"])
    assert math.isnan(h["holding_value"])
    assert h["holding_ratio_pct"] == 6.5


def _flow_df(sh, sz, date_val="2026-07-01"):
    # 真实东财 fund_flow_summary 结构(真网核验 2026-07-06):4 行——
    # 类型=沪港通/深港通(连接程序名,不含"港股通"子串)、板块=沪股通/港股通(沪)/深股通/港股通(深)、
    # 资金方向=北向/南向。北向两腿(沪股通/深股通)成交净买额置 999.0,验证被 资金方向 正确排除。
    return pd.DataFrame({
        "交易日": [date_val] * 4,
        "类型": ["沪港通", "沪港通", "深港通", "深港通"],
        "板块": ["沪股通", "港股通(沪)", "深股通", "港股通(深)"],
        "资金方向": ["北向", "南向", "北向", "南向"],
        "成交净买额": [999.0, sh, 999.0, sz],
        "资金净流入": [1000.0, 420.0, 1000.0, 420.0],
    })


def test_southbound_flow_sums_two_legs():
    with patch.object(AkshareFundamentalAdapter, "_fetch_sb_flow_df", return_value=_flow_df(12.0, 8.0)):
        f = AkshareFundamentalAdapter().get_southbound_flow()
    assert abs(f["southbound_net_flow"] - 20.0) < 1e-9
    assert f["partial"] is False
    assert f["flow_date"] == "2026-07-01"


def test_southbound_flow_matches_by_direction_not_type():
    # 回归钉(真网 bug):类型 列=沪港通/深港通 全不含"港股通"子串,南向两腿必须靠
    # 资金方向=南向 命中,而非旧代码对 类型 列子串匹配(那样 0 命中 → 永久 None)。
    df = _flow_df(12.0, 8.0)
    assert not df["类型"].astype(str).str.contains("港股通").any()   # 证类型列无"港股通"
    with patch.object(AkshareFundamentalAdapter, "_fetch_sb_flow_df", return_value=df):
        f = AkshareFundamentalAdapter().get_southbound_flow()
    assert f is not None and abs(f["southbound_net_flow"] - 20.0) < 1e-9


def test_southbound_flow_both_nan_returns_none_not_zero():
    # F4 假零陷阱:两腿 NaN → None,禁 sum 得 0.0
    with patch.object(AkshareFundamentalAdapter, "_fetch_sb_flow_df",
                      return_value=_flow_df(float("nan"), float("nan"))):
        assert AkshareFundamentalAdapter().get_southbound_flow() is None


def test_southbound_flow_one_leg_nan_is_partial():
    with patch.object(AkshareFundamentalAdapter, "_fetch_sb_flow_df",
                      return_value=_flow_df(12.0, float("nan"))):
        f = AkshareFundamentalAdapter().get_southbound_flow()
    assert abs(f["southbound_net_flow"] - 12.0) < 1e-9
    assert f["partial"] is True


def test_southbound_flow_excludes_northbound_legs():
    # 北向 沪股通/深股通 腿值 999.0 巨大;若未被 资金方向 过滤,和会被严重污染
    with patch.object(AkshareFundamentalAdapter, "_fetch_sb_flow_df", return_value=_flow_df(12.0, 8.0)):
        f = AkshareFundamentalAdapter().get_southbound_flow()
    assert abs(f["southbound_net_flow"] - 20.0) < 1e-9


def test_southbound_flow_fallback_to_bankuai_when_no_direction_col():
    # 兜底路径:无 资金方向 列时退回 板块 含"港股通"(全角括号亦免疫),仍正确求和
    df = pd.DataFrame({
        "交易日": ["2026-07-01"] * 4,
        "类型": ["沪港通", "沪港通", "深港通", "深港通"],
        "板块": ["沪股通", "港股通（沪）", "深股通", "港股通（深）"],   # 全角括号
        "成交净买额": [999.0, 12.0, 999.0, 8.0],
    })
    with patch.object(AkshareFundamentalAdapter, "_fetch_sb_flow_df", return_value=df):
        f = AkshareFundamentalAdapter().get_southbound_flow()
    assert f is not None and abs(f["southbound_net_flow"] - 20.0) < 1e-9


def test_southbound_flow_endpoint_failure_returns_none():
    with patch.object(AkshareFundamentalAdapter, "_fetch_sb_flow_df",
                      side_effect=RuntimeError("boom")):
        assert AkshareFundamentalAdapter().get_southbound_flow() is None
