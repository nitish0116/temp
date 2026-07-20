"""Final pipeline output exporter."""
from __future__ import annotations

from pathlib import Path

from .summary import SummaryReporter


class ReportExporter:
    """Export cleaned Markdown and optional per-file reports."""

    def __init__(self, output_directory, report_subdirectory="reports"):
        self.output_directory = Path(output_directory)
        self.report_directory = self.output_directory / Path(report_subdirectory)

    def export(
        self,
        *,
        cleaned_markdown,
        source_file,
        change_log,
        output_name: str | None = None,
    ):
        self._create_directories()

        source = Path(source_file)
        filename = output_name or (source.stem.replace(" ", "_") + "_clean.md")
        markdown_path = self.output_directory / filename
        markdown_path.write_text(cleaned_markdown, encoding="utf-8")

        changes_path = self.report_directory / "changes.json"
        change_log.export_json(changes_path)

        summary_path = self.report_directory / "summary.md"
        SummaryReporter(change_log).generate(summary_path, source_file)

        return {
            "markdown": markdown_path,
            "changes": changes_path,
            "summary": summary_path,
        }

    def _create_directories(self):
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.report_directory.mkdir(parents=True, exist_ok=True)
