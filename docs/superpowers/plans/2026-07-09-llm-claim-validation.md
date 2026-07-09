# LLM claim-validation 守卫(Inc 3)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给个股 LLM 分析加一个 opt-in 守卫,把 LLM 陈述的数值与 prompt 里实际喂给它的数值交叉核对(转录类)、并校验其自主生成的买卖计划是否内部自洽(结构类);不一致则标注 + 分级降权,绝不覆盖数值、不改决策方向、不触发重试。

**Architecture:** 两个纯函数模块(`src/sniper_parsing.py` / `src/claim_validation.py`)+ 两步挂载。步 A `extract_llm_claims` 在 pipeline **任何 in-place 回填之前**快照 LLM 原始 claim(纯读);步 B `apply_claim_validation` 在 `apply_phase_decision_guardrails` **之后**判定、写 `dashboard["claim_validation"]`、单调封顶置信度。`prompt_facts` 由并列纯函数 `collect_prompt_facts(context)` 采集,`_format_prompt` 零改动,漂移由 drift-lock 测试锁定。

**Tech Stack:** Python 3(stdlib `decimal` / `re` / `math`)、pytest + unittest、Jinja2(报告模板)、TypeScript(仅 locale 文案)。

**Spec:** `docs/superpowers/specs/2026-07-09-llm-claim-validation-design.md`(已过对抗审查 + 顶级复审,1 Blocker / 2 Important / 4 Minor 全部收敛)

---

## Global Constraints

以下约束对**每一个 task** 都生效,不再逐条重复:

- **opt-in,默认字节级不变。** 开关 `LLM_CLAIM_VALIDATION_ENABLED` 默认 `false`。关闭时:`collect_prompt_facts` 一次也不被调用、`result.prompt_facts is None`、`dashboard` 中**没有** `claim_validation` 键、报告文本逐字节不变。
- **追加字段优先**,绝不删改既有字段;绝不清空 `sniper_points`。
- **`d` 必须用 `decimal.Decimal(...).as_tuple().exponent` 求**,禁止 `len(token.split('.')[-1])`(对 `'1800'` 返回 4,导致任何无小数点的 claim 被误报)。
- **数字抽取正则必须同时接受指数段与前导点**:`r"[-+]?(?:\d+\.\d+|\.\d+|\d+)(?:[eE][-+]?\d+)?"`。缺指数段会把 `'1.23e-5'` 抽成 `1.23`(错 5 个数量级);缺前导点分支会把 `'-.05%'` 抽成 `(5.0, 0)`(符号与数量级双双丢失)。两者都会造成**假警报** —— 把正确的 claim 判成编造。
- **`_format_prompt` 零改动**(约 30 个测试断言其字符串返回值,另有 2 处 `patch.object(..., return_value="prompt")`)。
- **`GeminiAnalyzer` 是跨 `ThreadPoolExecutor` 共享的单例**(`pipeline.py:140` 构造,`:579` 并发调用)。**禁止用实例属性存 facts**(竞态)。
- 提交信息用**英文类型前缀 + 中文正文**,**不加** `Co-Authored-By`,**不加**工具/agent 前缀,单条 `git commit -m`。
- 所有 python 命令前置:`export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"`(路径含空格,必须带引号)。
- 后端门禁:`./scripts/ci_gate.sh`。**禁止 `| tail` 掩盖退出码。**
- 本增量**会触发 web-gate**(Task 9 改 `apps/dsa-web/src/locales/settingsHelp.ts`)。

### 已核实的事实(不要重新推导,也不要假定其它)

| 事实 | 锚点 |
| --- | --- |
| 非-agent 分支 `result` 来自 `self.analyzer.analyze(...)` | `src/core/pipeline.py:579` |
| **agent 分支不走 `GeminiAnalyzer.analyze()`**,`result` 来自 `_agent_result_to_analysis_result(...)` | `src/core/pipeline.py:1108`(其上 `:1095` 是 `executor.run`) |
| 因此 agent 路径**永远没有 `prompt_facts`** → 转录类恒 `not_applicable`,结构类照跑 | — |
| 两条分支**第一个 in-place 改写 `dashboard` 的调用都是 `normalize_chip_structure_availability`** | 非-agent `pipeline.py:623-624`;agent `pipeline.py:1149-1150` |
| **但守卫条件不同**:非-agent 是 `if result:`,agent 是 `if result and chip_data is not None:` | 同上。**不得照抄插入代码** |
| 回填顺序(两条分支一致):chip → capital_flow → margin → ggt → price_position → stabilize → phase guardrail → `_refresh_decision_action_for_final_result` | 非-agent `:624..656`;agent `:1150..1187` |
| `_refresh_decision_action_for_final_result` **不消费 `confidence_level`** | `pipeline.py:1475`,函数体内 `confidence` 零命中 |
| `_parse_sniper_value` 所属类是 **`DatabaseManager`**(不是 `Storage`) | `src/storage.py:831` 类定义,`:2204` 方法 |
| `pipeline` 类名是 **`StockAnalysisPipeline`** | `src/core/pipeline.py` |

---

## File Structure

| 文件 | 责任 | 动作 |
| --- | --- | --- |
| `src/sniper_parsing.py` | sniper 价位文本 → float 的**单一真源**(结构判据与落库共用,防口径漂移) | **新建** |
| `src/storage.py` | `DatabaseManager._parse_sniper_value` 改为委托 `sniper_parsing.parse_sniper_value` | 改 |
| `src/claim_validation.py` | 守卫全部逻辑:数值抽取 / 容差 / 结构判据 / facts 采集 / claim 快照 / 判定与降权 | **新建** |
| `src/config.py` | 新增 `llm_claim_validation_enabled: bool = False` | 改 |
| `src/analyzer.py` | `AnalysisResult.prompt_facts` 内部字段;`analyze()` 内门控调用 `collect_prompt_facts` | 改 |
| `src/core/pipeline.py` | 两条分支各插 A / B 两点,共 4 处 | 改 |
| `src/notification.py` | `_render_claim_validation_section` + 在 `generate_dashboard_report` 调用 | 改 |
| `templates/report_markdown.j2` | Jinja 引擎侧同款段落 | 改 |
| `src/report_language.py` | zh / en 各 3 个新标签 | 改 |
| `src/core/config_registry.py` | 注册 `LLM_CLAIM_VALIDATION_ENABLED`(16 键) | 改 |
| `apps/dsa-web/src/locales/settingsHelp.ts` | zhCN + enUS 各一条 help 文案 | 改 |
| `.env.example` | 注释行 `# LLM_CLAIM_VALIDATION_ENABLED=false` | 改 |
| `docs/llm-claim-validation.md` | 专题文档 | **新建** |
| `docs/CHANGELOG.md` | `[Unreleased]` 扁平条目 | 改 |

**测试文件**:`tests/test_sniper_parsing.py`(新)、`tests/test_claim_validation.py`(新)、`tests/test_claim_validation_pipeline.py`(新,集成)、`tests/test_claim_validation_render.py`(新)。既有 `tests/test_storage.py` / `tests/test_config_registry.py` 必须保持绿。

---

## Task 1: `src/sniper_parsing.py` —— 抽出共享 sniper 抽数器

结构判据必须与**落库口径一致**,否则会出现「守卫判合法,但存进 DB 的是另一个数」。既有抽数器是 `DatabaseManager` 的私有 `@staticmethod`。不重写(平行实现违反 AGENTS.md),不跨模块 import 私有 staticmethod(层次不清)。**机械剪切-粘贴-委托。**

**Files:**
- Create: `src/sniper_parsing.py`
- Modify: `src/storage.py:2203-2273`
- Test: `tests/test_sniper_parsing.py`(新);`tests/test_storage.py:90-139`(必须保持绿)

**Interfaces:**
- Produces: `parse_sniper_value(value: Any) -> Optional[float]`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_sniper_parsing.py`:

```python
# -*- coding: utf-8 -*-
"""Tests for the shared sniper price-text parser (Inc 3)."""

import unittest

from src.sniper_parsing import parse_sniper_value


class TestParseSniperValue(unittest.TestCase):
    def test_numeric_passthrough(self) -> None:
        self.assertEqual(parse_sniper_value(12.5), 12.5)
        self.assertEqual(parse_sniper_value(13), 13.0)

    def test_non_positive_numeric_is_none(self) -> None:
        self.assertIsNone(parse_sniper_value(0))
        self.assertIsNone(parse_sniper_value(-1.0))

    def test_plain_number_string(self) -> None:
        self.assertEqual(parse_sniper_value("12.34"), 12.34)

    def test_chinese_price_format(self) -> None:
        self.assertEqual(parse_sniper_value("止损位：18.50元"), 18.50)

    def test_range_takes_last_number(self) -> None:
        self.assertEqual(parse_sniper_value("180-182"), 182.0)

    def test_ma_indicator_digits_are_skipped(self) -> None:
        self.assertEqual(parse_sniper_value("93.40下方（MA20支撑）"), 93.4)
        self.assertIn(parse_sniper_value("1.52-1.53 (回踩MA5/10附近)"), [1.52, 1.53])

    def test_absent_inputs(self) -> None:
        for bad in (None, "", "-", "—", "N/A", "没有数字", "MA5但没有元"):
            self.assertIsNone(parse_sniper_value(bad), bad)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
python -m pytest tests/test_sniper_parsing.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.sniper_parsing'`

- [ ] **Step 3: 创建 `src/sniper_parsing.py`**

把 `src/storage.py:2204-2273` 的**函数体逐字**搬过来。只做三处机械变换:去掉 `@staticmethod`、去掉一级缩进、改名为 `parse_sniper_value`。**函数体一个字符都不要改**(等价性由此保证,不由测试保证)。

```python
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
                prefix = segment[start_idx - 2:start_idx].upper()
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
        if start_idx >= 2 and search_text[start_idx - 2:start_idx].upper() == "MA":
            continue
        valid_numbers.append(m.group())
    if valid_numbers:
        try:
            return float(valid_numbers[-1])
        except ValueError:
            pass
    return None
```

- [ ] **Step 4: `storage.py` 改为委托**

在 `src/storage.py` 顶部 import 段加(与既有 import 同区):

```python
from src.sniper_parsing import parse_sniper_value
```

把 `src/storage.py:2203-2273` 整段(`@staticmethod` 起、到 `return None` 止)替换为:

```python
    @staticmethod
    def _parse_sniper_value(value: Any) -> Optional[float]:
        """Delegate to the shared parser (single source of truth, see src/sniper_parsing.py)."""
        return parse_sniper_value(value)
```

