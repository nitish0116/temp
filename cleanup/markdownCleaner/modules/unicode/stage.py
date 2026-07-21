"""
modules/unicode/stage.py

Unicode cleanup stage.

Runs all Unicode processors in a controlled order.
"""

from __future__ import annotations

from ..core.stage import PipelineStage, StageResult

from .normalizer import (
    UnicodeNormalizer,
)

from .invisible import (
    InvisibleProcessor,
)

from .ligatures import (
    LigatureProcessor,
)

from .whitespace import (
    WhitespaceProcessor,
)

from .punctuation import (
    PunctuationProcessor,
)


class UnicodeStage(PipelineStage):
    """Normalize Unicode safely across every editable segment.

    Processor order is significant: normalize canonical forms first, remove
    invisible controls, expand ligatures, regularize whitespace, and finally
    normalize punctuation. Each processor records its own transformations in
    the shared tracker.

    Example:
        ``instance = UnicodeStage(config)``
        Expected behavior: Normalize Unicode safely across every editable segment.
    """

    name = "Unicode"

    config_section = "unicode"

    def __init__(
        self,
        config,
    ):
        """Initialize the Unicode stage before processors are constructed.

        Example:
            ``instance = UnicodeStage(config)``
            Expected behavior: Initialize the Unicode stage before processors are constructed.
        """

        super().__init__(config)

        self.processors = []

    # ---------------------------------------------------------

    def initialize(
        self,
        context,
    ):
        """Construct Unicode processors bound to the active context.

        Binding is deferred because processors use the context's logger, change
        tracker, and configuration. The list order defines the cleanup workflow.

        Example:
            ``instance.initialize(context)``
            Expected behavior: Construct Unicode processors bound to the active context.
        """

        self.processors = [
            UnicodeNormalizer(context),
            InvisibleProcessor(context),
            LigatureProcessor(context),
            WhitespaceProcessor(context),
            PunctuationProcessor(context),
        ]

    # ---------------------------------------------------------

    def process(
        self,
        context,
    ) -> StageResult:
        """Run the ordered Unicode workflow over every editable segment.

        Returns:
            A result containing the number of transformations logged during
            this stage, excluding changes made by earlier stages.

        Example:
            ``result = instance.process(context)``
            Expected behavior: Run the ordered Unicode workflow over every editable segment.
        """

        if not self.processors:

            self.initialize(context)

        start_changes = context.total_changes

        #
        # Process every segment
        #

        for segment in context.iter_segments():

            for processor in self.processors:

                processor.process(segment)

        total_changes = context.total_changes - start_changes

        return StageResult(
            stage=self.name,
            changes=total_changes,
        )
