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
    """
    Complete Unicode cleanup pipeline.
    """

    name = "Unicode"

    config_section = "unicode"

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
        Create processors after context exists.

        Processors need access to:
            - logger
            - tracker
            - config
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
        """
        Execute Unicode cleanup.

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
