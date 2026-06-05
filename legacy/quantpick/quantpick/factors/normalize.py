"""Cross-sectional normalization helpers (pure numpy).

These turn a raw factor column (one value per stock) into comparable scores so
factors on different scales can be combined. Fully implemented and unit-tested.
"""
from __future__ import annotations

import numpy as np


def _as_float_array(values: object) -> np.ndarray:
    return np.asarray(values, dtype=float)


def winsorize(values: object, lower: float = 0.01, upper: float = 0.99) -> np.ndarray:
    """Clip extreme values to the [lower, upper] quantiles. NaNs are preserved."""
    arr = _as_float_array(values).copy()
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return arr
    lo, hi = np.quantile(finite, [lower, upper])
    clipped = np.clip(arr, lo, hi)
    clipped[~np.isfinite(arr)] = np.nan
    return clipped


def zscore(values: object) -> np.ndarray:
    """Standardize to mean 0 / std 1 over finite values. NaNs preserved.

    Returns all-zeros (for finite entries) when the column has zero variance.
    """
    arr = _as_float_array(values)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return arr
    mu = float(finite.mean())
    sd = float(finite.std())
    out = np.full(arr.shape, np.nan)
    mask = np.isfinite(arr)
    out[mask] = 0.0 if sd == 0 else (arr[mask] - mu) / sd
    return out


def rank_pct(values: object) -> np.ndarray:
    """Percentile rank in [0, 1] over finite values. NaNs preserved.

    Ties get distinct ranks (stable ordering); good enough for scoring.
    """
    arr = _as_float_array(values)
    out = np.full(arr.shape, np.nan)
    mask = np.isfinite(arr)
    n = int(mask.sum())
    if n == 0:
        return out
    ranks = arr[mask].argsort().argsort().astype(float)
    out[mask] = 0.5 if n == 1 else ranks / (n - 1)
    return out
