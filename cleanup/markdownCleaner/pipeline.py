"""End-to-end orchestration for the OCR Markdown cleanup pipeline."""

from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter
from typing import Any

from markdownCleaner.modules.cleanup.document import DocumentCleanupStage
from markdownCleaner.modules.cleanup.tts_validation import TTSValidationStage
from markdownCleaner.modules.core.config import PipelineConfig
from markdownCleaner.modules.core.context import ProcessingContext
from markdownCleaner.modules.core.logger import (
    get_logger,
    initialize as initialize_logging,
)
from markdownCleaner.modules.core.stage import PipelineStage, StageResult
from markdownCleaner.modules.regex.stage import RegexStage
from markdownCleaner.modules.report.backup import BackupManager
from markdownCleaner.modules.report.exporter import ReportExporter, ReportOptions
from markdownCleaner.modules.symspell.stage import SymSpellStage
from markdownCleaner.modules.symspell.vocabulary import VocabularyCandidateStage
from markdownCleaner.modules.unicode.stage import UnicodeStage


DEFAULT_STAGE_TYPES: tuple[type[PipelineStage], ...] = (
    DocumentCleanupStage,
    UnicodeStage,
    RegexStage,
    VocabularyCandidateStage,
    SymSpellStage,
    TTSValidationStage,
)


class OCRPipeline:
    """Coordinate loading, ordered cleanup stages, and artifact export.

    A fresh :class:`ProcessingContext` and stage list are created for every
    :meth:`run`. Callers reusing one output directory for multiple files should
    pass distinct ``report_subdirectory`` values to avoid replacing companion
    reports. Stage failures are returned while later stages continue.
    """

    stage_types = DEFAULT_STAGE_TYPES

    def __init__(self, config_file: str | Path) -> None:
        self.config = PipelineConfig.load(config_file)
        self.config.apply_environment()
        self.config.validate()
        self._configure_logging()

        self.logger = get_logger()
        self.logger.info("OCR Cleanup Pipeline started.")
        self.context: ProcessingContext | None = None
        self.stages: list[PipelineStage] = []

    def _configure_logging(self) -> None:
        """Apply configured directory, file, and level to the shared logger."""

        directory = self.config.resolve_path(
            self.config.get("logging.directory", "logs")
        )
        configured_file = self.config.get("logging.file")
        log_file = None
        if configured_file is not None:
            configured_path = Path(str(configured_file))
            if configured_path.is_absolute():
                log_file = configured_path
            elif configured_path.parent == Path("."):
                # A bare filename belongs beneath logging.directory.
                log_file = configured_path
            else:
                # A relative path with directories is config-file-relative.
                log_file = self.config.resolve_path(configured_path)
        initialize_logging(
            directory or "logs",
            level=self.config.get("logging.level", logging.INFO),
            log_file=log_file,
        )

    def _build_stages(self) -> list[PipelineStage]:
        """Construct the configured ordered stage workflow."""

        return [stage_type(self.config) for stage_type in self.stage_types]

    def initialize(self, input_file: str | Path) -> None:
        """Load one document into a fresh context and construct its stages."""

        self.context = ProcessingContext(self.config)
        self.context.load_markdown(input_file)
        self.stages = self._build_stages()

    def backup(self, input_file: str | Path) -> Path:
        """Create a timestamped backup of the original input."""

        backup_directory = self.config.resolve_path(
            self.config.get("backup.directory", "backup")
        )
        manager = BackupManager(backup_directory or "backup")
        return manager.create_backup(input_file)

    def _require_context(self) -> ProcessingContext:
        if self.context is None:
            raise RuntimeError("Pipeline has not been initialized.")
        return self.context

    def _execute_stages(self) -> list[StageResult]:
        """Execute all registered stages and retain failure diagnostics."""

        context = self._require_context()
        results: list[StageResult] = []

        for stage in self.stages:
            result = stage.execute(context)
            self.logger.info("%s: %s changes", stage.name, result.changes)
            results.append(result)

            if result.success:
                print(f"✓ {stage.name}: {result.changes} changes")
            else:
                print(f"✗ {stage.name}: {result.error}")

        return results

    def _export(
        self,
        *,
        input_file: str | Path,
        output_directory: str | Path | None,
        output_name: str | None,
        report_subdirectory: str | Path,
    ) -> dict[str, Path]:
        """Export final artifacts through one integration point."""

        context = self._require_context()
        configured_output = self.config.resolve_path(
            self.config.get("paths.output_directory", "output")
        )
        exporter = ReportExporter(
            output_directory
            if output_directory is not None
            else configured_output or "output",
            report_subdirectory=report_subdirectory,
            options=ReportOptions.from_config(self.config),
        )
        result = exporter.export(
            cleaned_markdown=context.get_markdown(),
            source_file=input_file,
            change_log=context.tracker,
            output_name=output_name,
            vocabulary_candidates=context.metadata.get("glossary_candidates", []),
        )
        context.output_file = str(result["markdown"])
        return result

    def run(
        self,
        input_file: str | Path,
        *,
        output_directory: str | Path | None = None,
        output_name: str | None = None,
        report_subdirectory: str | Path = "reports",
    ) -> dict[str, Any]:
        """Execute the complete workflow for one Markdown file."""

        started = perf_counter()
        # Another pipeline instance may have reconfigured the shared logger
        # since this object was constructed.
        self._configure_logging()
        self.logger = get_logger()
        self.logger.info("Processing: %s", input_file)

        backup_path = None
        if self.config.get_bool("backup.enabled", True):
            backup_path = self.backup(input_file)
            print(f"Backup created: {backup_path}")

        self.initialize(input_file)
        results = self._execute_stages()
        context = self._require_context()
        context.finish()

        export_result = self._export(
            input_file=input_file,
            output_directory=output_directory,
            output_name=output_name,
            report_subdirectory=report_subdirectory,
        )

        return {
            "backup": backup_path,
            "stages": results,
            "output": export_result,
            "elapsed_seconds": round(perf_counter() - started, 2),
        }


def main(argv: list[str] | None = None) -> int:
    """Delegate the historical pipeline entry point to the canonical CLI."""

    from markdownCleaner.cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
