# -*- coding: utf-8 -*-
"""Sniper 价位文本解析(单一真源)。

`DatabaseManager._parse_sniper_value` 与 `src.claim_validation.validate_structure`
共用本函数,保证「守卫判定的数」与「落库的数」永远是同一个。
函数体自 src/storage.py 逐字迁出,行为不变。
"""

import re
from typing import Any, Optional


def parse_sniper_value(value: Any) -> Optional[float]:
    """
    Parse a sniper point value from various formats to float.

    Handles: numeric types, plain number strings, Chinese price formats
    like "18.50元", range formats like "18.50-19.00", and text with
    embedded numbers while filtering out MA indicators.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v if v > 0 else None

    text = str(value).replace(',', '').replace('，', '').strip()
    if not text or text == '-' or text == '—' or text == 'N/A':
        return None

    # 尝试直接解析纯数字字符串
    try:
        return float(text)
    except ValueError:
        pass

    # 优先截取 "：" 到 "元" 之间的价格，避免误提取 MA5/MA10 等技术指标数字
    colon_pos = max(text.rfind("："), text.rfind(":"))
    yuan_pos = text.find("元", colon_pos + 1 if colon_pos != -1 else 0)
    if yuan_pos != -1:
        segment_start = colon_pos + 1 if colon_pos != -1 else 0
        segment = text[segment_start:yuan_pos]

        # 使用 finditer 并过滤掉 MA 开头的数字
        matches = list(re.finditer(r"-?\d+(?:\.\d+)?", segment))
        valid_numbers = []
        for m in matches:
            # 检查前面是否是 "MA" (忽略大小写)
            start_idx = m.start()
            if start_idx >= 2:
                prefix = segment[start_idx-2:start_idx].upper()
                if prefix == "MA":
                    continue
            valid_numbers.append(m.group())

        if valid_numbers:
            try:
                return abs(float(valid_numbers[-1]))
            except ValueError:
                pass

    # 兜底：无"元"字时，先截去第一个括号后的内容，避免误提取括号内技术指标数字
    # 例如 "1.52-1.53 (回踩MA5/10附近)" → 仅在 "1.52-1.53 " 中搜索
    paren_pos = len(text)
    for paren_char in ('(', '（'):
        pos = text.find(paren_char)
        if pos != -1:
            paren_pos = min(paren_pos, pos)
    search_text = text[:paren_pos].strip() or text  # 括号前为空时降级用全文

    valid_numbers = []
    for m in re.finditer(r"\d+(?:\.\d+)?", search_text):
        start_idx = m.start()
        if start_idx >= 2 and search_text[start_idx-2:start_idx].upper() == "MA":
            continue
        valid_numbers.append(m.group())
    if valid_numbers:
        try:
            return float(valid_numbers[-1])
        except ValueError:
            pass
    return None
