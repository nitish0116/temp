"""
modules/regex/broken_words.py

Fix OCR-created spaces inside words.

Examples:

    some one     -> someone
    every thing  -> everything

"""

from __future__ import annotations

import re

from ..markdown.segmenter import MarkdownSegment

from .processor import RegexProcessor

from .constants import (
    BROKEN_WORD_PATTERNS,
)


class BrokenWordProcessor(RegexProcessor):
    """
    Merge words incorrectly separated by OCR.
    """

    name = "BrokenWords"

    # ---------------------------------------------------------

    def process(
        self,
        segment: MarkdownSegment,
    ) -> bool:
        """
        Fix broken words.

        Returns:
            True if changes occurred.
        """

        before = segment.current_text

        if not before:

            return False

        after = before

        total_confidence = 0.0

        changes = 0

        for pattern, replacement, confidence in BROKEN_WORD_PATTERNS:

            new_text, count = pattern.subn(
                replacement,
                after,
            )

            if count:

                changes += count

                total_confidence += confidence * count

                after = new_text

        if before == after:

            return False

        segment.current_text = after

        average_confidence = total_confidence / changes if changes else 80.0

        self.record_change(
            segment=segment,
            before=before,
            after=after,
            reason="OCR broken word merge",
            confidence=average_confidence,
        )

        self.context.increment(
            "broken_words_fixed",
            changes,
        )

        return True
