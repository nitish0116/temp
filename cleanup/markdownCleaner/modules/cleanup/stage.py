"""Legacy segment-oriented cleanup stage."""

from __future__ import annotations

from ..core.context import ProcessingContext
from ..core.processor import SegmentProcessor
from ..core.stage import SegmentProcessingStage
from ..markdown.segmenter import MarkdownSegment
from .image_text import ImageTextProcessor
from .markdown import MarkdownProcessor


class NovelCleanupStage(SegmentProcessingStage):
    """Apply image-text and inline-Markdown cleanup to editable segments.

    This compatibility stage remains available to direct callers. The main
    pipeline uses the whole-document :class:`DocumentCleanupStage` instead.
    """

    name = "NovelCleanup"
    config_section = "cleanup"

    def build_processors(
        self,
        context: ProcessingContext,
    ) -> list[SegmentProcessor]:
        return [
            ImageTextProcessor(context),
            MarkdownProcessor(context),
        ]

    def _process_segment(self, segment: MarkdownSegment) -> None:
        """Let cleanup processors see complete converter markup boundaries.

        The generic segment stage protects every inline HTML span before
        processors run. This compatibility stage intentionally consumes
        picture comments, ``<br>``, and ``<u>`` markup, so its processors
        protect literal Markdown internally and then inspect the full segment.
        """
        for processor in self.processors:
            processor.process(segment)
