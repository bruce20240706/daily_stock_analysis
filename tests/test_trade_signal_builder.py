# -*- coding: utf-8 -*-
"""Inc 0 TradeSignal 只读构造器测试。

规则路径喂**真实** derive_price_levels 的输出,LLM 路径喂**真实** parse_sniper_value,
以此证明契约能被今天的数据填满,而不是白板上的字段名。
"""

import ast
from pathlib import Path

import pandas as pd
import pytest

from src.schemas.report_schema import SniperPoints
from src.schemas.trade_signal import Invalidation, TradeSignal
from src.services.trade_signal_builder import attach_evidence, build_from_price_levels, build_from_sniper_points
from src.services.volume_price_signals import PriceLevels, derive_price_levels
from src.sniper_parsing import parse_sniper_value

COMMON = dict(
    code="600519", market="cn", signal_type="breakout", interval="1d",
    horizon_bars=10, as_of="2026-07-10T00:00:00", confidence="high",
)


def _invalidation():
    return Invalidation(note="跌破颈线即失效")


def _ohlcv(bars: int) -> pd.DataFrame:
    """温和上行的合成日线,足够让 MA20 / 20 根 swing low / ATR 都可算且 entry <= 现价。"""
    close = [10.0 + 0.25 * i for i in range(bars)]
    return pd.DataFrame({
        "date": pd.date_range("2026-05-01", periods=bars, freq="D"),
        "open": [c - 0.10 for c in close],
        "high": [c + 0.30 for c in close],
        "low": [c - 0.30 for c in close],
        "close": close,
        "volume": [1_000_000] * bars,
    })


def test_rule_path_builds_from_real_derive_price_levels():
    frame = _ohlcv(30)
    current_price = float(frame["close"].iloc[-1])
    levels = derive_price_levels(frame)
    assert levels.entry is not None, "前置条件:30 根足以算出价位"

    signal = build_from_price_levels(
        levels, invalidation=_invalidation(), current_price=current_price, **COMMON
    )

    assert isinstance(signal, TradeSignal)
    assert signal.source == "rule"
    assert signal.direction == "long"
    assert signal.entry_zone.low == signal.entry_zone.high == levels.entry   # 退化点区间
    assert signal.stop == levels.stop
    assert signal.targets == [levels.target]
    assert signal.position_size is None
    assert signal.evidence is None
    assert signal.risk_reward > 0


def test_rule_path_fail_closed_on_insufficient_window():
    """窗口不足 → derive_price_levels 返回全 None → 构造器返回 None,不抛异常。"""
    levels = derive_price_levels(_ohlcv(10))
    assert levels.entry is None
    assert build_from_price_levels(
        levels, invalidation=_invalidation(), current_price=12.5, **COMMON
    ) is None


def test_rule_path_fail_closed_when_entry_above_current_price():
    levels = PriceLevels(entry=11.0, stop=7.0, target=19.0, risk_reward=2.0)
    assert build_from_price_levels(
        levels, invalidation=_invalidation(), current_price=10.0, **COMMON
    ) is None
    assert build_from_price_levels(
        levels, invalidation=_invalidation(), current_price=13.0, **COMMON
    ) is not None


@pytest.mark.parametrize("levels", [
    PriceLevels(entry=11.0, stop=None, target=19.0, risk_reward=None),
    PriceLevels(entry=11.0, stop=7.0, target=None, risk_reward=None),
    PriceLevels(entry=11.0, stop=12.0, target=19.0, risk_reward=None),   # stop 高于 entry
    PriceLevels(entry=11.0, stop=7.0, target=10.0, risk_reward=None),    # target 低于 entry
])
def test_rule_path_fail_closed_on_bad_levels(levels):
    assert build_from_price_levels(
        levels, invalidation=_invalidation(), current_price=13.0, **COMMON
    ) is None


def _sniper(**overrides):
    payload = dict(ideal_buy="19.00", secondary_buy=None,
                   stop_loss="16.00", take_profit="25.00")
    payload.update(overrides)
    return SniperPoints(**payload)


def test_llm_path_assembles_entry_zone_from_both_buy_points():
    """secondary_buy 是区间第二端,LLM 一直在产,只是从没被组装过。"""
    signal = build_from_sniper_points(
        _sniper(secondary_buy="17.80元"), invalidation=_invalidation(), **COMMON
    )
    assert isinstance(signal, TradeSignal)
    assert signal.source == "llm"
    assert signal.direction == "long"
    assert (signal.entry_zone.low, signal.entry_zone.high) == (17.8, 19.0)
    assert signal.stop == 16.0
    assert signal.targets == [25.0]


@pytest.mark.parametrize("secondary, why", [
    (None, "缺失"),
    ("-5", "parse_sniper_value('-5') -> -5.0,非正"),
    ("inf", "parse_sniper_value('inf') -> inf,非有限"),
    ("nan", "parse_sniper_value('nan') -> nan,非有限"),
])
def test_llm_path_degenerates_when_secondary_buy_is_unusable(secondary, why):
    """secondary_buy 是可选的区间下端而非必需价位:坏值只降级为退化点区间,不使信号作废。

    'inf' 一例不可省:若守卫漏掉 math.isfinite 写成 `secondary > 0`,则 inf > 0 为真,
    zone_high = max(19, inf) = inf,被 trade_levels_invalid 拒绝 → 返回 None。
    可契约要求它退化为一条**合法**信号。这个变异逃得过其余全部测试。
    'nan' 走另一条路(nan > 0 为假,恰好被 > 0 挡下),故只测 nan 抓不到该变异。
    """
    signal = build_from_sniper_points(
        _sniper(secondary_buy=secondary), invalidation=_invalidation(), **COMMON
    )
    assert signal is not None, why
    assert signal.entry_zone.low == signal.entry_zone.high == 19.0


