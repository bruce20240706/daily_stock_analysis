from __future__ import annotations

import math

import numpy as np

from quantpick.factors.normalize import rank_pct, winsorize, zscore


def test_zscore_basic() -> None:
    out = zscore([1, 2, 3])
    assert abs(float(out.mean())) < 1e-9
    assert out[0] < out[1] < out[2]


def test_zscore_constant_is_zero() -> None:
    assert np.allclose(zscore([5, 5, 5]), 0.0)


def test_zscore_preserves_nan() -> None:
    out = zscore([1.0, float("nan"), 3.0])
    assert math.isnan(float(out[1]))


def test_rank_pct_range() -> None:
    out = rank_pct([10, 20, 30, 40])
    assert float(out.min()) == 0.0
    assert float(out.max()) == 1.0


def test_winsorize_clips_upper() -> None:
    out = winsorize([0, 1, 2, 3, 100], lower=0.0, upper=0.75)
    assert float(out[-1]) <= 3.0 + 1e-9
