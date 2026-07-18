"""
modules/report/summary.py

Generate human-readable OCR cleanup summary report.
"""

from __future__ import annotations

from collections import Counter

from pathlib import Path

from datetime import datetime


class SummaryReporter:
    """
    Creates markdown cleanup reports.
    """

    def __init__(
        self,
        change_log,
        review_threshold: float = 85.0,
    ):

        self.change_log = change_log

        self.review_threshold = review_threshold

    # ---------------------------------------------------------

    def generate(
        self,
        output_file,
        source_file=None,
    ):
        """
        Generate markdown summary report.

        Args:
            output_file:
                Report markdown path

            source_file:
                Original processed file name
        """

        output_path = Path(output_file)

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        lines = []

        #
        # Header
        #

        lines.append("# OCR Cleanup Report\n\n")

        lines.append("Generated:\n\n")

        lines.append(f"{datetime.now().isoformat()}\n\n")

        #
        # Source file
        #

        if source_file:

            lines.append("## File\n\n")

            lines.append(f"{source_file}\n\n")

        #
        # Total changes
        #

        total_changes = self.change_log.total_changes()

        lines.append("## Summary\n\n")

        lines.append(f"Total corrections: {total_changes}\n\n")

        #
        # Stage statistics
        #

        lines.append("## Changes by Stage\n\n")

        stage_counter = Counter()

        for record in self.change_log.records:

            stage_counter[record.stage] += 1

        if stage_counter:

            lines.append("| Stage | Changes |\n")

            lines.append("|---|---:|\n")

            for stage, count in sorted(stage_counter.items()):

                lines.append(f"| {stage} | {count} |\n")

        else:

            lines.append("No changes recorded.\n")

        #
        # Confidence statistics
        #

        lines.append("\n## Confidence\n\n")

        automatic_count = len(self.change_log.high_confidence(self.review_threshold))

        review_count = len(self.change_log.needs_review(self.review_threshold))

        lines.append(f"Threshold: {self.review_threshold}%\n\n")

        lines.append(f"Automatic corrections: {automatic_count}\n\n")

        lines.append(f"Review required: {review_count}\n\n")

        #
        # Review section
        #

        review_items = self.change_log.needs_review(self.review_threshold)

        if review_items:

            lines.append("## Review Required\n\n")

            for index, item in enumerate(
                review_items,
                start=1,
            ):

                lines.append(f"### {index}\n\n")

                lines.append("Before:\n\n")

                lines.append("```\n")

                lines.append(f"{item.before}\n")

                lines.append("```\n\n")

                lines.append("After:\n\n")

                lines.append("```\n")

                lines.append(f"{item.after}\n")

                lines.append("```\n\n")

                lines.append(f"Confidence: {item.confidence}%\n\n")

                lines.append(f"Reason: {item.reason}\n\n")

        #
        # Write report
        #

        output_path.write_text(
            "".join(lines),
            encoding="utf-8",
        )

        return output_path
