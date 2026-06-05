"""Factor layer: factor abstraction, registry, normalization, factor library.

Importing this package registers the built-in technical and fundamental
factors as a side effect.
"""
from __future__ import annotations

from quantpick.factors.base import Factor, all_factors, get_factor, register
from quantpick.factors.normalize import rank_pct, winsorize, zscore

# Importing these modules registers their factors in the global registry.
from quantpick.factors import fundamental as _fundamental  # noqa: F401
from quantpick.factors import technical as _technical  # noqa: F401

__all__ = [
    "Factor",
    "all_factors",
    "get_factor",
    "register",
    "rank_pct",
    "winsorize",
    "zscore",
]
