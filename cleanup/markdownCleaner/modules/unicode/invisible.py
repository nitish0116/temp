"""
modules/unicode/invisible.py

Remove invisible Unicode characters and unwanted control
characters introduced during OCR/PDF extraction.
"""

from __future__ import annotations

import unicodedata

from ..markdown.segmenter import MarkdownSegment

from .processor import UnicodeProcessor
from .constants import (
    ZERO_WIDTH_CHARACTERS,
    ALLOWED_CONTROL_CHARACTERS,
)


class InvisibleProcessor(UnicodeProcessor):
    """
    Remove invisible Unicode artifacts.

    Examples:

        Hello\u200bWorld
            |
            v
        HelloWorld


        \ufeffChapter 1
            |
            v
        Chapter 1
    """

    name = "InvisibleCharacters"

    # ---------------------------------------------------------

    def process(
        self,
        segment: MarkdownSegment,
    ) -> bool:
        """Remove invisible characters.

        Returns
        -------
        bool
            True if text changed.

        Example:
            ``result = instance.process(segment)``
            Expected behavior: Remove invisible characters.
        """

        before = segment.current_text

        if not before:
            return False

        after = self._clean_text(before)

        if before == after:
            return False

        segment.current_text = after

        self.record_change(
            segment=segment,
            before=before,
            after=after,
            reason="Invisible Unicode character removal",
            confidence=100.0,
        )

        self.context.increment("zero_width_removed")

        return True

    # ---------------------------------------------------------

    def _clean_text(
        self,
        text: str,
    ) -> str:
        """Remove invisible characters.

        Kept separate for easier unit testing.

        Example:
            ``result = instance._clean_text("Example text.")``
            Expected behavior: Remove invisible characters.
        """

        result = []

        removed = 0

        for char in text:

            #
            # Explicit zero-width characters
            #

            if char in ZERO_WIDTH_CHARACTERS:

                removed += 1

                continue

            #
            # Unicode category based cleanup
            #

            category = unicodedata.category(char)

            #
            # Control characters:
            #
            # Cc = control
            #

            if category == "Cc":

                if char in ALLOWED_CONTROL_CHARACTERS:

                    result.append(char)

                else:

                    removed += 1

                continue

            #
            # Format characters:
            #
            # Cf includes many invisible formatting marks
            #

            if category == "Cf":

                removed += 1

                continue

            result.append(char)

        if removed:

            self.context.increment(
                "zero_width_removed",
                removed,
            )

        return "".join(result)
