"""
modules/report/exporter.py

Final pipeline output exporter.

Creates:

- cleaned markdown
- JSON change log
- summary report
"""

from __future__ import annotations

from pathlib import Path

import shutil


from .summary import (
    SummaryReporter,
)


class ReportExporter:
    """
    Export final cleanup results.
    """

    def __init__(
        self,
        output_directory,
    ):

        self.output_directory = Path(output_directory)

        self.report_directory = self.output_directory / "reports"

    # ---------------------------------------------------------

    def export(
        self,
        *,
        cleaned_markdown,
        source_file,
        change_log,
    ):
        """
        Export all pipeline outputs.
        """

        self._create_directories()

        #
        # Export markdown
        #

        markdown_path = (
    self.output_directory
    / (
        Path(source_file).stem.replace(" ", "_")
        + "_clean.md"
    )
)

        markdown_path.write_text(
            cleaned_markdown,
            encoding="utf-8",
        )

        #
        # Export JSON changes
        #

        changes_path = self.report_directory / "changes.json"

        change_log.export_json(changes_path)

        #
        # Export summary
        #

        summary_path = self.report_directory / "summary.md"

        reporter = SummaryReporter(change_log)

        reporter.generate(
            summary_path,
            source_file,
        )

        return {
            "markdown": markdown_path,
            "changes": changes_path,
            "summary": summary_path,
        }

    # ---------------------------------------------------------

    def _create_directories(
        self,
    ):

        self.output_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.report_directory.mkdir(
            parents=True,
            exist_ok=True,
        )
