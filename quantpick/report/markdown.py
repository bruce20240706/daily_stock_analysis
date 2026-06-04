"""Markdown report rendering and saving."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from quantpick.ai.base import DISCLAIMER

if TYPE_CHECKING:  # pragma: no cover - typing only
    from quantpick.core.types import AnalysisCard, ScoredStock


def render_markdown(
    report_date: date,
    stocks: "list[ScoredStock]",
    cards: "dict[str, AnalysisCard] | None" = None,
    title: str = "QuantPick Daily",
) -> str:
    """Render a daily report (candidate table + optional AI cards) to Markdown."""
    cards = cards or {}
    lines: list[str] = [f"# {title} - {report_date.isoformat()}", ""]
    lines.append("| # | Code | Name | Market | Score |")
    lines.append("|---:|------|------|:------:|------:|")
    for s in stocks:
        lines.append(
            f"| {s.rank} | {s.code} | {s.name} | {s.market.value} | {s.total_score:.3f} |"
        )
    lines.append("")
    if cards:
        lines.append("## AI Analysis")
        lines.append("")
        for s in stocks:
            card = cards.get(s.code)
            if card is None:
                continue
            lines.append(f"### {s.code} {s.name}")
            lines.append(card.conclusion)
            lines.append("")
    lines.append("---")
    lines.append(f"> {DISCLAIMER}")
    return "\n".join(lines) + "\n"


def save_report(content: str, reports_dir: str, report_date: date) -> Path:
    """Write the report to ``<reports_dir>/<date>.md`` and return the path."""
    d = Path(reports_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{report_date.isoformat()}.md"
    path.write_text(content, encoding="utf-8")
    return path