- [ ] **Step 5: 跑测试确认通过 + 既有夹具仍绿**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
python -m pytest tests/test_sniper_parsing.py tests/test_storage.py -v
```

Expected: 全部 PASS。若 `tests/test_storage.py::TestStorage::test_parse_sniper_value` 变红,说明搬运时改了函数体 —— 回退重搬,**不要改测试**。

- [ ] **Step 6: 提交**

```bash
git add src/sniper_parsing.py src/storage.py tests/test_sniper_parsing.py
git commit -m "refactor: 抽出共享 sniper 价位解析器 parse_sniper_value(storage 改为委托,行为逐字不变),为 claim-validation 结构判据与落库口径提供单一真源"
```

---

## Task 2: 数值抽取与容差 —— `extract_numeric_claim` / `claim_matches_fact` / `_is_claim_absent`

这是整个守卫的核心判据。**Blocker 就在这里**:朴素 `split('.')` 对 `'1800'` 返回 `d=4`,容差退化成 `1e-4`,任何无小数点的 claim 都被误报。

**Files:**
- Create: `src/claim_validation.py`
- Test: `tests/test_claim_validation.py`(新)

**Interfaces:**
- Consumes: 无
- Produces:
  - `FACT_KEYS: Tuple[str, ...]`
  - `_is_claim_absent(value: Any) -> bool`
  - `extract_numeric_claim(value: Any) -> Optional[Tuple[float, int]]` —— 返回 `(值, 小数位数 d)`,`d` 已钳制到 `[0, 8]`
  - `claim_matches_fact(claimed: float, decimals: int, fact: float) -> bool`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_claim_validation.py`:

```python
# -*- coding: utf-8 -*-
"""Tests for LLM claim-validation primitives (Inc 3)."""

import unittest

from src.claim_validation import (
    _is_claim_absent,
    claim_matches_fact,
    extract_numeric_claim,
)


class TestExtractNumericClaim(unittest.TestCase):
    def test_string_keeps_trailing_zero(self) -> None:
        self.assertEqual(extract_numeric_claim("12.30"), (12.30, 2))

    def test_integer_string_has_zero_decimals(self) -> None:
        # D17 回归锁：len('1800'.split('.')[-1]) == 4 会让 tol 退化成 1e-4
        self.assertEqual(extract_numeric_claim("1800"), (1800.0, 0))

    def test_scientific_notation_is_parsed_whole(self) -> None:
        # D17 回归锁：缺指数段的正则会抽成 1.23，错 5 个数量级
        value, decimals = extract_numeric_claim("1.23e-5")
        self.assertAlmostEqual(value, 1.23e-5)
        self.assertEqual(decimals, 7)

    def test_positive_exponent_clamps_to_zero(self) -> None:
        value, decimals = extract_numeric_claim("1e+16")
        self.assertEqual(value, 1e16)
        self.assertEqual(decimals, 0)

    def test_decimals_clamped_to_eight(self) -> None:
        _, decimals = extract_numeric_claim("0.1234567890123")
        self.assertEqual(decimals, 8)

    def test_json_number_float(self) -> None:
        self.assertEqual(extract_numeric_claim(12.3), (12.3, 1))

    def test_json_number_int(self) -> None:
        self.assertEqual(extract_numeric_claim(1800), (1800.0, 0))

    def test_chinese_suffix_and_percent(self) -> None:
        self.assertEqual(extract_numeric_claim("12.34元"), (12.34, 2))
        self.assertEqual(extract_numeric_claim("72.3%"), (72.3, 1))

    def test_bool_is_not_a_number(self) -> None:
        self.assertIsNone(extract_numeric_claim(True))

    def test_non_finite_is_none(self) -> None:
        self.assertIsNone(extract_numeric_claim(float("nan")))
        self.assertIsNone(extract_numeric_claim(float("inf")))

    def test_absent_returns_none(self) -> None:
        for bad in (None, "", "N/A", "待补充"):
            self.assertIsNone(extract_numeric_claim(bad), bad)


class TestClaimMatchesFact(unittest.TestCase):
    def test_integer_rounding_is_legal(self) -> None:
        # 头号示例：fact=1800.4231，LLM 写 "1800"
        self.assertTrue(claim_matches_fact(1800.0, 0, 1800.4231))

    def test_fabricated_integer_is_caught(self) -> None:
        self.assertFalse(claim_matches_fact(1795.0, 0, 1800.4231))

    def test_truncation_and_rounding_both_pass(self) -> None:
        self.assertTrue(claim_matches_fact(3.4, 1, 3.4512))
        self.assertTrue(claim_matches_fact(3.5, 1, 3.4512))

    def test_one_ulp_off_is_caught(self) -> None:
        self.assertFalse(claim_matches_fact(3.3, 1, 3.4512))

    def test_transposition_is_caught(self) -> None:
        self.assertFalse(claim_matches_fact(21.34, 2, 12.34))

    def test_invented_precision_is_caught(self) -> None:
        # prompt 只给了 72.3，LLM 却写 72.34
        self.assertFalse(claim_matches_fact(72.34, 2, 72.3))

    def test_penny_stock(self) -> None:
        self.assertTrue(claim_matches_fact(0.53, 2, 0.5312))

    def test_crypto_tiny_price_still_catches_fabrication(self) -> None:
        # d=7 → tol=1e-7；若 d 被钳到 4，tol=1e-4 会让任何数都 pass
        self.assertTrue(claim_matches_fact(1.23e-5, 7, 1.23e-5))
        self.assertFalse(claim_matches_fact(4.56e-5, 7, 1.23e-5))

    def test_float_noise_floor(self) -> None:
        self.assertTrue(claim_matches_fact(1800.4231, 4, 1800.4231 + 1e-12))


class TestIsClaimAbsent(unittest.TestCase):
    def test_absent_values(self) -> None:
        for v in (None, "", "  ", "N/A", "n/a", "null", "待补充", "数据缺失"):
            self.assertTrue(_is_claim_absent(v), v)

    def test_numeric_zero_is_a_valid_claim(self) -> None:
        # 锁住「不复用 _is_value_placeholder」：它把 0 判为占位符，
        # 而 bias_ma5 = 0 在 prompt 里就渲染成 +0.00%，是合法 claim。
        self.assertFalse(_is_claim_absent(0))
        self.assertFalse(_is_claim_absent(0.0))
        self.assertFalse(_is_claim_absent("0"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
python -m pytest tests/test_claim_validation.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.claim_validation'`

- [ ] **Step 3: 创建 `src/claim_validation.py`(本 task 只写这一段)**

```python
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
        number = float(value)
        if not math.isfinite(number):
            return None
        # 对**原始值**取 repr，不是转换后的 float：repr(float(1800)) == '1800.0' → d=1（错），
        # 而 repr(1800) == '1800' → d=0（对）。int claim 必须得到 d=0。
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
```

- [ ] **Step 4: 跑测试确认通过**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
python -m pytest tests/test_claim_validation.py -v
```

Expected: 全部 PASS(约 20 项)。

- [ ] **Step 5: 变异验证(必做,不写进代码)**

把 `_decimals_of` 临时改成 `return len(token.split('.')[-1])`,重跑:

Expected: `test_integer_string_has_zero_decimals` 与 `test_composed_integer_claim_against_fractional_fact` **变红**。

> 注意:`test_integer_rounding_is_legal` **不会**变红 —— 它直接给 `claim_matches_fact` 传字面量 `decimals=0`,不经过 `_decimals_of`。这正是为什么必须有下面那条**组合路径**测试:它是 §4.1 容差表第一行的真正端到端锁。

确认后**改回来**。这一步证明 D17 的回归锁真的锁得住。

- [ ] **Step 5b: 补组合路径测试(§4.1 表格第一行的端到端锁)**

`extract_numeric_claim` 与 `claim_matches_fact` 各自被锁住,但**合起来**没有任何测试 —— 而「fact=1800.4231,LLM 写 `1800` → pass」正是 §4.1 的头号断言。在 `tests/test_claim_validation.py` 追加:

```python
class TestComposedTolerance(unittest.TestCase):
    """§4.1 容差表的端到端锁：抽取 + 比对合起来跑。

    两个半边各自的单测都绿，组合起来仍可能错（例如 d 从错误的
    repr 求出）。这一类才是守卫真正的行为。
    """

    def _matches(self, claim, fact) -> bool:
        parsed = extract_numeric_claim(claim)
        assert parsed is not None, claim
        value, decimals = parsed
        return claim_matches_fact(value, decimals, fact)

    def test_composed_integer_claim_against_fractional_fact(self) -> None:
        self.assertTrue(self._matches("1800", 1800.4231))   # 整数 round 合法
        self.assertFalse(self._matches("1795", 1800.4231))  # 编造

    def test_composed_json_number_int(self) -> None:
        self.assertTrue(self._matches(1800, 1800.4231))

    def test_composed_truncation_and_rounding(self) -> None:
        self.assertTrue(self._matches("3.4", 3.4512))
        self.assertTrue(self._matches("3.5", 3.4512))
        self.assertFalse(self._matches("3.3", 3.4512))

    def test_composed_invented_precision(self) -> None:
        self.assertFalse(self._matches("72.34", 72.3))

    def test_composed_crypto_tiny_price(self) -> None:
        self.assertTrue(self._matches("1.23e-5", 1.23e-5))
        self.assertFalse(self._matches("4.56e-5", 1.23e-5))
```

跑一次,确认全绿;再跑一次 Step 5 的变异,确认 `test_composed_integer_claim_against_fractional_fact` **变红**。

- [ ] **Step 6: 提交**

```bash
git add src/claim_validation.py tests/test_claim_validation.py
git commit -m "feat: claim-validation 数值原语(Decimal 求小数位数 + 指数段正则 + 自校准容差),含整数与科学计数法回归锁"
```

---

## Task 3: 结构判据 `validate_structure`

**Files:**
- Modify: `src/claim_validation.py`
- Test: `tests/test_claim_validation.py`

**Interfaces:**
- Consumes: `src.sniper_parsing.parse_sniper_value`
- Produces: `validate_structure(sniper_points: Any) -> Dict[str, Any]`

**签名只收 `sniper_points`。** 不传 `current_price`、不传 `result` —— 这让「照搬 `is_invalid_price_level` 的 `entry <= current_price`」这种误用**在类型上就无法表达**。LLM 的 `ideal_buy` 没有那个构造不变式(「突破 12.8 元买入」是合法计划),照搬会系统性误杀所有突破买入。签名即护栏。

- [ ] **Step 1: 追加失败测试**

在 `tests/test_claim_validation.py` 末尾(`if __name__` 之前)追加:

```python
from src.claim_validation import validate_structure  # noqa: E402


