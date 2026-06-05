from __future__ import annotations

import pytest

from quantpick.core.types import Market


@pytest.fixture
def sample_rows() -> list[tuple[str, str, Market, dict[str, float]]]:
    """A tiny universe of (code, name, market, factor_scores) for scorer tests."""
    return [
        ("600519.SH", "Kweichow Moutai", Market.A_SHARE, {"f1": 1.0, "f2": 0.0}),
        ("000001.SZ", "Ping An Bank", Market.A_SHARE, {"f1": 0.0, "f2": 1.0}),
        ("000002.SZ", "Vanke", Market.A_SHARE, {"f1": 0.5, "f2": 0.5}),
    ]
