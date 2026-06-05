"""Holdings / watchlist: the stocks that sell-signals are evaluated against.

Loaded from a user-maintained YAML file (``config/holdings.yaml``), which is
git-ignored because positions are personal. See ``config/holdings.example.yaml``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from quantpick.core.errors import ErrorCode, Result


@dataclass(slots=True)
class Holding:
    code: str
    name: str = ""
    shares: float = 0.0
    cost_price: float | None = None     # optional cost basis (enables P&L later)


def load_holdings(path: str | Path = "config/holdings.yaml") -> Result[list[Holding]]:
    """Load the holdings / watchlist.

    A missing file is not an error - it means "no holdings yet" (empty list),
    so a fresh checkout simply produces no sell signals until configured.
    """
    p = Path(path)
    if not p.exists():
        return Result.success([])
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        items = raw.get("holdings", []) if isinstance(raw, dict) else []
        holdings = [
            Holding(
                code=str(it["code"]),
                name=str(it.get("name", "")),
                shares=float(it.get("shares", 0) or 0),
                cost_price=(
                    float(it["cost_price"]) if it.get("cost_price") is not None else None
                ),
            )
            for it in items
        ]
        return Result.success(holdings)
    except (yaml.YAMLError, ValueError, KeyError, TypeError) as exc:
        return Result.fail(ErrorCode.CONFIG_INVALID, f"{p}: {exc}")