class TestValidateStructure(unittest.TestCase):
    def test_valid_long_plan(self) -> None:
        out = validate_structure(
            {"ideal_buy": 12.5, "stop_loss": 12.0, "take_profit": 13.5}
        )
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["violations"], [])

    def test_breakout_buy_above_current_price_is_valid(self) -> None:
        # 文档性护栏：真正的防线是签名拿不到 current_price。
        # 若有人给本函数加上 current_price 参数并引入 entry<=current 判据，本例会红。
        out = validate_structure(
            {"ideal_buy": 12.8, "stop_loss": 12.0, "take_profit": 13.5}
        )
        self.assertEqual(out["status"], "ok")

    def test_stop_above_entry_is_violation(self) -> None:
        out = validate_structure(
            {"ideal_buy": 12.5, "stop_loss": 13.0, "take_profit": 14.0}
        )
        self.assertEqual(out["status"], "violation")
        self.assertTrue(any("stop_loss" in v for v in out["violations"]))

    def test_target_below_entry_is_violation(self) -> None:
        out = validate_structure(
            {"ideal_buy": 12.5, "stop_loss": 12.0, "take_profit": 12.1}
        )
        self.assertEqual(out["status"], "violation")

    def test_secondary_buy_outside_band_is_violation(self) -> None:
        out = validate_structure(
            {
                "ideal_buy": 12.5,
                "secondary_buy": 14.0,
                "stop_loss": 12.0,
                "take_profit": 13.5,
            }
        )
        self.assertEqual(out["status"], "violation")

    def test_range_ideal_buy_uses_last_number(self) -> None:
        # 与落库口径一致：'180-182' → 182（不是 180）。
        # 夹具必须能分辨首/尾 —— 把边界卡在 181，两种取法结论相反。
        # 取尾(182)：181 < 182 < 190 → ok ；取首(180)：181 < 180 为假 → violation
        ok = validate_structure({"ideal_buy": "180-182", "stop_loss": 181.0, "take_profit": 190.0})
        self.assertEqual(ok["status"], "ok")
        # 取尾(182)：182 < 181 为假 → violation ；取首(180)：180 < 181 → ok
        bad = validate_structure({"ideal_buy": "180-182", "stop_loss": 175.0, "take_profit": 181.0})
        self.assertEqual(bad["status"], "violation")

    def test_non_positive_extracted_value_is_violation(self) -> None:
        out = validate_structure({"ideal_buy": "-5", "stop_loss": 1.0, "take_profit": 2.0})
        self.assertEqual(out["status"], "violation")
        self.assertTrue(any("ideal_buy" in v and "<= 0" in v for v in out["violations"]))

    def test_zero_extracted_value_is_violation(self) -> None:
        out = validate_structure({"ideal_buy": "0元", "stop_loss": 1.0, "take_profit": 2.0})
        self.assertEqual(out["status"], "violation")

    def test_non_finite_extracted_value_is_violation(self) -> None:
        out = validate_structure({"ideal_buy": 12.5, "stop_loss": 12.0, "take_profit": "inf"})
        self.assertEqual(out["status"], "violation")

    def test_violation_wins_over_insufficient_fields(self) -> None:
        # 只有一个非正字段，凑不出任何序关系，但仍是 violation
        out = validate_structure({"ideal_buy": "-5"})
        self.assertEqual(out["status"], "violation")

    def test_numeric_non_positive_is_absent_not_violation(self) -> None:
        # parse_sniper_value(-5) → None（上游即滤除，与落库 NULL 一致）
        out = validate_structure({"ideal_buy": -5, "stop_loss": 1.0, "take_profit": 2.0})
        self.assertEqual(out["status"], "ok")

    def test_insufficient_fields_is_not_applicable(self) -> None:
        out = validate_structure({"stop_loss": 12.0})
        self.assertEqual(out["status"], "not_applicable")
        self.assertEqual(out["reason"], "insufficient_fields")

    def test_malformed_input_is_not_applicable(self) -> None:
        for bad in (None, "", [], 42):
            out = validate_structure(bad)
            self.assertEqual(out["status"], "not_applicable", bad)
            self.assertEqual(out["reason"], "no_sniper_points")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
python -m pytest tests/test_claim_validation.py -k Structure -v
```

Expected: `ImportError: cannot import name 'validate_structure'`

- [ ] **Step 3: 实现**

`typing` 与 `parse_sniper_value` 的 import 已在 Task 2 一次到位,本 task 只在文件末尾追加:

```python
_SNIPER_FIELDS = ("ideal_buy", "secondary_buy", "stop_loss", "take_profit")


