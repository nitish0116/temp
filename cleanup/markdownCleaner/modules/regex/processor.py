"""
modules/regex/processor.py

Base class for OCR regex processors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.context import ProcessingContext
from ..markdown.segmenter import MarkdownSegment


class RegexProcessor(ABC):

    name = "Regex"

    def __init__(
        self,
        context: ProcessingContext,
    ):

        self.context = context

        self.config = context.config

        self.logger = context.logger

        self.tracker = context.tracker

    # ------------------------------------------------------

    @abstractmethod
    def process(
        self,
        segment: MarkdownSegment,
    ) -> bool:
        """
        Process one text segment.

        Returns:
            True if text changed.
        """

        raise NotImplementedError

    # ------------------------------------------------------

    def record_change(
        self,
        *,
        segment: MarkdownSegment,
        before: str,
        after: str,
        reason: str,
        confidence: float,
    ):

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