@pytest.mark.parametrize("overrides, why", [
    (dict(stop_loss=None), "stop_loss 缺失"),
    (dict(stop_loss="20.00"), "stop_loss 高于 ideal_buy,排序违反"),
    (dict(ideal_buy="0"), "parse_sniper_value('0') -> 0.0 泄漏"),
    (dict(take_profit="inf"), "parse_sniper_value('inf') -> inf 泄漏"),
])
def test_llm_path_fail_closed_returns_none_without_raising(overrides, why):
    """fail-closed:返回 None,**不**抛异常(不得用 try/except ValidationError 吞)。"""
    assert build_from_sniper_points(
        _sniper(**overrides), invalidation=_invalidation(), **COMMON
    ) is None, why


def test_parse_sniper_value_inherited_behaviour_is_characterized():
    """锁住既有行为,防有人「顺手修」。构造器原样继承,不在本增量修改。"""
    assert parse_sniper_value("18.50-19.00") == 19.0    # 区间字符串塌缩到上界
    assert parse_sniper_value("0") == 0.0               # 字符串入口无 >0 守卫
    assert parse_sniper_value(0) is None                # 数值入口有 >0 守卫
    assert parse_sniper_value("inf") == float("inf")    # 字符串入口无有限性守卫


REPO_ROOT = Path(__file__).resolve().parents[1]

# resolve_marker_hit_fields(src/services/signal_hit_rate.py:117-120)的缺桶哨兵
RESOLVER_NONE_SENTINEL = {
    "hit_rate": None, "hit_sample": None, "verified": False, "ci_low": None,
    "ci_high": None, "baseline_excess": None, "horizon": None,
    "ci_low_corrected": None, "family_size": None, "risk_metrics": None, "oos": None,
}

# 有桶但未通过超额判定:verified=False 却带真实样本。八个字段值互不相同以锁住串位。
RESOLVER_REAL_BUCKET = {
    "hit_rate": 0.61, "hit_sample": 37, "verified": False, "ci_low": 0.52,
    "ci_high": 0.71, "baseline_excess": 0.09, "horizon": 10,
    "ci_low_corrected": 0.48, "family_size": 23,
    "risk_metrics": {"sharpe": 1.0}, "oos": {"train": 1},
}


def _llm_signal():
    return build_from_sniper_points(_sniper(), invalidation=_invalidation(), **COMMON)


def test_attach_evidence_no_hit_fields():
    assert attach_evidence(_llm_signal(), None).evidence is None


def test_attach_evidence_resolver_none_sentinel_means_no_stats():
    assert attach_evidence(_llm_signal(), RESOLVER_NONE_SENTINEL).evidence is None


def test_attach_evidence_real_bucket_survives_verified_false():
    """判别式是 hit_sample is not None,不是 verified。"""
    evidence = attach_evidence(_llm_signal(), RESOLVER_REAL_BUCKET).evidence
    assert evidence is not None
    assert evidence.verified is False
    assert evidence.hit_sample == 37


def test_attach_evidence_maps_every_field_by_sentinel():
    """八个字段值互不相同:任何一对串位都必红。"""
    evidence = attach_evidence(_llm_signal(), RESOLVER_REAL_BUCKET).evidence
    assert evidence.hit_rate == 0.61
    assert evidence.hit_sample == 37
    assert evidence.ci_low == 0.52
    assert evidence.ci_high == 0.71
    assert evidence.baseline_excess == 0.09
    assert evidence.ci_low_corrected == 0.48
    assert evidence.family_size == 23
    assert evidence.verified is False


def test_attach_evidence_drops_risk_metrics_and_oos():
    """两生产者形状不同且为描述性统计,不进契约。"""
    evidence = attach_evidence(_llm_signal(), RESOLVER_REAL_BUCKET).evidence
    assert not hasattr(evidence, "risk_metrics")
    assert not hasattr(evidence, "oos")


def test_attach_evidence_raises_on_horizon_mismatch():
    """把 5 根窗口的统计附到 10 根窗口的信号上是编程错误,必须响,不静默。"""
    mismatched = {**RESOLVER_REAL_BUCKET, "horizon": 5}
    with pytest.raises(ValueError, match="horizon"):
        attach_evidence(_llm_signal(), mismatched)


def test_attach_evidence_returns_new_object():
    original = _llm_signal()
    updated = attach_evidence(original, RESOLVER_REAL_BUCKET)
    assert updated is not original
    assert original.evidence is None


def test_builder_module_declares_no_config_or_db_dependency():
    """构造器纯度。

    **不**用 monkeypatch 打 get_config:它根本不在本模块的调用路径上,打一个永不被
    调的桩,任何实现都能通过——那是恒真测试。改测「这个文件 import 了什么」这个
    可判定的事实。
    """
    source = (REPO_ROOT / "src" / "services" / "trade_signal_builder.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported.add(module)
            imported.update(f"{module}.{alias.name}" for alias in node.names)

    # 前缀 + 末段精确匹配,不用子串:子串判据里的 "_repo" 迟早会误伤
    # "src.schemas.report_schema" 这类合法名字。
    forbidden_prefixes = ("src.config", "src.storage", "src.repositories", "sqlalchemy")
    forbidden_names = {"get_config"}
    offenders = sorted(
        name for name in imported
        if name.startswith(forbidden_prefixes) or name.rsplit(".", 1)[-1] in forbidden_names
    )
    assert not offenders, f"构造器不得依赖 config / DB:{offenders}"