def validate_structure(sniper_points: Any) -> Dict[str, Any]:
    """校验 LLM 自主生成的买卖计划是否内部自洽。

    **签名只收 sniper_points**：拿不到 current_price 的函数不可能拿它做判据。
    `ideal_buy > current_price`（突破买入）是完全合法的计划，
    照搬 `is_invalid_price_level` 的 `entry <= current_price` 会系统性误杀。

    仅在相关字段都成功抽出数值时才判；缺失 → 跳过，不判违规。
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
```

- [ ] **Step 4: 跑测试确认通过**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
python -m pytest tests/test_claim_validation.py -v
```

Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/claim_validation.py tests/test_claim_validation.py
git commit -m "feat: claim-validation 结构判据 validate_structure(签名只收 sniper_points,使 entry<=current_price 误用在类型上不可表达)"
```

---

## Task 4: `collect_prompt_facts` + drift-lock

facts 是「我们实际喂给 LLM 的数」。**必须从渲染字符串反解,不得从原始值重算** —— `chip['profit_ratio'] * 100` 在 float 下是 `72.34000000000001`,而 prompt 里写的是 `f"{0.7234:.1%}"` = `"72.3%"`。

**Files:**
- Modify: `src/claim_validation.py`
- Test: `tests/test_claim_validation.py`

**Interfaces:**
- Produces: `collect_prompt_facts(context: Any) -> Dict[str, Any]`
  - `facts["current_price"]` 是 `List[float]`(**值集合**:`today['close']` 与 `realtime['price']` 在 prompt 里同时出现,LLM 引用任一个都合法)
  - 其余键是 `float`
  - 没渲染进 prompt 的字段**不出现在 facts 里**

- [ ] **Step 1: 追加失败测试**

在 `tests/test_claim_validation.py` 追加(import 段补 `from unittest.mock import patch`、`from types import SimpleNamespace`):

```python
from src.claim_validation import collect_prompt_facts  # noqa: E402


# 唯一哨兵值：消除「短字面量恒真」——若用 0 / 1 这类值，
# "0" 在 prompt 里到处都是，drift-lock 的子串断言会恒绿。
_SENTINEL_CONTEXT = {
    "code": "600519",
    "stock_name": "贵州茅台",
    "date": "2026-03-16",
    "today": {
        "close": 11111.1111,
        "ma5": 22222.2222,
        "ma10": 33333.3333,
        "ma20": 44444.4444,
        "pct_chg": 1.11,
        "volume": 123456,
        "amount": 987654321,
    },
    "realtime": {
        "price": 55555.5555,
        "volume_ratio": 66666.6666,
        "turnover_rate": 77777.7777,
    },
    "chip": {
        "profit_ratio": 0.888888,
        "avg_cost": 99999.9999,
        "concentration_90": 0.123456,
        "concentration_70": 0.234567,
    },
    "trend_analysis": {
        "bias_ma5": 8.7654,
        "bias_ma10": 1.2345,
        "trend_status": "多头",
        "signal_score": 60,
    },
}


class TestCollectPromptFacts(unittest.TestCase):
    def test_current_price_is_a_value_set(self) -> None:
        facts = collect_prompt_facts(_SENTINEL_CONTEXT)
        self.assertEqual(sorted(facts["current_price"]), [11111.1111, 55555.5555])

    def test_percent_fields_are_reverse_parsed_from_rendered_string(self) -> None:
        facts = collect_prompt_facts(_SENTINEL_CONTEXT)
        # prompt 写的是 f"{0.888888:.1%}" == "88.9%"，不是 88.8888
        self.assertEqual(facts["profit_ratio"], 88.9)
        # prompt 写的是 f"{8.7654:+.2f}%" == "+8.77%"
        self.assertEqual(facts["bias_ma5"], 8.77)

    def test_bare_fields_are_raw(self) -> None:
        facts = collect_prompt_facts(_SENTINEL_CONTEXT)
        self.assertEqual(facts["ma5"], 22222.2222)
        self.assertEqual(facts["avg_cost"], 99999.9999)
        self.assertEqual(facts["volume_ratio"], 66666.6666)

    def test_missing_blocks_yield_missing_facts(self) -> None:
        ctx = {"today": {"close": 10.0, "ma5": 11.0}}
        facts = collect_prompt_facts(ctx)
        self.assertEqual(facts["current_price"], [10.0])
        self.assertEqual(facts["ma5"], 11.0)
        for absent in ("volume_ratio", "turnover_rate", "profit_ratio", "avg_cost", "bias_ma5"):
            self.assertNotIn(absent, facts)

    def test_non_numeric_is_skipped(self) -> None:
        ctx = {"today": {"close": "N/A", "ma5": None}}
        self.assertEqual(collect_prompt_facts(ctx), {})


class TestPromptFactsDriftLock(unittest.TestCase):
    """facts 一旦对不上 prompt，守卫就会拿错的基准去判 LLM「幻觉」，
    反而制造假警报。这个测试是 collect_prompt_facts 与 _format_prompt
    之间唯一的防漂移保证。"""

    def _render(self, *, legacy: bool) -> str:
        from src.analyzer import GeminiAnalyzer

        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()
        analyzer._use_legacy_default_prompt_override = legacy
        fake_cfg = SimpleNamespace(news_max_age_days=30, news_strategy_profile="medium")
        with patch("src.analyzer.get_config", return_value=fake_cfg):
            return analyzer._format_prompt(dict(_SENTINEL_CONTEXT), "贵州茅台", news_context=None)

    def _assert_facts_in_prompt(self, prompt: str) -> None:
        facts = collect_prompt_facts(_SENTINEL_CONTEXT)
        for key, value in facts.items():
            candidates = value if isinstance(value, list) else [value]
            for number in candidates:
                self.assertIn(
                    str(number),
                    prompt,
                    f"fact {key}={number} 未出现在 prompt 里 —— collect_prompt_facts 与 _format_prompt 已漂移",
                )

    def test_drift_lock_legacy_branch(self) -> None:
        self._assert_facts_in_prompt(self._render(legacy=True))

    def test_drift_lock_non_legacy_branch(self) -> None:
        self._assert_facts_in_prompt(self._render(legacy=False))
```

- [ ] **Step 2: 跑测试确认失败**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
python -m pytest tests/test_claim_validation.py -k "Facts or Drift" -v
```

Expected: `ImportError: cannot import name 'collect_prompt_facts'`

- [ ] **Step 3: 实现**

在 `src/claim_validation.py` 末尾追加。**每一处取值与条件都必须镜像 `src/analyzer.py::_format_prompt`**:`today`(无条件)、`if 'realtime' in context`、`if 'chip' in context`、`if 'trend_analysis' in context`。

```python
def _as_finite_float(value: Any) -> Optional[float]:
    if _is_claim_absent(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rendered(fmt: str, value: Any) -> Optional[float]:
    """按 prompt 的格式化串渲染后再反解回 float。

    fact 必须与 prompt 里那个 token **按构造相等**：
    `0.7234 * 100` 是 72.34000000000001，而 prompt 写的是 "72.3%"。
    """
    number = _as_finite_float(value)
    if number is None:
        return None
    text = format(number, fmt).rstrip("%")
    try:
        return float(text)
    except ValueError:
        return None


def collect_prompt_facts(context: Any) -> Dict[str, Any]:
    """采集「本次实际渲染进 prompt 的数值」。

    这是 `_format_prompt` 的并列纯函数（不改 `_format_prompt`：它有约 30 个
    测试断言其字符串返回值，且 GeminiAnalyzer 是跨线程共享的单例，
    实例属性存 facts 会有竞态）。两者的漂移由 drift-lock 测试锁定。
    """
    if not isinstance(context, dict):
        return {}
    facts: Dict[str, Any] = {}

    today = context.get("today") if isinstance(context.get("today"), dict) else {}
    prices: List[float] = []
    close = _as_finite_float(today.get("close"))
    if close is not None:
        prices.append(close)
    for key in ("ma5", "ma10", "ma20"):
        number = _as_finite_float(today.get(key))
        if number is not None:
            facts[key] = number

    if isinstance(context.get("realtime"), dict):
        realtime = context["realtime"]
        price = _as_finite_float(realtime.get("price"))
        if price is not None:
            prices.append(price)
        ratio = _as_finite_float(realtime.get("volume_ratio"))
        if ratio is not None:
            facts["volume_ratio"] = ratio
        turnover = _as_finite_float(realtime.get("turnover_rate"))
        if turnover is not None:
            facts["turnover_rate"] = turnover

    if isinstance(context.get("chip"), dict):
        chip = context["chip"]
        profit = _rendered(".1%", chip.get("profit_ratio", 0))
        if profit is not None:
            facts["profit_ratio"] = profit
        avg_cost = _as_finite_float(chip.get("avg_cost"))
        if avg_cost is not None:
            facts["avg_cost"] = avg_cost

    if isinstance(context.get("trend_analysis"), dict):
        # 直接读未 sanitize 的 trend：_sanitize_trend_analysis_for_prompt 是纯函数，
        # 只改写 signal_reasons / risk_factors / prompt_*，bias_ma5 原样拷贝。
        # 若将来它开始改写 bias_ma5，drift-lock 测试会立刻变红。
        bias = _rendered("+.2f", context["trend_analysis"].get("bias_ma5", 0))
        if bias is not None:
            facts["bias_ma5"] = bias

    if prices:
        facts["current_price"] = prices
    return facts
```

- [ ] **Step 4: 跑测试确认通过**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
python -m pytest tests/test_claim_validation.py -v
```

Expected: 全部 PASS。若 drift-lock 红了,**先看 prompt 实际渲染成什么**,再决定改 `collect_prompt_facts`(几乎总是这一侧错)。**不要为了让测试变绿而放宽断言。**

若 `_format_prompt` 因 `fake_cfg` 缺属性抛 `AttributeError`,按报错逐个补 `SimpleNamespace` 字段。**绝不能改 `_format_prompt`。**

- [ ] **Step 5: 提交**

```bash
git add src/claim_validation.py tests/test_claim_validation.py
git commit -m "feat: collect_prompt_facts 并列纯函数(从渲染字符串反解,不重算)+ 唯一哨兵值 drift-lock 锁定与 _format_prompt 的一致性"
```

---

## Task 5: 配置开关 + `AnalysisResult.prompt_facts` + `analyze()` 门控

**Files:**
- Modify: `src/config.py:917`(字段)、`src/config.py:1957`(env 解析)
- Modify: `src/analyzer.py`(`AnalysisResult` 尾部字段 ~L1780、`analyze()` 内 `_format_prompt` 调用处 ~L2985)
- Test: `tests/test_claim_validation.py`

**Interfaces:**
- Produces: `Config.llm_claim_validation_enabled: bool`;`AnalysisResult.prompt_facts: Optional[Dict[str, Any]]`

**门控读在 `analyze()` 内。** 契约,不可含糊 —— 否则「关闭时 `collect_prompt_facts` 未被调用」无法断言。

- [ ] **Step 1: 追加失败测试**

在 `tests/test_claim_validation.py` 追加:

```python
class TestAnalyzeGate(unittest.TestCase):
    def _analyzer(self):
        from src.analyzer import GeminiAnalyzer

        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            return GeminiAnalyzer()

    def test_prompt_facts_field_defaults_to_none(self) -> None:
        from src.analyzer import AnalysisResult

        result = AnalysisResult(
            code="600519", name="贵州茅台", sentiment_score=60,
            trend_prediction="震荡", operation_advice="持有",
        )
        self.assertIsNone(result.prompt_facts)

    def test_to_dict_does_not_leak_prompt_facts(self) -> None:
        from src.analyzer import AnalysisResult

        result = AnalysisResult(
            code="600519", name="贵州茅台", sentiment_score=60,
            trend_prediction="震荡", operation_advice="持有",
        )
        result.prompt_facts = {"ma5": 1.0}
        self.assertNotIn("prompt_facts", result.to_dict())

    def test_config_default_is_false(self) -> None:
        from src.config import Config

        self.assertIs(Config.llm_claim_validation_enabled, False)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
python -m pytest tests/test_claim_validation.py -k Gate -v
```

Expected: `AttributeError: 'AnalysisResult' object has no attribute 'prompt_facts'`

- [ ] **Step 3: `src/config.py` 加字段**

在 `intraday_backtest_enabled: bool = False`(L917)**之后**插入:

```python
    # LLM claim-validation 守卫(Inc 3):把 LLM 陈述的数值与 prompt 喂给它的数值交叉核对
    # 默认关闭;开启后不一致会标注并封顶置信度,绝不覆盖数值、不改决策方向
    llm_claim_validation_enabled: bool = False
```

在 `intraday_backtest_enabled=parse_env_bool(...)`(L1957-1959)**之后**插入:

```python
            llm_claim_validation_enabled=parse_env_bool(
                os.getenv('LLM_CLAIM_VALIDATION_ENABLED'), False,
            ),
```

- [ ] **Step 4: `src/analyzer.py` 加 dataclass 字段**

在 `AnalysisResult` 的 `fundamental_context: Optional[Dict[str, Any]] = None`(~L1780)**之后**、`def to_dict` **之前**插入:

```python
    # ========== claim-validation 内部快照（Inc 3；不进 to_dict / 不落库 / 不进报告）==========
    prompt_facts: Optional[Dict[str, Any]] = None  # 本次实际渲染进 prompt 的数值快照
```

**不要动 `to_dict()`** —— 它是显式枚举,新字段天然不会泄漏进 `raw_result`。

- [ ] **Step 5: `analyze()` 内门控采集 facts**

在 `src/analyzer.py` import 段加:

```python
from src.claim_validation import collect_prompt_facts
```

把 `analyze()` 里的(~L2985):

```python
            prompt = self._format_prompt(
                context,
                name,
                news_context,
                report_language=report_language,
                analysis_context_pack_summary=analysis_context_pack_summary,
            )
```

改为(**`_format_prompt` 本身零改动**):

```python
            prompt = self._format_prompt(
                context,
                name,
                news_context,
                report_language=report_language,
                analysis_context_pack_summary=analysis_context_pack_summary,
            )
            # Inc 3: 与 _format_prompt 消费同一个 context 的并列纯函数。
            # 关闭时一次也不调用 → 字节级不变。
            _claim_facts = (
                collect_prompt_facts(context)
                if getattr(self._get_runtime_config(), "llm_claim_validation_enabled", False)
                else None
            )
```

然后在 `analyze()` 里 `result` 构造完成之后(`_parse_response` 返回之后、`return result` 之前)加一行:

```python
            if result is not None:
                result.prompt_facts = _claim_facts
```

> 实现者注意:`analyze()` 有完整性重试循环,`result` 在循环内被赋值。把这一行放在**循环结束后、`return result` 之前**,确保只赋一次且拿到最终 result。

- [ ] **Step 6: 跑测试确认通过**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
python -m pytest tests/test_claim_validation.py tests/test_analyzer_news_prompt.py tests/test_crypto_derivatives_prompt.py -v
```

Expected: 全部 PASS。**`test_analyzer_news_prompt.py` / `test_crypto_derivatives_prompt.py` 变红 = 你改了 `_format_prompt` 的签名,必须回退。**

- [ ] **Step 7: 提交**

```bash
git add src/config.py src/analyzer.py tests/test_claim_validation.py
git commit -m "feat: 新增 LLM_CLAIM_VALIDATION_ENABLED 开关与 AnalysisResult.prompt_facts 内部快照(门控读在 analyze() 内,关闭时 collect_prompt_facts 不被调用)"
```

---

## Task 6: `extract_llm_claims` + `apply_claim_validation`

**Files:**
- Modify: `src/claim_validation.py`
- Test: `tests/test_claim_validation.py`

**Interfaces:**
- Consumes: `extract_numeric_claim` / `claim_matches_fact` / `_is_claim_absent` / `validate_structure`
- Produces:
  - `extract_llm_claims(result: Any) -> Optional[Dict[str, Any]]` —— 纯读快照,`{"transcription": {...9 keys...}, "sniper_points": <raw>}`;`dashboard` 非 dict → `None`
  - `apply_claim_validation(result, claims, facts, *, language: str) -> List[str]` —— 返回 action codes

- [ ] **Step 1: 追加失败测试**

```python
from src.analyzer import AnalysisResult  # noqa: E402
from src.claim_validation import apply_claim_validation, extract_llm_claims  # noqa: E402


def _result_with_dashboard(dashboard, *, confidence="高"):
    result = AnalysisResult(
        code="600519", name="贵州茅台", sentiment_score=75,
        trend_prediction="看多", operation_advice="买入",
        decision_type="buy", confidence_level=confidence,
    )
    result.dashboard = dashboard
    return result


_GOOD_DASHBOARD = {
    "data_perspective": {
        "price_position": {"current_price": 11111.1111, "ma5": 22222.2222},
        "volume_analysis": {"volume_ratio": 66666.6666},
        "chip_structure": {"profit_ratio": "88.9%"},
    },
    "battle_plan": {
        "sniper_points": {"ideal_buy": 12.5, "stop_loss": 12.0, "take_profit": 13.5}
    },
}

_FACTS = {"current_price": [11111.1111], "ma5": 22222.2222,
          "volume_ratio": 66666.6666, "profit_ratio": 88.9}


class TestExtractLlmClaims(unittest.TestCase):
    def test_snapshot_reads_all_three_blocks(self) -> None:
        claims = extract_llm_claims(_result_with_dashboard(dict(_GOOD_DASHBOARD)))
        self.assertEqual(claims["transcription"]["ma5"], 22222.2222)
        self.assertEqual(claims["transcription"]["profit_ratio"], "88.9%")
        self.assertEqual(claims["sniper_points"]["stop_loss"], 12.0)

    def test_non_dict_dashboard_returns_none(self) -> None:
        for bad in (None, "", [], 42):
            self.assertIsNone(extract_llm_claims(_result_with_dashboard(bad)), bad)

    def test_malformed_result_returns_none(self) -> None:
        self.assertIsNone(extract_llm_claims(SimpleNamespace()))


class TestApplyClaimValidation(unittest.TestCase):
    def test_all_ok_writes_key_without_capping(self) -> None:
        result = _result_with_dashboard(dict(_GOOD_DASHBOARD))
        claims = extract_llm_claims(result)
        actions = apply_claim_validation(result, claims, _FACTS, language="zh")
        cv = result.dashboard["claim_validation"]
        self.assertTrue(cv["applied"])
        self.assertEqual(cv["transcription"]["status"], "ok")
        self.assertEqual(cv["transcription"]["checked"], 4)
        self.assertEqual(cv["structural"]["status"], "ok")
        self.assertEqual(actions, [])
        self.assertEqual(result.confidence_level, "高")

    def test_transcription_mismatch_caps_confidence(self) -> None:
        dashboard = {
            "data_perspective": {"price_position": {"ma5": 99999.9}},
            "battle_plan": {},
        }
        result = _result_with_dashboard(dashboard)
        claims = extract_llm_claims(result)
        actions = apply_claim_validation(result, claims, {"ma5": 22222.2222}, language="zh")
        self.assertIn("confidence_capped_claim_mismatch", actions)
        self.assertEqual(result.confidence_level, "中")
        cv = result.dashboard["claim_validation"]
        self.assertEqual(cv["transcription"]["status"], "mismatch")
        self.assertEqual(cv["transcription"]["mismatches"][0]["field"], "price_position.ma5")

    def test_cap_is_monotone_no_op_when_already_medium(self) -> None:
        dashboard = {"data_perspective": {"price_position": {"ma5": 99999.9}}, "battle_plan": {}}
        result = _result_with_dashboard(dashboard, confidence="低")
        claims = extract_llm_claims(result)
        apply_claim_validation(result, claims, {"ma5": 22222.2222}, language="zh")
        self.assertEqual(result.confidence_level, "低")

    def test_english_cap_writes_localized_text(self) -> None:
        dashboard = {"data_perspective": {"price_position": {"ma5": 99999.9}}, "battle_plan": {}}
        result = _result_with_dashboard(dashboard, confidence="High")
        claims = extract_llm_claims(result)
        apply_claim_validation(result, claims, {"ma5": 22222.2222}, language="en")
        self.assertEqual(result.confidence_level, "Medium")

    def test_structural_violation_marks_unexecutable_without_clearing(self) -> None:
        dashboard = {
            "data_perspective": {},
            "battle_plan": {"sniper_points": {"ideal_buy": 12.5, "stop_loss": 13.0, "take_profit": 14.0}},
        }
        result = _result_with_dashboard(dashboard)
        claims = extract_llm_claims(result)
        actions = apply_claim_validation(result, claims, {}, language="zh")
        self.assertIn("sniper_points_unexecutable", actions)
        self.assertEqual(result.confidence_level, "高")  # 结构违规不封顶置信度
        # 契约：不清空字段，storage._extract_sniper_points 仍要读它
        self.assertEqual(dashboard["battle_plan"]["sniper_points"]["stop_loss"], 13.0)

    def test_no_facts_means_transcription_not_applicable(self) -> None:
        """agent 路径：不经 _format_prompt，永远没有 prompt_facts。"""
        result = _result_with_dashboard(dict(_GOOD_DASHBOARD))
        claims = extract_llm_claims(result)
        apply_claim_validation(result, claims, None, language="zh")
        cv = result.dashboard["claim_validation"]
        self.assertEqual(cv["transcription"]["status"], "not_applicable")
        self.assertEqual(cv["transcription"]["reason"], "no_prompt_facts")
        self.assertEqual(cv["structural"]["status"], "ok")  # 结构类照跑

    def test_numeric_zero_claim_is_validated(self) -> None:
        dashboard = {"data_perspective": {"price_position": {"bias_ma5": 0}}, "battle_plan": {}}
        result = _result_with_dashboard(dashboard)
        claims = extract_llm_claims(result)
        apply_claim_validation(result, claims, {"bias_ma5": 0.0}, language="zh")
        cv = result.dashboard["claim_validation"]
        self.assertEqual(cv["transcription"]["checked"], 1)
        self.assertEqual(cv["transcription"]["status"], "ok")

    def test_guard_never_raises(self) -> None:
        """一个防幻觉守卫自己把主流程搞崩，是最坏的结果。"""
        result = _result_with_dashboard(dict(_GOOD_DASHBOARD))
        claims = extract_llm_claims(result)
        with patch("src.claim_validation.extract_numeric_claim", side_effect=RuntimeError("boom")):
            actions = apply_claim_validation(result, claims, _FACTS, language="zh")
        self.assertEqual(actions, [])
        self.assertNotIn("claim_validation", result.dashboard)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
python -m pytest tests/test_claim_validation.py -k "ExtractLlm or ApplyClaim" -v
```

Expected: `ImportError: cannot import name 'extract_llm_claims'`

- [ ] **Step 3: 实现**

在 `src/claim_validation.py` import 段加:

```python
from src.phase_decision_guardrail import _is_high_confidence
```

在文件末尾追加:

```python
# canonical fact key → dashboard 内的点分路径（用于 mismatch 报告与快照）
_CLAIM_PATHS: Dict[str, Tuple[str, str]] = {
    "current_price": ("price_position", "current_price"),
    "ma5": ("price_position", "ma5"),
    "ma10": ("price_position", "ma10"),
    "ma20": ("price_position", "ma20"),
    "bias_ma5": ("price_position", "bias_ma5"),
    "volume_ratio": ("volume_analysis", "volume_ratio"),
    "turnover_rate": ("volume_analysis", "turnover_rate"),
    "profit_ratio": ("chip_structure", "profit_ratio"),
    "avg_cost": ("chip_structure", "avg_cost"),
}

_MISSING = object()


def extract_llm_claims(result: Any) -> Optional[Dict[str, Any]]:
    """纯读快照：LLM 原始输出里的可校验 claim。

    **必须在 pipeline 的任何 in-place 回填之前调用。**
    `normalize_chip_structure_availability` 会用 chip_data 回填 chip_structure，
    `fill_price_position_if_needed` 会用 trend_result 的重算值回填 price_position。
    挂在它们之后，守卫就是在拿系统自己的值当 LLM 的 claim。

    dashboard 非 dict → 返回 None（调用方据此整体跳过，不写键、不抛错）。
    """
    try:
        dashboard = getattr(result, "dashboard", None)
        if not isinstance(dashboard, dict):
            return None
        perspective = dashboard.get("data_perspective")
        perspective = perspective if isinstance(perspective, dict) else {}
        transcription: Dict[str, Any] = {}
        for key, (block_name, field) in _CLAIM_PATHS.items():
            block = perspective.get(block_name)
            if isinstance(block, dict) and field in block:
                transcription[key] = block[field]
        battle_plan = dashboard.get("battle_plan")
        battle_plan = battle_plan if isinstance(battle_plan, dict) else {}
        return {"transcription": transcription, "sniper_points": battle_plan.get("sniper_points")}
    except Exception as exc:  # noqa: BLE001 - 纯读也不得抛错
        logger.warning("[claim_validation] extract_llm_claims failed, skipping: %s", exc)
        return None


def _validate_transcription(claims: Dict[str, Any], facts: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not facts:
        return {"status": "not_applicable", "reason": "no_prompt_facts", "checked": 0, "mismatches": []}

    checked = 0
    mismatches: List[Dict[str, Any]] = []
    for key in FACT_KEYS:
        raw = claims.get(key, _MISSING)
        if raw is _MISSING or _is_claim_absent(raw):
            continue
        fact = facts.get(key)
        if fact is None:
            continue
        parsed = extract_numeric_claim(raw)
        if parsed is None:
            continue
        claimed, decimals = parsed
        candidates = fact if isinstance(fact, (list, tuple)) else [fact]
        checked += 1
        if any(claim_matches_fact(claimed, decimals, candidate) for candidate in candidates):
            continue
        block_name, field = _CLAIM_PATHS[key]
        mismatches.append(
            {
                "field": f"{block_name}.{field}",
                "claimed": claimed,
                "fact": list(candidates) if len(candidates) > 1 else candidates[0],
                "tolerance": 10.0 ** (-decimals),
            }
        )

    if checked == 0:
        return {"status": "not_applicable", "reason": "no_comparable_claims", "checked": 0, "mismatches": []}
    if mismatches:
        return {"status": "mismatch", "reason": None, "checked": checked, "mismatches": mismatches}
    return {"status": "ok", "reason": None, "checked": checked, "mismatches": []}


def apply_claim_validation(
    result: Any,
    claims: Optional[Dict[str, Any]],
    facts: Optional[Dict[str, Any]],
    *,
    language: str,
) -> List[str]:
    """判定 + 写 dashboard['claim_validation'] + 单调封顶置信度。

    **必须在 apply_phase_decision_guardrails 之后调用。** 该守卫在入口一次性算
    `initially_high_confidence`，随后两个降级分支（高→中、高→低）都消费它。
    若 claim-validation 先把「高」降到「中」，那两个分支会全部静默失效 ——
    包括更严厉的高→低 安全降级。结果是开启防幻觉守卫反而让阶段护栏变得不保守。

    cap 是单调的：仅当仍为「高」时降到「中」，否则 no-op，永不回撤 guardrail 的降级。
    """
    if claims is None:
        return []
    actions: List[str] = []
    try:
        dashboard = getattr(result, "dashboard", None)
        if not isinstance(dashboard, dict):
            return []

        transcription = _validate_transcription(claims.get("transcription") or {}, facts)
        structural = validate_structure(claims.get("sniper_points"))

        # action code 与**真实的 cap 动作**绑定：置信度已是中/低时 cap 是 no-op，
        # 不能报 "capped"。与 phase_decision_guardrail 的
        # confidence_capped_core_data_degraded 门控方式一致。
        # 「mismatch 发生过」这个信息在 transcription.status 里，不会丢。
        if transcription["status"] == "mismatch" and is_high_confidence(
            getattr(result, "confidence_level", "")
        ):
            result.confidence_level = "Medium" if language == "en" else "中"
            actions.append("confidence_capped_claim_mismatch")
        if structural["status"] == "violation":
            actions.append("sniper_points_unexecutable")

        dashboard["claim_validation"] = {
            "applied": True,
            "transcription": transcription,
            "structural": structural,
            "actions": list(actions),
        }
        return actions
    except Exception as exc:  # noqa: BLE001 - 守卫绝不阻塞报告产出
        logger.warning("[claim_validation] apply_claim_validation failed, skipping: %s", exc)
        return []
```

- [ ] **Step 4: 跑测试确认通过**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
python -m pytest tests/test_claim_validation.py -v
```

Expected: 全部 PASS。

- [ ] **Step 5: 变异验证(必做,不写进代码)**

删掉 `apply_claim_validation` 的 `try/except`,重跑 `test_guard_never_raises`。

Expected: **变红**。确认后改回来。

- [ ] **Step 6: 提交**

```bash
git add src/claim_validation.py tests/test_claim_validation.py
git commit -m "feat: extract_llm_claims 纯读快照 + apply_claim_validation 判定与单调降权(异常安全,守卫绝不阻塞报告产出)"
```

---

## Task 7: pipeline 接线(4 个插入点)+ 顺序集成测试

**这是全计划风险最高的一步。** 两条分支的第一个 in-place 回填**都是** `normalize_chip_structure_availability`,但**守卫条件不同**:

| 分支 | 现状代码 | Step A 插入位置 |
| --- | --- | --- |
| 非-agent | `# Step 7.6: chip_structure fallback ...`<br>`if result:`<br>`    normalize_chip_structure_availability(result, chip_data)` | **紧贴 `# Step 7.6` 注释之前** |
| agent | `# chip_structure fallback (Issue #589), before save_analysis_history`<br>`if result and chip_data is not None:`<br>`    normalize_chip_structure_availability(result, chip_data)` | **紧贴该注释之前** |

**不要照抄插入代码。** 用你自己的 `if result and getattr(self.config, ...)` 块。

**Files:**
- Modify: `src/core/pipeline.py`(非-agent ~L623 与 ~L651;agent ~L1148 与 ~L1182)
- Test: `tests/test_claim_validation_pipeline.py`(新)

**Interfaces:**
- Consumes: `extract_llm_claims` / `apply_claim_validation`

- [ ] **Step 1: 先 grep 确认锚点(不要凭行号)**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
grep -n "normalize_chip_structure_availability\|apply_phase_decision_guardrails\|Applied adjustments\|Applied agent adjustments" src/core/pipeline.py
```

Expected: 6 行 —— 两处 `normalize_...`、两处 `apply_phase_decision_guardrails`、两处 logger。记下实际行号。

- [ ] **Step 2: 写失败的集成测试**

创建 `tests/test_claim_validation_pipeline.py`。**复用 `tests/test_pipeline_market_phase_context.py` 的 `_make_pipeline` 手法** —— 它用 `StockAnalysisPipeline.__new__` 绕过 `__init__`,把 `pipeline.analyzer` 换成 MagicMock 并令 `analyze.return_value` 为真实 `AnalysisResult`,从而能 stub 掉 LLM 把 `analyze_stock` 跑穿到 Step 7.7。

```python
# -*- coding: utf-8 -*-
"""pipeline 级集成测试：锁住 claim-validation 的挂载顺序(Inc 3)。

