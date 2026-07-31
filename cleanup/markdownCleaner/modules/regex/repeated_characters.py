"""
modules/regex/repeated_characters.py

Fix standalone repeated-character OCR noise.

Examples:

    aaaa  -> a
    bbbb  -> b

Internal runs such as ``helllo`` are deliberately left for dictionary-backed
correction because reducing them without lexical evidence can damage valid
words.

"""

from __future__ import annotations

import re

from ..markdown.segmenter import MarkdownSegment

from .processor import RegexProcessor

from .constants import (
    REPEATED_CHARACTER_PATTERN,
    REPEATED_CHARACTER_CONFIDENCE,
)


class RepeatedCharacterProcessor(RegexProcessor):
    """Reduce accidental OCR character duplication.

    Example:
        ``instance = RepeatedCharacterProcessor(context)``
        Expected behavior: Reduce accidental OCR character duplication.
    """

    name = "RepeatedCharacters"

    # ---------------------------------------------------------

    def process(
        self,
        segment: MarkdownSegment,
    ) -> bool:
        """Remove excessive repeated characters.

        Returns:
            True if changed.

        Example:
            ``result = instance.process(segment)``
            Expected behavior: Remove excessive repeated characters.
        """

        before = segment.current_text

        if not before or not self.correction_enabled("repeated_characters"):

            return False

        after, count = REPEATED_CHARACTER_PATTERN.subn(
            self._reduce_repeat,
            before,
        )

        if count == 0:

            return False

        segment.current_text = after

        self.record_change(
            segment=segment,
            before=before,
            after=after,
            reason="OCR repeated character correction",
            confidence=REPEATED_CHARACTER_CONFIDENCE,
        )

        self.context.increment(
            "repeated_characters_fixed",
            count,
        )

        return True

    # ---------------------------------------------------------

    def _reduce_repeat(
        self,
        match: re.Match,
    ) -> str:
        """Convert:

            aaa

        into:

            a

        Example:
            ``result = instance._reduce_repeat(match)``
            Expected behavior: Convert:.
        """

        character = match.group(1)

        return character
