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
from typing import Any, Optional, Tuple

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

# 数字抽取：**必须**含指数段。缺了它 '1.23e-5' 会被抽成 1.23（错 5 个数量级）。
_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")

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
        number = float(value)
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
    """
    if not (math.isfinite(claimed) and math.isfinite(fact)):
        return False
    tolerance = max(10.0 ** (-decimals), abs(fact) * _REL_NOISE_FLOOR)
    return abs(claimed - fact) <= tolerance
