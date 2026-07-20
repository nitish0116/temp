"""
pipeline.py

Main OCR cleanup pipeline.

Workflow:

Markdown
    |
Backup
    |
UnicodeStage
    |
RegexStage
    |
SymSpellStage
    |
Export Reports
"""

from __future__ import annotations


from markdownCleaner.modules.cleanup.document import DocumentCleanupStage
from markdownCleaner.modules.cleanup.tts_validation import TTSValidationStage
from markdownCleaner.modules.symspell.vocabulary import VocabularyCandidateStage
from markdownCleaner.modules.core.logger import (
    initialize,
    get_logger,
)


from pathlib import Path


from markdownCleaner.modules.report.backup import (
    BackupManager,
)


from markdownCleaner.modules.report.exporter import (
    ReportExporter,
)


from markdownCleaner.modules.unicode.stage import (
    UnicodeStage,
)


from markdownCleaner.modules.regex.stage import (
    RegexStage,
)


from markdownCleaner.modules.symspell.stage import (
    SymSpellStage,
)


from markdownCleaner.modules.core.context import (
    ProcessingContext,
)


from markdownCleaner.modules.core.config import (
    PipelineConfig,
)


class OCRPipeline:
    """
    Main OCR cleanup pipeline.
    """

    def __init__(
        self,
        config_file,
    ):

        self.config = PipelineConfig.load(config_file)

        self.config.apply_environment()

        self.config.validate()

        initialize(
            self.config.get(
                "logging.directory",
                "logs",
            ),
            # Optional:
            # logging.DEBUG if self.config.get("logging.debug", False)
            # else logging.INFO
        )

        get_logger().info("OCR Cleanup Pipeline started.")

        self.context = None

        self.stages = []

    # ---------------------------------------------------------

    def initialize(
        self,
        input_file,
    ):
        """
        Initialize pipeline.
        """

        self.context = ProcessingContext(self.config)

        #
        # Load markdown
        #

        self.context.load_markdown(input_file)

        #
        # Register stages
        #

        self.stages = [
            DocumentCleanupStage(self.config),
            UnicodeStage(self.config),
            RegexStage(self.config),
            VocabularyCandidateStage(self.config),
            SymSpellStage(self.config),
            TTSValidationStage(self.config),
        ]

    # ---------------------------------------------------------

    def backup(
        self,
        input_file,
    ):
        """
        Create original backup.
        """

        manager = BackupManager(
            self.config.get(
                "backup.directory",
                "backup",
            )
        )

        return manager.create_backup(input_file)

    # ---------------------------------------------------------

    def run(
        self,
        input_file,
        *,
        output_directory=None,
        output_name=None,
        report_subdirectory="reports",
    ):
        """
        Execute complete pipeline.
        """

        #
        # Backup first
        #
        from time import perf_counter

        start = perf_counter()

        logger = get_logger()

        logger.info(f"Processing: {input_file}")

        backup_path = None

        if self.config.get(
            "backup.enabled",
            True,
        ):
            backup_path = self.backup(input_file)

            print(f"Backup created: {backup_path}")

        #
        # Initialize
        #

        self.initialize(input_file)

        #
        # Execute stages
        #

        results = []

        for stage in self.stages:

            result = stage.execute(self.context)

            logger.info(f"{stage.name}: {result.changes} changes")

            results.append(result)

            if result.success:

                print(f"✓ {stage.name}: " f"{result.changes} changes")

            else:

                print(f"✗ {stage.name}: " f"{result.error}")

        #
        # Export output
        #

        exporter = ReportExporter(
            output_directory
            or self.config.get(
                "paths.output_directory",
                "output",
            ),
            report_subdirectory=report_subdirectory,
        )

        export_result = exporter.export(
            cleaned_markdown=self.context.get_markdown(),
            source_file=input_file,
            change_log=self.context.tracker,
            output_name=output_name,
            vocabulary_candidates=self.context.metadata.get("glossary_candidates", []),
        )

        elapsed = perf_counter() - start

        return {
            "backup": backup_path,
            "stages": results,
            "output": export_result,
            "elapsed_seconds": round(
                elapsed,
                2,
            ),
        }


# -------------------------------------------------------------
# Command line execution
# -------------------------------------------------------------


if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser(description="OCR Markdown Cleanup Pipeline")

    parser.add_argument("input", help="Input markdown file")

    parser.add_argument(
        "--config", default="config.yaml", help="Pipeline configuration"
    )

    args = parser.parse_args()

    pipeline = OCRPipeline(args.config)

    result = pipeline.run(args.input)

    print("\nCompleted")

    print("\nPipeline completed.")

    print(f"Total corrections: " f"{pipeline.context.total_changes}")

    print(f"Output directory: " f"{result['output']['markdown']}")
