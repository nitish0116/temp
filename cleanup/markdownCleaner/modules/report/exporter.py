"""Final pipeline output exporter."""

from __future__ import annotations

import re
import json
from pathlib import Path

from .summary import SummaryReporter


def meaningful_output_name(source_file: str | Path) -> str:
    """Return a readable, filesystem-safe name for a cleaned Markdown file.

    Release/source tags such as ``[Yen Press][Kobo]`` are useful on input files
    but make poor output names. Keep the actual book title and volume, use normal
    spaces, and make the generated-file status explicit.
    """
    source = Path(source_file)
    name = source.stem

    # Do not accumulate generated suffixes when an output is cleaned again.
    name = re.sub(r"(?i)(?:[ _-]+)(?:clean|cleaned)$", "", name)
    # Drop trailing release/source tags while retaining brackets that are part of
    # an actual title elsewhere in the name.
    name = re.sub(r"(?:\s*\[[^\[\]]+\])+\s*$", "", name)
    name = name.replace("_", " ")
    name = re.sub(r"\s*[-–—]\s*", " - ", name)
    name = re.sub(r"\s+", " ", name).strip(" .-_")

    # Windows-invalid filename characters and control characters.
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "Cleaned document"

    return f"{name} - Cleaned.md"


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
        vocabulary_candidates: list[dict] | None = None,
    ):
        self._create_directories()

        source = Path(source_file)
        filename = output_name or meaningful_output_name(source)
        markdown_path = self.output_directory / filename
        markdown_path.write_text(cleaned_markdown, encoding="utf-8")

        changes_path = self.report_directory / "changes.json"
        change_log.export_json(changes_path)

        summary_path = self.report_directory / "summary.md"
        SummaryReporter(change_log).generate(summary_path, source_file)

        candidates_path = self.report_directory / "glossary_candidates.json"
        candidates_path.write_text(
            json.dumps(vocabulary_candidates or [], indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )

        return {
            "markdown": markdown_path,
            "changes": changes_path,
            "summary": summary_path,
            "glossary_candidates": candidates_path,
        }

    def _create_directories(self):
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.report_directory.mkdir(parents=True, exist_ok=True)
