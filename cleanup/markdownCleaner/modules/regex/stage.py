"""
modules/regex/stage.py

OCR regex correction stage.

Runs all regex-based OCR processors.
"""

from __future__ import annotations

from ..core.stage import (
    PipelineStage,
    StageResult,
)

from .ocr_characters import (
    OCRCharacterProcessor,
)

from .broken_words import (
    BrokenWordProcessor,
)

from .hyphenation import (
    HyphenationProcessor,
)

from .repeated_characters import (
    RepeatedCharacterProcessor,
)

from .number_letter import (
    NumberLetterProcessor,
)


class RegexStage(PipelineStage):
    """
    OCR correction stage based on deterministic regex rules.
    """

    name = "RegexOCR"

    config_section = "regex"

    def __init__(
        self,
        config,
    ):

        super().__init__(config)

        self.processors = []

    # ---------------------------------------------------------

    def initialize(
        self,
        context,
    ):
        """
        Initialize processors.

        Processors require:
            - config
            - logger
            - tracker
        """

        self.processors = [
            OCRCharacterProcessor(context),
            BrokenWordProcessor(context),
            HyphenationProcessor(context),
            RepeatedCharacterProcessor(context),
        ]

    # ---------------------------------------------------------

    def process(
        self,
        context,
    ) -> StageResult:
        """
        Execute OCR correction.
        """
        if not self.processors:

            self.initialize(context)

        start_changes = context.total_changes

        #
        # Process every segment
        #

        for segment in context.iter_segments():

            if not segment.current_text.strip():
                continue
            original = segment.current_text

            for processor in self.processors:

                processor.process(segment)

        total_changes = context.total_changes - start_changes

        return StageResult(
            stage=self.name,
            changes=total_changes,
        )
