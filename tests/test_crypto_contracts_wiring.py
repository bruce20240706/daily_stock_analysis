# -*- coding: utf-8 -*-
"""全站点契约守卫：每个 ReportDetails(...) 构造点都必须接 crypto_contracts=（防漏接历史/任务态入口）。"""
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_FILES = [
    _ROOT / "api" / "v1" / "endpoints" / "analysis.py",
    _ROOT / "api" / "v1" / "endpoints" / "history.py",
]


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_every_report_details_site_wires_crypto_contracts(path):
    text = path.read_text(encoding="utf-8")
    construct_sites = text.count("ReportDetails(")          # 仅构造调用（class 定义在 schemas，不在本文件）
    wired = text.count("crypto_contracts=")
    assert construct_sites > 0, f"{path.name}: 预期存在 ReportDetails 构造点"
    assert wired >= construct_sites, (
        f"{path.name}: {construct_sites} 个 ReportDetails 构造点，但只有 {wired} 处接 crypto_contracts="
        "（漏接会导致该入口的报告永远不透出永续指标）"
    )
