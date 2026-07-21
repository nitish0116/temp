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
    """Apply deterministic OCR corrections before dictionary-based guessing.

    The workflow fixes configured character confusions, joins recognizable
    broken words, resolves line-break hyphenation, and collapses suspicious
    repeated characters. Empty segments are skipped. Running these predictable
    corrections before SymSpell gives the probabilistic stage cleaner input.

    Example:
        ``instance = RegexStage(config)``
        Expected behavior: Apply deterministic OCR corrections before dictionary-based guessing.
    """

    name = "RegexOCR"

    config_section = "regex"

    def __init__(
        self,
        config,
    ):
        """Initialize the regex stage before its processors are constructed.

        Example:
            ``instance = RegexStage(config)``
            Expected behavior: Initialize the regex stage before its processors are constructed.
        """

        super().__init__(config)

        self.processors = []

    # ---------------------------------------------------------

    def initialize(
        self,
        context,
    ):
        """Construct regex processors in their required execution order.

        Each processor receives the active context so it can read configuration
        and record exact before/after values in the common audit trail.

        Example:
            ``instance.initialize(context)``
            Expected behavior: Construct regex processors in their required execution order.
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
        """Run each deterministic OCR processor over nonempty segments.

        Returns:
            A result containing only the changes logged during this stage.

        Example:
            ``result = instance.process(context)``
            Expected behavior: Run each deterministic OCR processor over nonempty segments.
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
