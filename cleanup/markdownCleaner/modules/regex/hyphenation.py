"""
modules/regex/hyphenation.py

Fix OCR/PDF line-break hyphenation.

Examples:

    inter-
    national

becomes:

    international
"""

from __future__ import annotations

import re

from ..markdown.segmenter import MarkdownSegment

from .processor import RegexProcessor

from .constants import (
    HYPHENATION_PATTERN,
    HYPHENATION_CONFIDENCE,
)


class HyphenationProcessor(RegexProcessor):
    """
    Remove artificial line-break hyphenation.
    """

    name = "Hyphenation"

    # ---------------------------------------------------------

    def process(
        self,
        segment: MarkdownSegment,
    ) -> bool:
        """
        Repair broken words across lines.

        Returns:
            True if text changed.
        """

        before = segment.current_text

        if not before:

            return False

        after, count = HYPHENATION_PATTERN.subn(
            self._merge_words,
            before,
        )

        if count == 0:

            return False

        segment.current_text = after

        self.record_change(
            segment=segment,
            before=before,
            after=after,
            reason="OCR line-break hyphenation repair",
            confidence=HYPHENATION_CONFIDENCE,
        )

        self.context.increment(
            "hyphenations_fixed",
            count,
        )

        return True

    # ---------------------------------------------------------

    def _merge_words(
        self,
        match: re.Match,
    ) -> str:
        """
        Join the two halves of a broken word.
        """

        first = match.group(1)

        second = match.group(2)

        return first + second