§3.1 与 §3.3 的顺序约束**只存在于 pipeline.py 的插入位置**。
在 guardrail 层手工按正确顺序调两个函数的测试是 tautology ——
无论有人把 pipeline.py 里的调用挪到哪儿，它都恒绿。
因此这些测试必须驱动真实 pipeline。
"""

import os
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from src.analyzer import AnalysisResult
from src.core.pipeline import StockAnalysisPipeline
from src.enums import ReportType


def _dashboard(*, ma5, sniper=None):
    return {
        "data_perspective": {"price_position": {"ma5": ma5}},
        "battle_plan": {"sniper_points": sniper or {}},
    }


def _analysis_result(*, confidence="高", ma5=22222.2222, prompt_facts=None, sniper=None) -> AnalysisResult:
    result = AnalysisResult(
        code="600519", name="贵州茅台", sentiment_score=80,
        trend_prediction="看多", operation_advice="立即买入",
        decision_type="buy", confidence_level=confidence,
    )
    result.dashboard = _dashboard(ma5=ma5, sniper=sniper)
    result.prompt_facts = prompt_facts
    return result


def _make_pipeline(
    *, agent_mode: bool, analysis_result: AnalysisResult, chip_data=None, enable_chip: bool = False
) -> StockAnalysisPipeline:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.config = SimpleNamespace(
        enable_realtime_quote=False,
        enable_chip_distribution=enable_chip,
        realtime_source_priority=[],
        agent_mode=agent_mode,
        agent_skills=[],
        save_context_snapshot=False,
        report_language="zh",
        report_integrity_enabled=False,
        fundamental_stage_timeout_seconds=1,
        llm_claim_validation_enabled=True,
    )
    pipeline.source_message = None
    pipeline.query_id = None
    pipeline.query_source = "system"
    pipeline.save_context_snapshot = False
    pipeline.progress_callback = None
    pipeline.analysis_skills = None
    pipeline.analysis_phase = "auto"
    pipeline.social_sentiment_service = None

    pipeline.fetcher_manager = MagicMock()
    pipeline.fetcher_manager.get_stock_name.return_value = "贵州茅台"
    pipeline.fetcher_manager.get_realtime_quote.return_value = None
    pipeline.fetcher_manager.get_chip_distribution.return_value = chip_data
    pipeline.fetcher_manager.get_fundamental_context.return_value = {
        "market": "cn", "coverage": {"boards": "not_supported"}, "source_chain": [],
    }
    pipeline.fetcher_manager.build_failed_fundamental_context.return_value = {
        "market": "cn", "coverage": {"boards": "not_supported"}, "source_chain": [],
    }

    pipeline.db = MagicMock()
    pipeline.db.get_data_range.return_value = []
    pipeline.db.get_analysis_context.return_value = {
        "code": "600519", "stock_name": "贵州茅台", "date": "2026-03-26", "today": {}, "yesterday": {},
    }

    pipeline.trend_analyzer = MagicMock()
    pipeline.analyzer = MagicMock()
    pipeline.analyzer.analyze.return_value = analysis_result
    pipeline.search_service = MagicMock()
    pipeline.search_service.is_available = False
    pipeline.search_service.news_window_days = 3
    pipeline._emit_progress = MagicMock()
    return pipeline


class TestClaimValidationOrder(unittest.TestCase):
    def _run(self, pipeline) -> AnalysisResult:
        return pipeline.analyze_stock(
            "600519", ReportType.SIMPLE, "q-claim", current_time=datetime(2026, 3, 27, 10, 0)
        )

    def test_b_runs_after_phase_guardrail(self) -> None:
        """§3.1 Blocker 回归锁。

        若 apply_claim_validation 被挪到 apply_phase_decision_guardrails 之前，
        它会先把「高」降成「中」，guardrail 的 initially_high_confidence 变 False，
        高→低 分支静默失效，最终 confidence 会是「中」而非「低」。
        """
        stub = _analysis_result(confidence="高", ma5=99999.9, prompt_facts={"ma5": 22222.2222})
        pipeline = _make_pipeline(agent_mode=False, analysis_result=stub)
        result = self._run(pipeline)
        cv = result.dashboard["claim_validation"]
        self.assertEqual(cv["transcription"]["status"], "mismatch")
        # guardrail 的高→低 仍然生效（保守盘口阶段 + 立即买入 + 原本高置信）
        self.assertEqual(result.confidence_level, "低")

    def test_a_runs_before_any_backfill_price_position(self) -> None:
        """§3.1 假警报侧回归锁。

        ma5 是占位符 → 若 A 挪到 fill_price_position_if_needed 之后，
        系统会用 trend_result 的重算值回填，与 prompt fact 不符 → 假 mismatch。
        """
        stub = _analysis_result(confidence="中", ma5="N/A", prompt_facts={"ma5": 22222.2222})
        pipeline = _make_pipeline(agent_mode=False, analysis_result=stub)
        pipeline.trend_analyzer.analyze.return_value = SimpleNamespace(ma5=11.11, to_dict=lambda: {})
        result = self._run(pipeline)
        cv = result.dashboard["claim_validation"]
        self.assertEqual(cv["transcription"]["mismatches"], [])

    def test_a_runs_before_chip_backfill(self) -> None:
        """§3.1 tautology 侧回归锁（D18）。

        profit_ratio 是占位符 + chip_data 有效 → 若 A 挪到
        normalize_chip_structure_availability 之后，系统回填的 "72.3%"
        会被当成 LLM claim 拿去和同源 fact 自比 → checked 计入。
        """
        stub = _analysis_result(confidence="中", prompt_facts={"profit_ratio": 72.3})
        stub.dashboard["data_perspective"]["chip_structure"] = {"profit_ratio": "N/A"}
        chip = SimpleNamespace(profit_ratio=0.7234, avg_cost=12.0, concentration_90=0.1234)
        # enable_chip=True 必须传 —— 否则 pipeline 压根不抓 chip_data，
        # normalize_chip_structure_availability 不回填，测试 trivially 通过。
        pipeline = _make_pipeline(
            agent_mode=False, analysis_result=stub, chip_data=chip, enable_chip=True
        )
        result = self._run(pipeline)
        cv = result.dashboard["claim_validation"]
        self.assertEqual(cv["transcription"]["checked"], 0)


class TestBothBranchesWiredInOrder(unittest.TestCase):
    """源码级顺序锁 —— 覆盖 agent 分支。

    agent 分支要端到端驱动需 mock agent executor，脆且易漂移。
    但顺序不变式本身是**静态**的：两条分支都必须满足
    extract_llm_claims 早于第一个 in-place 回填、
    apply_claim_validation 晚于 apply_phase_decision_guardrails。
    直接对源码断言，确定性且覆盖两条路径。
    """

    @staticmethod
    def _source() -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / "src" / "core" / "pipeline.py").read_text(
            encoding="utf-8"
        )

    @staticmethod
    def _positions(source: str, needle: str) -> list:
        start, out = 0, []
        while True:
            idx = source.find(needle, start)
            if idx == -1:
                return out
            out.append(idx)
            start = idx + 1

    def test_two_branches_each_have_both_hooks(self) -> None:
        source = self._source()
        self.assertEqual(len(self._positions(source, "extract_llm_claims(result)")), 2)
        self.assertEqual(len(self._positions(source, "apply_claim_validation(")), 2)
        self.assertEqual(len(self._positions(source, "normalize_chip_structure_availability(")), 2)
        self.assertEqual(len(self._positions(source, "apply_phase_decision_guardrails(")), 2)

    def test_extract_precedes_first_backfill_in_both_branches(self) -> None:
        source = self._source()
        extracts = self._positions(source, "extract_llm_claims(result)")
        chips = self._positions(source, "normalize_chip_structure_availability(")
        for branch, (extract_at, chip_at) in enumerate(zip(extracts, chips)):
            self.assertLess(
                extract_at, chip_at,
                f"分支 {branch}: extract_llm_claims 必须在 normalize_chip_structure_availability 之前",
            )

    def test_apply_follows_phase_guardrail_in_both_branches(self) -> None:
        source = self._source()
        applies = self._positions(source, "apply_claim_validation(")
        guards = self._positions(source, "apply_phase_decision_guardrails(")
        for branch, (apply_at, guard_at) in enumerate(zip(applies, guards)):
            self.assertGreater(
                apply_at, guard_at,
                f"分支 {branch}: apply_claim_validation 必须在 apply_phase_decision_guardrails 之后",
            )

    def test_gate_off_writes_nothing(self) -> None:
        stub = _analysis_result(confidence="高", ma5=99999.9, prompt_facts={"ma5": 22222.2222})
        pipeline = _make_pipeline(agent_mode=False, analysis_result=stub)
        pipeline.config.llm_claim_validation_enabled = False
        result = self._run(pipeline)
        self.assertNotIn("claim_validation", result.dashboard)

    def test_structural_violation_flows_through(self) -> None:
        stub = _analysis_result(
            confidence="中",
            prompt_facts={},
            sniper={"ideal_buy": 12.5, "stop_loss": 13.0, "take_profit": 14.0},
        )
        pipeline = _make_pipeline(agent_mode=False, analysis_result=stub)
        result = self._run(pipeline)
        cv = result.dashboard["claim_validation"]
        self.assertEqual(cv["structural"]["status"], "violation")
        self.assertIn("sniper_points_unexecutable", cv["actions"])


if __name__ == "__main__":
    unittest.main()
```

> **实现者注意**:`test_b_runs_after_phase_guardrail` 依赖 `apply_phase_decision_guardrails` 的「保守盘口阶段 + 立即买卖信号 + 原本高置信 → 高→低」分支被触发。若断言拿到的是「中」而不是「低」,先用 `pytest -s` 打印 `result.dashboard["phase_decision"]` 确认 `confidence_capped_non_intraday_action` 是否在 `adjustments` 里;必要时调整 stub 的 `operation_advice` 与 `current_time`(用非交易时段),**不要为了让它绿而删掉断言**。

- [ ] **Step 3: 跑测试确认失败**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
python -m pytest tests/test_claim_validation_pipeline.py -v
```

Expected: `KeyError: 'claim_validation'`

- [ ] **Step 4: 非-agent 分支接线**

`src/core/pipeline.py` import 段加:

```python
from src.claim_validation import apply_claim_validation, extract_llm_claims
```

在 `# Step 7.6: chip_structure fallback (Issue #589) and unavailable collapse` **之前**插入:

```python
            # Step 7.5b: claim-validation 快照(Inc 3)
            # 必须在任何 in-place 回填之前 —— 之后 dashboard 里的值可能是系统填的，不是 LLM 的 claim。
            claim_snapshot = None
            if result and getattr(self.config, "llm_claim_validation_enabled", False):
                claim_snapshot = extract_llm_claims(result)
```

在 `if adjustments:` / `logger.info("[phase_decision_guardrail] Applied adjustments for %s: %s", code, adjustments)` **之后**、`if isinstance(fundamental_context, dict):` **之前**插入:

```python
                # claim-validation 判定与降权(Inc 3)
                # 必须在 apply_phase_decision_guardrails 之后 —— 否则会抑制它的高→低 安全降级。
                if claim_snapshot is not None:
                    claim_actions = apply_claim_validation(
                        result,
                        claim_snapshot,
                        getattr(result, "prompt_facts", None),
                        language=getattr(result, "report_language", None)
                        or getattr(self.config, "report_language", "zh"),
                    )
                    if claim_actions:
                        logger.info("[claim_validation] Applied actions for %s: %s", code, claim_actions)
```

- [ ] **Step 5: agent 分支接线**

在 `# chip_structure fallback (Issue #589), before save_analysis_history` **之前**插入:

```python
            # claim-validation 快照(Inc 3)：必须在任何 in-place 回填之前。
            # agent 路径不经 _format_prompt，result.prompt_facts 恒为 None
            # → 转录类 not_applicable，结构类照跑。
            claim_snapshot = None
            if result and getattr(self.config, "llm_claim_validation_enabled", False):
                claim_snapshot = extract_llm_claims(result)
```

在 `logger.info("[phase_decision_guardrail] Applied agent adjustments for %s: %s", code, adjustments)` **之后**、`if isinstance(fundamental_context, dict):` **之前**插入(与非-agent **同一段代码**):

```python
                # claim-validation 判定与降权(Inc 3)：必须在 phase guardrail 之后。
                if claim_snapshot is not None:
                    claim_actions = apply_claim_validation(
                        result,
                        claim_snapshot,
                        getattr(result, "prompt_facts", None),
                        language=getattr(result, "report_language", None)
                        or getattr(self.config, "report_language", "zh"),
                    )
                    if claim_actions:
                        logger.info("[claim_validation] Applied agent actions for %s: %s", code, claim_actions)
```

- [ ] **Step 6: 跑测试确认通过 + 既有 pipeline 测试仍绿**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
python -m pytest tests/test_claim_validation_pipeline.py tests/test_pipeline_market_phase_context.py tests/test_agent_pipeline.py -v
```

Expected: 全部 PASS。

- [ ] **Step 7: 变异验证(必做,是本 task 唯一的安全网)**

这些集成测试**存在 tautology 风险**:若 `pipeline.db.get_data_range` 返回 `[]` 导致 `trend_result` 为 `None`,`fill_price_position_if_needed` 什么都不填,那么即便把 A 挪到 fill 之后,`test_a_runs_before_any_backfill_price_position` 仍会绿。**变异验证是唯一能证伪它的手段。**

变异 1 —— 把非-agent 的 Step A 块临时挪到 `# Step 7.7` **之后**,重跑:

Expected: `test_a_runs_before_any_backfill_price_position`、`test_a_runs_before_chip_backfill`、`test_extract_precedes_first_backfill_in_both_branches` **全部变红**。

变异 2 —— 把 Step B 块临时挪到 `apply_phase_decision_guardrails` **之前**,重跑:

Expected: `test_b_runs_after_phase_guardrail`、`test_apply_follows_phase_guardrail_in_both_branches` **变红**。

变异 3 —— 删掉 agent 分支的两个插入块,重跑:

Expected: `test_two_branches_each_have_both_hooks` **变红**。

**若某个变异之后测试仍然绿,那条测试就是 tautology —— 必须重写测试,而不是接受它。** 具体做法:把 stub 的 `trend_result` / `chip_data` 补足到能真正触发回填(用 `pytest -s` 打印 `result.dashboard` 确认回填确实发生),再重跑变异。

三个变异都确认后**全部改回来**,重跑一遍确保恢复绿。

- [ ] **Step 8: 提交**

```bash
git add src/core/pipeline.py tests/test_claim_validation_pipeline.py
git commit -m "feat: claim-validation 接入两条 pipeline 路径(A 在任何 in-place 回填之前,B 在 phase guardrail 之后),含顺序回归锁集成测试"
```

---

## Task 8: 通知渲染(两套引擎)+ 双语标签

仓库有**两套**报告渲染引擎,由 `config.report_renderer_enabled`(`config.py:847`,**默认 `False`**)切换。两者本就已经漂移(`ggt_context` 只在传统引擎有,`phase_decision` 只在 Jinja 有)。**两侧都加**,否则切换 flag 时提示行会时有时无。

**Files:**
- Modify: `src/report_language.py`(zh 块 ~L203 起、en 块 ~L325 起,各加 3 个键)
- Modify: `src/notification.py`(新增 `_render_claim_validation_section`,并在 `generate_dashboard_report` 调用)
- Modify: `templates/report_markdown.j2`
- Test: `tests/test_claim_validation_render.py`(新)

**Interfaces:**
- Produces: `_render_claim_validation_section(claim_validation: Optional[dict], report_language: str) -> str`

**presence-only**:只在 `transcription.status == "mismatch"` 或 `structural.status == "violation"` 时渲染。都 ok 时返回空串 —— 守卫通过时报告零变化。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_claim_validation_render.py`:

```python
# -*- coding: utf-8 -*-
"""claim-validation 提示行必须同时出现在两套渲染引擎里(Inc 3)。"""

import unittest

from src.notification import _render_claim_validation_section


def _cv(*, transcription_status="ok", structural_status="ok", mismatches=0):
    return {
        "applied": True,
        "transcription": {
            "status": transcription_status,
            "reason": None,
            "checked": 5,
            "mismatches": [{"field": "price_position.ma5"}] * mismatches,
        },
        "structural": {"status": structural_status, "reason": None, "violations": ["stop_loss(13.0) >= ideal_buy(12.5)"]},
        "actions": [],
    }


class TestRenderClaimValidationSection(unittest.TestCase):
    def test_all_ok_renders_nothing(self) -> None:
        self.assertEqual(_render_claim_validation_section(_cv(), "zh"), "")

    def test_missing_block_renders_nothing(self) -> None:
        self.assertEqual(_render_claim_validation_section(None, "zh"), "")
        self.assertEqual(_render_claim_validation_section({}, "zh"), "")

    def test_not_applicable_renders_nothing(self) -> None:
        self.assertEqual(
            _render_claim_validation_section(
                _cv(transcription_status="not_applicable"), "zh"
            ),
            "",
        )

    def test_mismatch_renders_zh(self) -> None:
        text = _render_claim_validation_section(
            _cv(transcription_status="mismatch", mismatches=2), "zh"
        )
        self.assertIn("2", text)
        self.assertIn("置信度", text)

    def test_mismatch_renders_en(self) -> None:
        text = _render_claim_validation_section(
            _cv(transcription_status="mismatch", mismatches=2), "en"
        )
        self.assertIn("2", text)
        self.assertIn("confidence", text.lower())

    def test_violation_renders_both_languages(self) -> None:
        for lang in ("zh", "en"):
            text = _render_claim_validation_section(_cv(structural_status="violation"), lang)
            self.assertTrue(text, lang)


class TestJinjaTemplateHasSection(unittest.TestCase):
    def test_report_markdown_template_renders_claim_validation(self) -> None:
        from pathlib import Path

        template = Path(__file__).resolve().parents[1] / "templates" / "report_markdown.j2"
        content = template.read_text(encoding="utf-8")
        self.assertIn("claim_validation", content)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
python -m pytest tests/test_claim_validation_render.py -v
```

Expected: `ImportError: cannot import name '_render_claim_validation_section'`

- [ ] **Step 3: `src/report_language.py` 加双语标签**

在 `_REPORT_LABELS` 的 `"zh"` 块内(与 `ggt_label` 等同区)加:

```python
        "claim_validation_heading": "数值校验",
        "claim_mismatch_text": "检出 {count} 处与输入数据不一致的数值陈述，已下调本次结论置信度。",
        "claim_unexecutable_text": "买卖计划数值不自洽，该计划不可执行。",
```

在 `"en"` 块内(**起始行是 L325,不是 L375**)加:

```python
        "claim_validation_heading": "Claim Validation",
        "claim_mismatch_text": "Found {count} numeric statement(s) inconsistent with the input data; confidence has been capped.",
        "claim_unexecutable_text": "The trade plan is numerically inconsistent and is not executable.",
```

- [ ] **Step 4: `src/notification.py` 加渲染函数**

在 `_render_ggt_section`(~L96)**之后**追加(镜像它的 presence-only 风格):

```python
def _render_claim_validation_section(claim_validation: Optional[dict], report_language: str) -> str:
    """渲染 LLM 数值校验提示(presence-only；仅在检出问题时出现)。

    转录类 ok / not_applicable、结构类 ok / not_applicable → 返回空串，
    报告与守卫未启用时逐字节一致。
    """
    if not claim_validation:
        return ""
    labels = get_report_labels(report_language)
    transcription = claim_validation.get("transcription") or {}
    structural = claim_validation.get("structural") or {}
    lines = []
    if transcription.get("status") == "mismatch":
        count = len(transcription.get("mismatches") or [])
        lines.append(labels["claim_mismatch_text"].format(count=count))
    if structural.get("status") == "violation":
        lines.append(labels["claim_unexecutable_text"])
    if not lines:
        return ""
    body = "\n".join(f"- {line}" for line in lines)
    return f"**⚠️ {labels['claim_validation_heading']}**\n{body}"
```

在 `generate_dashboard_report` 里,**`ggt_section` 那一段之后、`# ========== 作战计划 ==========` 之前**插入(注意 `claim_validation` 是**顶层 dashboard 键**,与 `data_perspective` 同级,**不要**从 `data_persp` 里取):

```python
                # LLM 数值校验(Inc 3；presence-only；顶层 dashboard 键)
                claim_section = _render_claim_validation_section(
                    dashboard.get('claim_validation') if dashboard else None, report_language
                )
                if claim_section:
                    report_lines.extend([claim_section, ""])
```

- [ ] **Step 5: `templates/report_markdown.j2` 加同款段落**

在 `phase_decision` 块(~L123-153)**之后**追加。先读现有块确认 `labels` 的用法与缩进,再照抄风格:

```jinja
{% set cv = dashboard.get('claim_validation') if dashboard else None %}
{% if cv %}
{% set _t = cv.get('transcription') or {} %}
{% set _s = cv.get('structural') or {} %}
{% if _t.get('status') == 'mismatch' or _s.get('status') == 'violation' %}
**⚠️ {{ labels.claim_validation_heading }}**
{% if _t.get('status') == 'mismatch' %}
- {{ labels.claim_mismatch_text.format(count=(_t.get('mismatches') or [])|length) }}
{% endif %}
{% if _s.get('status') == 'violation' %}
- {{ labels.claim_unexecutable_text }}
{% endif %}

{% endif %}
{% endif %}
```

- [ ] **Step 6: 跑测试确认通过**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
python -m pytest tests/test_claim_validation_render.py tests/test_notification_report_fixtures.py -v
```

Expected: 全部 PASS。**`test_notification_report_fixtures.py` 变红 = 你让守卫通过时也渲染了内容,违反 presence-only。**

- [ ] **Step 7: 提交**

```bash
git add src/report_language.py src/notification.py templates/report_markdown.j2 tests/test_claim_validation_render.py
git commit -m "feat: claim-validation 提示行接入两套渲染引擎(传统 notification + Jinja 模板),zh/en 双语标签,presence-only 守卫通过时零变化"
```

---

## Task 9: 配置注册 + locale + .env.example + 文档

**注册进 `config_registry` 会触及 `apps/dsa-web/src/locales/settingsHelp.ts`,本 task 触发 web-gate。**

**Files:**
- Modify: `src/core/config_registry.py`
- Modify: `apps/dsa-web/src/locales/settingsHelp.ts`(zhCN + enUS 两张 map)
- Modify: `.env.example`
- Create: `docs/llm-claim-validation.md`
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: 注册 registry 条目(16 键,一个都不能少)**

在 `src/core/config_registry.py` 的 `_FIELD_DEFINITIONS` 里,`ai_model` 类目区加(逐键照抄 `SIGNAL_BACKTEST_ENABLED` 的结构):

```python
    "LLM_CLAIM_VALIDATION_ENABLED": {
        "title": "LLM Claim Validation",
        "description": "Cross-check the numbers the LLM states against the numbers actually fed to it in the prompt, and validate that its trade plan is internally consistent. Disabled by default; when enabled, mismatches are annotated and confidence is capped (values are never overwritten).",
        "category": "ai_model",
        "data_type": "boolean",
        "ui_control": "switch",
        "is_sensitive": False,
        "is_required": False,
        "is_editable": True,
        "default_value": "false",
        "options": [],
        "validation": {},
        "display_order": 63,
        "help_key": "settings.ai_model.LLM_CLAIM_VALIDATION_ENABLED",
        "examples": [
            "LLM_CLAIM_VALIDATION_ENABLED=false",
            "LLM_CLAIM_VALIDATION_ENABLED=true",
        ],
        "docs": [
            {
                "label": "专题：LLM 数值校验守卫",
                "href": "https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/docs/llm-claim-validation.md",
            },
        ],
        "warning_codes": [],
    },
```

> `display_order=63`(`ai_model` 类目当前最大是 62)。该字段**无唯一性门禁**,但取未占用值以免打乱 UI 排序。

- [ ] **Step 2: `settingsHelp.ts` 两张 map 各加一条**

key 必须与 registry 的 `help_key` **字面一致**。在 zhCN map 里加:

```ts
  'settings.ai_model.LLM_CLAIM_VALIDATION_ENABLED': {
    title: 'LLM 数值校验守卫',
    summary: '启用后，系统会把 LLM 在报告里陈述的价位/指标数值，与 prompt 中实际喂给它的数值逐一核对，并检查其买卖计划是否内部自洽。',
    usage: '设为 true 后，个股分析报告会在检出问题时追加一段「数值校验」提示；不一致时本次结论的置信度会被下调。',
    valueNotes: [
      'false（默认）：不做任何校验，报告与现状逐字节一致。',
      'true：检出转录不一致 → 置信度「高」封顶为「中」；检出买卖计划不自洽 → 标注该计划不可执行。',
    ],
    impact: ['仅标注与降权，绝不覆盖 LLM 给出的数值、不改变买/卖/观望方向、不触发重试。'],
    notes: ['agent 模式下不产生 prompt，故转录类校验会标记为「不适用」，结构类校验仍然生效。'],
  },
```

在 enUS map 里加同 key 的英文条目(至少 `title`;门禁只要求 key 出现在**任一张** map,但两张都加以求 UI 完整):

```ts
  'settings.ai_model.LLM_CLAIM_VALIDATION_ENABLED': {
    title: 'LLM Claim Validation',
    summary: 'Cross-checks the numbers the LLM states against the numbers actually fed to it, and validates that its trade plan is internally consistent.',
    valueNotes: [
      'false (default): no validation; reports are byte-for-byte unchanged.',
      'true: transcription mismatch caps High confidence to Medium; an inconsistent trade plan is marked not executable.',
    ],
    impact: ['Annotate and downgrade only — values are never overwritten and the decision direction is never changed.'],
  },
```

- [ ] **Step 3: `.env.example` 加注释行**

在盘中回测段(~L725)之后加:

```
# LLM 数值校验守卫(默认关闭)。开启后把 LLM 陈述的数值与 prompt 喂给它的数值交叉核对,
# 不一致则标注并封顶置信度;绝不覆盖数值、不改决策方向、不触发重试。
# LLM_CLAIM_VALIDATION_ENABLED=false
```

**必须是注释行。** 裸键 `LLM_CLAIM_VALIDATION_ENABLED=false` 会被 `TestEnvExampleWebSettingsCoverage` 当作 active key 检查。

- [ ] **Step 4: 写 `docs/llm-claim-validation.md`**

内容至少覆盖:两类校验的语义(转录类 / 结构类)与为什么不做推断类;容差规则 `|claimed − fact| ≤ max(10^(−d), |fact|×1e-9)` 与 `d` 的两条来源;`dashboard["claim_validation"]` 的字段契约;动作(标注 + 分级降权,不覆盖数值 / 不改方向 / 不重试);挂载点不变式(A 在任何回填之前、B 在 phase guardrail 之后)与各自的理由;9 个被校验字段与 3 类被排除字段及理由;agent 路径的 `not_applicable`;配置项与默认值。

- [ ] **Step 5: `docs/CHANGELOG.md` 加扁平条目**

在 `[Unreleased]` 段追加(**扁平格式,禁止新增 `### 类目标题`**):

```markdown
- [新功能] 新增 LLM 数值校验守卫(`LLM_CLAIM_VALIDATION_ENABLED`,默认关闭):把 LLM 陈述的价位/指标与 prompt 实际喂入的数值交叉核对,并校验买卖计划内部自洽;不一致时标注并封顶置信度,绝不覆盖数值、不改决策方向、不触发重试。详见 `docs/llm-claim-validation.md`。
```

- [ ] **Step 6: 跑门禁**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
python -m pytest tests/test_config_registry.py -v
./scripts/ci_gate.sh
```

Expected: `tests/test_config_registry.py` 全绿(特别是 `test_web_settings_visible_fields_have_help_metadata`、`test_registry_help_keys_exist_in_locales`、`test_locale_help_keys_are_registry_or_llm_channel_internal`);`ci_gate.sh` 退出码 0。

**禁止 `| tail`。**

- [ ] **Step 7: 跑 web-gate**

```bash
cd "/root/AI/WorkSpace/cursor/AI _Trading_System/apps/dsa-web"
npm run lint && npm run build
```

> **踩坑**:主仓路径含空格,`npm ci` 会装出残缺的 `node_modules`。若 `npm run lint` 因依赖缺失报错,先在 `/root/` 下用 `git worktree` 起一个**无空格的持久路径**再跑前端门禁。

Expected: lint 0 error,build 成功。

- [ ] **Step 8: 提交**

```bash
git add src/core/config_registry.py apps/dsa-web/src/locales/settingsHelp.ts .env.example docs/llm-claim-validation.md docs/CHANGELOG.md
git commit -m "feat: 注册 LLM_CLAIM_VALIDATION_ENABLED 到 config_registry 与设置页 locale,补 .env.example 注释行、专题文档与 CHANGELOG"
```

---

## 收尾验收(全部 task 完成后)

- [ ] **全量后端门禁**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
./scripts/ci_gate.sh
```

Expected: 退出码 0。基线 **4015 passed**,本增量应为 **4015 + 新增用例数**。

- [ ] **默认字节级不变的最终验证**

```bash
export PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"
cd "/root/AI/WorkSpace/cursor/AI _Trading_System"
python -c "
from src.config import Config
assert Config.llm_claim_validation_enabled is False, '默认必须关闭'
print('OK: 默认关闭')
"
python -m pytest tests/test_notification_report_fixtures.py -v
```

Expected: golden 报告夹具全绿(守卫默认关闭 → 报告文本零变化)。

- [ ] **交付说明**(按 AGENTS.md §9)必须包含:改了什么 / 为什么这么改 / 验证情况 / 未验证项 / 风险点 / 回滚方式。

**未验证项(预期)**:真实 LLM 端到端(需在线,离线 fixture 全覆盖);Jinja 引擎侧需 `REPORT_RENDERER_ENABLED=true` 才生效,本增量仅有模板存在性断言,无渲染快照。

**回滚方式**:`LLM_CLAIM_VALIDATION_ENABLED` 保持默认 `false` 即可完全停用(零行为变化);彻底回滚 `git revert` 全部 9 个 commit。
