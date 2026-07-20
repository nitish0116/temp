"""
modules/unicode/processor.py

Base class for Unicode cleanup processors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from email.policy import default

from ..core.context import ProcessingContext
from ..markdown.segmenter import MarkdownSegment


class UnicodeProcessor(ABC):
    """
    Base class for every Unicode cleanup processor.

    Responsibilities
    ----------------
    * Access shared ProcessingContext
    * Record changes
    * Provide a common processing interface
    """

    #: Display name used in logs/reports
    name = "Unicode"

    def __init__(self, context: ProcessingContext):

        self.context = context
        self.config = context.config
        self.logger = context.logger
        self.tracker = context.tracker

    # ---------------------------------------------------------

    def enabled(self, key, default=True):
        unicode_config = self.config.data.get("unicode", {})

        fixes = unicode_config.get("fixes", {})

        return fixes.get(key, default)

    @abstractmethod
    def process(
        self,
        segment: MarkdownSegment,
    ) -> bool:
        """
        Process a segment.

        Returns
        -------
        bool
            True if the processor modified the segment.
        """
        raise NotImplementedError

    # ---------------------------------------------------------

    def record_change(
        self,
        *,
        segment: MarkdownSegment,
        before: str,
        after: str,
        reason: str,
        confidence: float = 100.0,
    ) -> None:
        """
        Record a change in the shared ChangeTracker.
        """

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
        )
