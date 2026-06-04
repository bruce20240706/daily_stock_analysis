"""Console output: render scored candidates as a rich table."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from quantpick.core.types import ScoredStock


def render_table(stocks: "list[ScoredStock]", title: str = "Candidates") -> None:
    """Pretty-print ranked candidates to stdout using rich."""
    from rich.console import Console
    from rich.table import Table

    table = Table(title=title)
    table.add_column("#", justify="right")
    table.add_column("Code")
    table.add_column("Name")
    table.add_column("Market", justify="center")
    table.add_column("Score", justify="right")
    for s in stocks:
        table.add_row(str(s.rank), s.code, s.name, s.market.value, f"{s.total_score:.3f}")
    Console().print(table)
