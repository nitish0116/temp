"""
modules/report/summary.py

Generate human-readable OCR cleanup summary report.
"""

from __future__ import annotations

import re
from collections import Counter

from pathlib import Path

from datetime import datetime


def _table_cell(value: object) -> str:
    """Escape a value for a single Markdown table cell."""
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _fenced_block(value: object) -> str:
    """Render arbitrary text in a fence longer than any contained backtick run."""
    text = str(value)
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}\n\n"


class SummaryReporter:
    """Creates markdown cleanup reports.

    Example:
        ``instance = SummaryReporter(change_log)``
        Expected behavior: Creates markdown cleanup reports.
    """

    def __init__(
        self,
        change_log,
        review_threshold: float = 85.0,
    ):
        """Configure summary generation and its review threshold.

        Example:
            ``instance = SummaryReporter(change_log)``
            Expected behavior: Configure summary generation and its review threshold.
        """

        self.change_log = change_log

        self.review_threshold = review_threshold

    # ---------------------------------------------------------

    def render(self, source_file=None, *, generated_at: str | None = None) -> str:
        """Return the complete Markdown report without writing to disk.

        Separating rendering from I/O makes report behavior straightforward to
        test. Review excerpts use adaptive code fences, so OCR text containing
        triple backticks cannot corrupt the report structure.
        """
        generated_at = generated_at or datetime.now().isoformat()
        records = self.change_log.records
        review_items = self.change_log.needs_review(self.review_threshold)
        applied_count = sum(item.applied for item in records)
        proposed_count = len(records) - applied_count
        automatic_count = sum(
            item.applied and item.confidence >= self.review_threshold
            for item in records
        )
        stage_counter = Counter(record.stage for record in records)

        lines = [
            "# OCR Cleanup Report\n\n",
            "Generated:\n\n",
            f"{generated_at}\n\n",
        ]
        if source_file:
            lines.extend(["## File\n\n", f"{source_file}\n\n"])

        lines.extend(
            [
                "## Summary\n\n",
                f"Total audit records: {len(records)}\n\n",
                f"Applied mutations: {applied_count}\n\n",
                f"Report-only/suppressed proposals: {proposed_count}\n\n",
                "## Changes by Stage\n\n",
            ]
        )
        if stage_counter:
            lines.extend(["| Stage | Changes |\n", "|---|---:|\n"])
            lines.extend(
                f"| {_table_cell(stage)} | {count} |\n"
                for stage, count in sorted(stage_counter.items())
            )
        else:
            lines.append("No changes recorded.\n")

        lines.extend(
            [
                "\n## Confidence\n\n",
                f"Threshold: {self.review_threshold}%\n\n",
                f"High-confidence records: {automatic_count}\n\n",
                f"Review required: {len(review_items)}\n\n",
            ]
        )

        if review_items:
            lines.append("## Review Required\n\n")
            for index, item in enumerate(review_items, start=1):
                lines.extend(
                    [
                        f"### {index}\n\n",
                        "Before:\n\n",
                        _fenced_block(item.before),
                        "After:\n\n",
                        _fenced_block(item.after),
                        f"Confidence: {item.confidence}%\n\n",
                        f"Reason: {item.reason}\n\n",
                        f"Applied: {'yes' if item.applied else 'no'}\n\n",
                    ]
                )
                if item.broken_word:
                    lines.append(f"Broken word: {item.broken_word}\n\n")

        return "".join(lines)

    def generate(self, output_file, source_file=None):
        """Generate a Markdown summary report and return its path."""
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(self.render(source_file), encoding="utf-8")
        return output_path
