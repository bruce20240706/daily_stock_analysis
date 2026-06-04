"""Logging setup. Uses rich if available, else stdlib formatting."""
from __future__ import annotations

import logging


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging once. Safe to call multiple times."""
    lvl = getattr(logging, level.upper(), logging.INFO)
    handler: logging.Handler
    try:
        from rich.logging import RichHandler

        handler = RichHandler(rich_tracebacks=True, show_path=False)
        fmt = "%(message)s"
    except Exception:  # pragma: no cover - rich is a core dep, but stay robust
        handler = logging.StreamHandler()
        fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=lvl, format=fmt, handlers=[handler], force=True)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
