"""Shared contract for processors that edit one Markdown segment."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import ProcessingContext
    from ..markdown.segmenter import MarkdownSegment


class SegmentProcessor(ABC):
    """Bind shared services and audit segment-level text transformations.

    Stage packages can extend this class with package-specific configuration
    helpers while sharing one processing and change-recording contract.
    """

    name = "Processor"

    def __init__(self, context: ProcessingContext) -> None:
        self.context = context
        self.config = context.config
        self.logger = context.logger
        self.tracker = context.tracker

    @abstractmethod
    def process(self, segment: MarkdownSegment) -> bool:
        """Process one segment and return whether its text changed."""

        raise NotImplementedError

    def record_change(
        self,
        *,
        segment: MarkdownSegment,
        before: str,
        after: str,
        reason: str,
        confidence: float = 100.0,
        broken_word: str | None = None,
    ) -> None:
        """Append a non-empty transformation to the shared change log."""

        if before == after:
            return

        self.tracker.add(
            stage=self.name,
            block_index=segment.block_index,
            segment_index=segment.segment_index,
            line=segment.start_line,
            before=before,
            after=after,
            confidence=confidence,
            reason=reason,
            broken_word=broken_word,
        )
