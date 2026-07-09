# -*- coding: utf-8 -*-
"""LLM claim-validation 守卫(Inc 3)。

把 LLM 陈述的数值与 prompt 里实际喂给它的数值交叉核对(转录类),
并校验其自主生成的买卖计划是否内部自洽(结构类)。
不一致时标注 + 分级降权;绝不覆盖数值、不改决策方向、不触发重试。

设计见 docs/superpowers/specs/2026-07-09-llm-claim-validation-design.md
"""

import logging
import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from src.sniper_parsing import parse_sniper_value

logger = logging.getLogger(__name__)

# 转录类可校验的 9 个字段(canonical key)。
FACT_KEYS: Tuple[str, ...] = (
    "current_price",
    "ma5",
    "ma10",
    "ma20",
    "bias_ma5",
    "volume_ratio",
    "turnover_rate",
    "profit_ratio",
    "avg_cost",
)

# 数字抽取：**必须**含指数段与前导点分支。
# 缺指数段 → '1.23e-5' 被抽成 1.23（错 5 个数量级）。
# 缺前导点 → '-.05%' 被抽成 (5.0, 0)（符号与数量级双双丢失）。
# 两者都会把**正确**的 claim 判成编造 —— 假警报比漏报更糟。
_NUMBER_RE = re.compile(r"[-+]?(?:\d+\.\d+|\.\d+|\d+)(?:[eE][-+]?\d+)?")

# `d` 的钳制区间。上界 8 兼顾 crypto 极小价（1.23e-5 → d=7）与 float64 分辨率。
_MIN_DECIMALS = 0
_MAX_DECIMALS = 8

# 浮点噪声下限系数。
_REL_NOISE_FLOOR = 1e-9

# absent 语义：**数值 0 不在此列**（bias_ma5=0 是合法 claim）。
_ABSENT_TEXTS = frozenset(
    {
        "",
        "-",
        "—",
        "n/a",
        "na",
        "none",
        "null",
        "unknown",
        "tbd",
        "未知",
        "暂无",
        "待补充",
        "数据缺失",
    }
)


