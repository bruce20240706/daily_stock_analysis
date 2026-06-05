"""Report layer: console tables and Markdown daily reports."""
from __future__ import annotations

from quantpick.report.console import render_table
from quantpick.report.markdown import render_markdown, save_report

__all__ = ["render_table", "render_markdown", "save_report"]
