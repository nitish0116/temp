"""
modules/regex/processor.py

Base class for OCR regex processors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.context import ProcessingContext
from ..markdown.segmenter import MarkdownSegment


class RegexProcessor(ABC):
    """Base interface and reporting support for deterministic regex processors.

    Example:
        ``instance = RegexProcessor(context)``
        Expected behavior: Base interface and reporting support for deterministic regex processors.
    """

    name = "Regex"

    def __init__(
        self,
        context: ProcessingContext,
    ):
        """Bind shared configuration, logging, and change-tracking services.

        Example:
            ``instance = RegexProcessor(context)``
            Expected behavior: Bind shared configuration, logging, and change-tracking services.
        """

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
        """Process one text segment.

        Returns:
            True if text changed.

        Example:
            ``result = instance.process(segment)``
            Expected behavior: Process one text segment.
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
        """Record a non-empty segment transformation in the shared change log.

        Example:
            ``instance.record_change(segment=segment, before="teh", after="the", reason="Safe correction", confidence=98.0)``
            Expected behavior: Record a non-empty segment transformation in the shared change log.
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
