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
    PUNCTUATION_TRANSLATION,
)


class PunctuationProcessor(UnicodeProcessor):
    """
    Normalize Unicode punctuation.

    Examples:

        “Hello”  -> "Hello"

        don’t    -> don't

        word―word -> word—word

        1‒2 -> 1–2

        wait…    -> wait...

    Word hyphens, range dashes, sentence dashes, and mathematical minus signs
    remain distinct so later dehyphenation and narration can interpret them
    correctly.
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

        if not self.enabled("punctuation", True):
            return False

        before = segment.current_text
        if not before:

            return False

        normalize_quotes = self.enabled("normalize_quotes", True)
        normalize_dashes = self.enabled("normalize_dashes", True)
        normalize_ellipsis = self.enabled("normalize_ellipsis", True)

        if normalize_quotes and normalize_dashes and normalize_ellipsis:
            after = before.translate(PUNCTUATION_TRANSLATION)
        else:
            after = before
            if normalize_quotes:
                after = after.translate(QUOTE_TRANSLATION)
            if normalize_dashes:
                after = after.translate(DASH_TRANSLATION)
            if normalize_ellipsis:
                after = after.translate(ELLIPSIS_TRANSLATION)

        return self.apply_change(
            segment=segment,
            before=before,
            after=after,
            reason="Unicode punctuation normalization",
            statistic="punctuation_normalized",
            confidence=100.0,
        )
