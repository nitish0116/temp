"""Final pipeline output exporter."""

from __future__ import annotations

import re
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..core.config import require_bool
from .change_log import ChangeLog
from .summary import SummaryReporter


def meaningful_output_name(source_file: str | Path) -> str:
    """Return a readable, filesystem-safe name for a cleaned Markdown file.

    Release/source tags such as ``[Yen Press][Kobo]`` are useful on input files
    but make poor output names. Keep the actual book title and volume, use normal
    spaces, and make the generated-file status explicit.

    Example:
        ``meaningful_output_name("Tanya_V13_[Kobo].md")`` returns
        ``"Tanya V13 - Cleaned.md"``.
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


@dataclass(frozen=True, slots=True)
class ReportOptions:
    """Control companion report creation without affecting Markdown export.

    Defaults preserve the historical behavior: every companion report is
    written and all confidence levels are included. ``from_config`` accepts
    either a full ``PipelineConfig`` or a mapping containing the ``report``
    section.
    """

    enabled: bool = True
    export_json: bool = True
    export_summary: bool = True
    include_low_confidence: bool = True
    review_threshold: float = 85.0

    @classmethod
    def from_config(cls, config) -> ReportOptions:
        """Build typed options from pipeline configuration or a mapping."""
        if hasattr(config, "section"):
            values = config.section("report")
        elif isinstance(config, Mapping):
            candidate = config.get("report", config)
            values = candidate if isinstance(candidate, Mapping) else {}
        else:
            raise TypeError("report options require PipelineConfig or a mapping")

        return cls(
            enabled=require_bool(
                values.get("enabled", True),
                "report.enabled",
            ),
            export_json=require_bool(
                values.get("export_json", True),
                "report.export_json",
            ),
            export_summary=require_bool(
                values.get("export_summary", True),
                "report.export_summary",
            ),
            include_low_confidence=require_bool(
                values.get("include_low_confidence", True),
                "report.include_low_confidence",
            ),
            review_threshold=float(values.get("review_threshold", 85.0)),
        )


class ReportExporter:
    """Export cleaned Markdown and its auditable companion reports.

    Markdown is always written. By default each export also writes JSON change
    records, a readable Markdown summary, and pending vocabulary candidates.
    :class:`ReportOptions` can disable companion reports, individual change
    formats, or low-confidence record inclusion.

    Example:
        ``instance = ReportExporter(Path("output"))``
        Expected behavior: Export cleaned Markdown and its auditable companion reports.
    """

    def __init__(
        self,
        output_directory,
        report_subdirectory="reports",
        *,
        options: ReportOptions | None = None,
    ):
        """Configure Markdown/report destinations and export policy.

        Example:
            ``instance = ReportExporter(Path("output"))``
            Expected behavior: Configure Markdown and report destination directories.
        """
        self.output_directory = Path(output_directory)
        self.report_directory = self.output_directory / Path(report_subdirectory)
        self.options = options or ReportOptions()

    def export(
        self,
        *,
        cleaned_markdown,
        source_file,
        change_log,
        output_name: str | None = None,
        vocabulary_candidates: list[dict] | None = None,
    ):
        """Write all artifacts for one successfully processed source.

        Args:
            cleaned_markdown: Final reconstructed Markdown text.
            source_file: Original source used in names and report metadata.
            change_log: Shared tracker capable of JSON export.
            output_name: Optional filename overriding the generated readable name.
            vocabulary_candidates: Review-only terms discovered by the pipeline.

        Returns:
            A mapping from names to paths for artifacts actually written.
            ``markdown`` is always present; disabled report formats are omitted.

        Example:
            ``result = instance.export(cleaned_markdown="Cleaned text.", source_file=Path("input.md"), change_log=change_log)``
            Expected behavior: Write all artifacts for one successfully processed source.
        """
        source = Path(source_file)
        filename = output_name or meaningful_output_name(source)
        markdown_path = self.output_directory / filename
        if markdown_path.resolve() == source.resolve():
            raise ValueError(
                "Refusing to overwrite the source Markdown in place. "
                "Choose a different output directory or output name."
            )

        self._create_directories()
        markdown_path.write_text(cleaned_markdown, encoding="utf-8")

        paths = {"markdown": markdown_path}
        if not self.options.enabled:
            return paths

        report_log = change_log
        if not self.options.include_low_confidence:
            if isinstance(change_log, ChangeLog):
                report_log = change_log.with_minimum_confidence(
                    self.options.review_threshold
                )
            else:
                report_log = ChangeLog(
                    record
                    for record in change_log.records
                    if record.confidence >= self.options.review_threshold
                )

        if self.options.export_json:
            changes_path = self.report_directory / "changes.json"
            report_log.export_json(changes_path)
            paths["changes"] = changes_path

        if self.options.export_summary:
            summary_path = self.report_directory / "summary.md"
            SummaryReporter(
                report_log,
                review_threshold=self.options.review_threshold,
            ).generate(summary_path, source_file)
            paths["summary"] = summary_path

        candidates_path = self.report_directory / "glossary_candidates.json"
        candidates_path.write_text(
            json.dumps(vocabulary_candidates or [], indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        paths["glossary_candidates"] = candidates_path

        return paths

    def _create_directories(self):
        """Create output and report directories when absent.

        Example:
            ``result = instance._create_directories()``
            Expected behavior: Create output and report directories when absent.
        """
        self.output_directory.mkdir(parents=True, exist_ok=True)
        if self.options.enabled:
            self.report_directory.mkdir(parents=True, exist_ok=True)