def _is_claim_absent(value: Any) -> bool:
    """claim 是否「压根没给」。

    刻意**不复用** `analyzer._is_value_placeholder`：它委托的
    `is_chip_placeholder_value` 把数值 0 判为占位符，而 `bias_ma5 = 0`
    在 prompt 里就渲染成 `+0.00%`，是一个合法的、必须参与校验的 claim。
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return False
    return str(value).strip().lower() in _ABSENT_TEXTS


def _decimals_of(token: str) -> Optional[int]:
    """求文本的十进制小数位数。

    必须用 Decimal，不能用 `len(token.split('.')[-1])`：
    后者对 '1800' 返回 4（整串无小数点时 [-1] 是它自己），
    容差退化成 1e-4，任何无小数点的 claim 都会被误报。
    """
    try:
        exponent = Decimal(token).as_tuple().exponent
    except (InvalidOperation, ValueError):
        return None
    if not isinstance(exponent, int):  # Decimal('NaN'/'Infinity') 的 exponent 是 str
        return None
    return max(_MIN_DECIMALS, min(_MAX_DECIMALS, -exponent))


def extract_numeric_claim(value: Any) -> Optional[Tuple[float, int]]:
    """从 LLM 的 claim 抽出 `(数值, 小数位数 d)`。

    `d` 表示 LLM 自己声称的精度：声称得越精确，容差越严。
    - 字符串 → 从文本 token 求 d（保留尾零：'12.30' → d=2）
    - JSON number → 从 repr 求 d（尾零已被 json.loads 抹掉，d 偏小 →
      容差偏大 → 只可能漏报，绝不会误报）

    返回 None 表示「不可校验」（absent / 非数值 / 非有限）。
    """
    if _is_claim_absent(value):
        return None

    if isinstance(value, (int, float)):  # bool 已在 _is_claim_absent 里排除
        try:
            number = float(value)
        except OverflowError:
            # 超出 float64 范围的 Python int（如 10**400）：float() 抛
            # OverflowError 而非返回 inf。字符串分支的 float(token) 在
            # 同等量级下会静默返回 inf，交由下面的 isfinite 守卫收口；
            # 这里用 except 补齐同一「非有限 → None」契约，不让主流程崩。
            return None
        if not math.isfinite(number):
            return None
        # 注意：对 repr(value)（原始值）取位数，不是 repr(number)（转 float 后的值）。
        # int 1800 转 float 后 repr 是 '1800.0'（1 位小数，误判精度）；
        # 对原始 int 取 repr 是 '1800'（0 位小数，正确）。float 输入两者一致。
        decimals = _decimals_of(repr(value))
        return None if decimals is None else (number, decimals)

    text = str(value).replace(",", "").replace("，", "").strip()
    match = _NUMBER_RE.search(text)
    if not match:
        return None
    token = match.group(0)
    try:
        number = float(token)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    decimals = _decimals_of(token)
    return None if decimals is None else (number, decimals)


def claim_matches_fact(claimed: float, decimals: int, fact: float) -> bool:
    """`|claimed - fact| <= max(10^(-d), |fact| * 1e-9)`。

    自校准：LLM 写 '1800'（d=0）只要求误差 < 1；写 '1800.42'（d=2）
    要求误差 < 0.01。凭空多出一位有效数字会被逮住。

    `decimals` 在函数内部不做钳制：调用方须保证传入值已在
    `[_MIN_DECIMALS, _MAX_DECIMALS]`（即 `[0, 8]`）内 —— `extract_numeric_claim`
    已钳制过；直接传字面量的调用方需自行保证。
    """
    if not (math.isfinite(claimed) and math.isfinite(fact)):
        return False
    tolerance = max(10.0 ** (-decimals), abs(fact) * _REL_NOISE_FLOOR)
    return abs(claimed - fact) <= tolerance


_SNIPER_FIELDS = ("ideal_buy", "secondary_buy", "stop_loss", "take_profit")


def validate_structure(sniper_points: Any) -> Dict[str, Any]:
    """校验 LLM 自主生成的买卖计划是否内部自洽。

    **签名只收 sniper_points**：拿不到 current_price 的函数不可能拿它做判据。
    `ideal_buy > current_price`（突破买入）是完全合法的计划，
    照搬 `is_invalid_price_level` 的 `entry <= current_price` 会系统性误杀。

    两类判据：
    1. 单字段抽出的值非有限或 `<= 0` → violation（与落库口径一致：
       `parse_sniper_value` 对数值类型的非正输入在上游就返回 None——
       该字段视为缺失，不在此列；只有字符串/文本抽出的非正值才会落到
       这一判据，因为它们会被同一个 `parse_sniper_value` 实际落库）。
    2. 字段两两之间的价位顺序（stop < entry < target 等）不自洽 → violation。

    `parse_sniper_value` 返回 None（抽不出数）→ 字段视为缺失，跳过该字段
    参与的判据，不因缺失而报违规。violations 非空时整体判 violation；
    否则若没有任何可比较的字段对，判 not_applicable。
    """
    if not isinstance(sniper_points, dict) or not sniper_points:
        return {"status": "not_applicable", "reason": "no_sniper_points", "violations": []}

    parsed: Dict[str, float] = {}
    violations: List[str] = []
    for field in _SNIPER_FIELDS:
        number = parse_sniper_value(sniper_points.get(field))
        if number is None:
            continue  # 抽不出数 → 字段缺失（与落库的 NULL 一致）
        if not math.isfinite(number):
            violations.append(f"{field}({number}) 非有限")
            continue
        if number <= 0:
            violations.append(f"{field}({number}) <= 0")
            continue
        parsed[field] = number

    entry = parsed.get("ideal_buy")
    second = parsed.get("secondary_buy")
    stop = parsed.get("stop_loss")
    target = parsed.get("take_profit")

    pairs: List[Tuple[str, Optional[float], str, Optional[float]]] = [
        ("stop_loss", stop, "ideal_buy", entry),
        ("ideal_buy", entry, "take_profit", target),
        ("stop_loss", stop, "take_profit", target),
        ("stop_loss", stop, "secondary_buy", second),
        ("secondary_buy", second, "take_profit", target),
    ]

    comparable = 0
    for lo_name, lo, hi_name, hi in pairs:
        if lo is None or hi is None:
            continue
        comparable += 1
        if not lo < hi:
            violations.append(f"{lo_name}({lo}) >= {hi_name}({hi})")

    # violations 优先：只有 `ideal_buy: "-5"` 一个字段时凑不出任何序关系，
    # 但它仍是 violation，不是 not_applicable。
    if violations:
        return {"status": "violation", "reason": None, "violations": violations}
    if comparable == 0:
        return {"status": "not_applicable", "reason": "insufficient_fields", "violations": []}
    return {"status": "ok", "reason": None, "violations": []}
