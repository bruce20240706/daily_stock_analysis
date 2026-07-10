# -*- coding: utf-8 -*-
"""Inc 0 TradeSignal 契约的五条 drift-lock。

一条 import 白名单(锁零接线);两条 canonical-derived 跨源钉死(两边各有独立真源,
非 tautology):SignalInterval ↔ SUPPORTED_INTERVALS、RESERVED_SIGNAL_TYPE ↔
BASELINE_SIGNAL_TYPE;一条哨兵字段映射锁(锁构造器内部赋值不串位、不误读
levels.risk_reward);一条证据键锁(锁 _EVIDENCE_KEYS 相对真实 resolver 输出集合的
子集关系,防 resolver 改名后静默漏映射)。
"""

from pathlib import Path
from typing import get_args

import pytest

from src.core.intraday_backtest import SUPPORTED_INTERVALS
from src.schemas.trade_signal import RESERVED_SIGNAL_TYPE, Invalidation, SignalInterval
from src.services.signal_backtest import BASELINE_SIGNAL_TYPE
from src.services.signal_hit_rate import resolve_marker_hit_fields
from src.services.trade_signal_builder import _EVIDENCE_KEYS, build_from_price_levels
from src.services.volume_price_signals import PriceLevels

REPO_ROOT = Path(__file__).resolve().parents[1]

# 扫描时跳过的目录。tests/ 必须跳过(本文件自身就满篇 trade_signal)。
_SKIP_DIR_PARTS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    "tests", "build", "dist", ".worktrees",
}

# 当前允许提及 TradeSignal 的**全部**非测试模块。
# 接线到任何 runtime 路径前,先读 docs/trade-signal-contract.md 的呈现边界;
# 修改本白名单是一个显式、可 diff、可审计的动作。
ALLOWED_MODULES = {
    "src/schemas/trade_signal.py",
    "src/services/trade_signal_builder.py",
}


def _non_test_python_files():
    for path in REPO_ROOT.rglob("*.py"):
        relative = path.relative_to(REPO_ROOT)
        if any(part in _SKIP_DIR_PARTS for part in relative.parts):
            continue
        yield relative.as_posix(), path


def test_trade_signal_import_allowlist():
    """零接线不变式:TradeSignal 不被任何 runtime 路径消费。

    合规档 (a) 自用/内部 下,信号只能呈现为「分析结论」而非「操作指令」。Inc 0 靠
    「压根不呈现」满足这条边界。白名单强于黑名单:它连 main.py、scripts/ 以及任何
    没人预先想到的路径一并管住。
    """
    hits = set()
    for relative, path in _non_test_python_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "trade_signal" in text:
            hits.add(relative)

    # 阳性对照:扫描器必须活着。没有这条,当扫描逻辑写坏(路径拼错、编码异常吞掉
    # 全部文件、rglob 模式失效)时 hits 会是空集,而空集永远 ⊆ 白名单,这条 lock
    # 就悄无声息地永远绿。
    assert "src/services/trade_signal_builder.py" in hits, "扫描器失效:阳性对照未命中"

    unexpected = sorted(hits - ALLOWED_MODULES)
    assert not unexpected, (
        "TradeSignal 已被接线到白名单之外的模块:"
        f"{unexpected};接线前请先读 docs/trade-signal-contract.md 的呈现边界"
    )


def test_signal_interval_matches_supported_intervals():
    """SignalInterval 是新拼写,由本 lock 钉死在回测模块的规范集合上。"""
    assert set(get_args(SignalInterval)) == set(SUPPORTED_INTERVALS)


def test_reserved_signal_type_pinned_to_backtest_sentinel():
    """schema 层不能 import 回测模块(层级倒挂),故 "__baseline__" 是字面量。"""
    assert RESERVED_SIGNAL_TYPE == BASELINE_SIGNAL_TYPE


def test_rule_path_field_mapping_sentinels():
    """哨兵值互不相同、互不为倍数:兄弟字段串位、漏字段必红。

    risk_reward 的哨兵取 99.0 而非真值 2.0:若取 2.0,则「构造器错误地读了
    levels.risk_reward」与「按公式重算」给出同一个数,断言失去区分力。
    """
    levels = PriceLevels(entry=11.0, stop=7.0, target=19.0, risk_reward=99.0)
    signal = build_from_price_levels(
        levels,
        code="600519",
        market="cn",
        signal_type="breakout",
        interval="1d",
        horizon_bars=10,
        as_of="2026-07-10T00:00:00",
        confidence="high",
        invalidation=Invalidation(note="哨兵"),
        current_price=13.0,
    )
    assert signal is not None
    assert signal.entry_zone.low == 11.0
    assert signal.entry_zone.high == 11.0
    assert signal.stop == 7.0
    assert signal.targets == [19.0]
    assert signal.risk_reward == pytest.approx(2.0)   # (19-11)/(11-7);证明 99.0 被无视


def test_evidence_keys_are_subset_of_real_resolver_output():
    """_EVIDENCE_KEYS 钉死在**真实** resolve_marker_hit_fields 输出的 key 集合上。

    tests/test_trade_signal_builder.py 里的 RESOLVER_REAL_BUCKET 是手写字典,形状上
    模仿 resolve_marker_hit_fields 的返回值,但从未真正流经该函数——如果有人改名
    resolver 里的某个 key(比如把 baseline_excess 改成别的名字),RESOLVER_REAL_BUCKET
    不会察觉,attach_evidence 会静默把 evidence.baseline_excess 置 None,而 builder
    测试仍然全绿。这正是仓库明确禁止的「mock 掉真实风险层」模式,手写镜像字典本身
    管不住这条契约,必须有一条真正调用真实 resolver 的锁。

    本测试改为直接调用 resolve_marker_hit_fields("breakout", "NOT_A_REAL_CODE")。
    "NOT_A_REAL_CODE" 不被任何市场规则识别,resolver 内部
    `market = get_market_for_stock(code)` 会返回 None;signal_hit_rate.py 里
    `if market is None: return dict(_none)` 这一行**先于** `cfg = get_config()`,
    也先于任何 Repository 构造——因此这是一次零 I/O、零 config、零 DB 的纯调用,
    但拿到的是 resolver 真实定义的 key 名集合,不是我们手写猜测出来的。
    """
    result = resolve_marker_hit_fields("breakout", "NOT_A_REAL_CODE")

    # 证明真的走了零 I/O 的哨兵早退路径,不是意外命中了某个真实数据桶。
    assert result["hit_sample"] is None

    # 真正的锁:resolver 一旦改名/删掉 _EVIDENCE_KEYS 依赖的任何一个 key,这里立刻红。
    assert set(_EVIDENCE_KEYS) <= set(result)

    # resolver 返回但 SignalEvidence 刻意丢弃的 key 必须恰好是这三个;resolver 新增
    # 字段会让这个差集变化,逼一次有意识的收进/继续丢的决定,而不是被默默忽略。
    dropped = set(result) - set(_EVIDENCE_KEYS)
    assert dropped == {"horizon", "risk_metrics", "oos"}
