"""
modules/regex/ocr_characters.py

OCR character confusion processor.

Handles common OCR substitutions:

    rn -> m
    vv -> w

and contextual number/letter confusion:

    l0ve -> love
    1ife -> life

"""

from __future__ import annotations

import re

from ..markdown.segmenter import MarkdownSegment

from .processor import RegexProcessor

from .constants import (
    OCR_CHARACTER_REPLACEMENTS,
    OCR_CHARACTER_CONFIDENCE,
    ZERO_IN_WORD_PATTERN,
    ONE_IN_WORD_PATTERN,
    FIVE_IN_WORD_PATTERN,
    EIGHT_IN_WORD_PATTERN,
    NUMBER_LETTER_REPLACEMENTS,
    NUMBER_LETTER_CONFIDENCE,
)


class OCRCharacterProcessor(RegexProcessor):
    """
    Fix character-level OCR errors.
    """

    name = "OCRCharacters"

    # ---------------------------------------------------------

    def process(
        self,
        segment: MarkdownSegment,
    ) -> bool:
        """
        Apply OCR character corrections.

        Returns:
            True if text changed.
        """

        before = segment.current_text

        if not before:

            return False

        after = before

        #
        # Alphabet confusion
        #

        after = self._replace_character_patterns(after)

        #
        # Number-letter confusion
        #

        after = self._replace_number_letters(after)

        if before == after:

            return False

        segment.current_text = after

        self.record_change(
            segment=segment,
            before=before,
            after=after,
            reason="OCR character correction",
            confidence=90.0,
        )

        self.context.increment("ocr_character_fixed")

        return True

    # ---------------------------------------------------------

    def _replace_character_patterns(
        self,
        text: str,
    ) -> str:
        """
        Replace common OCR alphabet confusion.
        """

        result = text

        for old, new in OCR_CHARACTER_REPLACEMENTS.items():

            pattern = re.compile(rf"\b\w*{old}\w*\b")

            def replace(match):

                word = match.group(0)

                #
                # Avoid tiny words.
                #

                if len(word) < 4:

                    return word

                return word.replace(
                    old,
                    new,
                )

            result = pattern.sub(
                replace,
                result,
            )

        return result

    # ---------------------------------------------------------

    def _replace_number_letters(
        self,
        text: str,
    ) -> str:
        """
        Replace digits appearing inside words.

        Examples:

            l0ve -> love

            1ife -> life
        """

        replacements = [
            (
                ZERO_IN_WORD_PATTERN,
                "0",
                "o",
            ),
            (
                ONE_IN_WORD_PATTERN,
                "1",
                "l",
            ),
            (
                FIVE_IN_WORD_PATTERN,
                "5",
                "s",
            ),
            (
                EIGHT_IN_WORD_PATTERN,
                "8",
                "b",
            ),
        ]

        result = text

        for pattern, old, new in replacements:

            result = pattern.sub(
                new,
                result,
            )

        return result
