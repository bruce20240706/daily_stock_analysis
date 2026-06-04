"""QuantPick - personal A-share / HK stock screening tool.

Rule-based quantitative scoring with a pluggable AI-enhancement layer.

This top-level package intentionally avoids importing heavy submodules, so that
``import quantpick`` stays cheap and dependency-light. Import submodules
explicitly, e.g. ``from quantpick.screening import Screener``.
"""
from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
