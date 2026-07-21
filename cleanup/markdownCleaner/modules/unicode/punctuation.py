"""
modules/unicode/punctuation.py

Normalize Unicode punctuation characters commonly found
in OCR/PDF extracted text.
"""

from __future__ import annotations

from ..markdown.segmenter import MarkdownSegment

from .processor import UnicodeProcessor

from .constants import (
    QUOTE_TRANSLATION,
    DASH_TRANSLATION,
    ELLIPSIS_TRANSLATION,
)


class PunctuationProcessor(UnicodeProcessor):
    """
    Normalize Unicode punctuation.

    Examples:

        “Hello”  -> "Hello"

        don’t    -> don't

        word—word -> word-word

        wait…    -> wait...
    """

    name = "Punctuation"

    def process(
        self,
        segment: MarkdownSegment,
    ) -> bool:
        """Normalize punctuation.

        Returns
        -------
        bool
            True if text changed.

        Example:
            ``result = instance.process(segment)``
            Expected behavior: Normalize punctuation.
        """

        before = segment.current_text

        if not before:

            return False

        after = before

        #
        # Quotes
        #

        if self.enabled(
            "normalize_quotes",
            True,
        ):

            after = after.translate(QUOTE_TRANSLATION)

        #
        # Dashes
        #

        if self.enabled(
            "normalize_dashes",
            True,
        ):

            after = after.translate(DASH_TRANSLATION)

        #
        # Ellipsis
        #

        if self.enabled(
            "normalize_ellipsis",
            True,
        ):

            after = after.translate(ELLIPSIS_TRANSLATION)

        if before == after:

            return False

        segment.current_text = after

        self.record_change(
            segment=segment,
            before=before,
            after=after,
            reason="Unicode punctuation normalization",
            confidence=100.0,
        )

        self.context.increment("punctuation_normalized")

        return True
