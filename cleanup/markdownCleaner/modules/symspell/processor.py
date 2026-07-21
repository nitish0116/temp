"""
modules/symspell/processor.py

Base SymSpell correction processor.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..markdown.segmenter import MarkdownSegment

from ..core.context import ProcessingContext


class SymSpellProcessor(ABC):
    """Base class for dictionary-based correction.

    Example:
        ``instance = SymSpellProcessor(context)``
        Expected behavior: Base class for dictionary-based correction.
    """

    name = "SymSpell"

    def __init__(
        self,
        context: ProcessingContext,
    ):
        """Bind shared context and construct the configured SymSpell engine.

        Example:
            ``instance = SymSpellProcessor(context)``
            Expected behavior: Bind shared context and construct the configured SymSpell engine.
        """

        self.context = context

        self.config = context.config

        self.logger = context.logger

        self.tracker = context.tracker

    # ---------------------------------------------------------

    @abstractmethod
    def process(
        self,
        segment: MarkdownSegment,
    ) -> bool:
        """Process a text segment.

        Returns:
            True if modified.

        Example:
            ``result = instance.process(segment)``
            Expected behavior: Process a text segment.
        """

        raise NotImplementedError

    # ---------------------------------------------------------

    def record_change(
        self,
        *,
        segment,
        before,
        after,
        confidence,
        reason,
    ):
        """Record dictionary correction.

        Example:
            ``instance.record_change(segment=segment, before="teh", after="the", confidence=98.0, reason="Safe correction")``
            Expected behavior: Record dictionary correction.
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
