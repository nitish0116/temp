"""
modules/regex/number_letter.py

Fix OCR number/letter confusion.

Examples:

    l0ve   -> love
    1ife   -> life
    5word  -> sword

Avoids:

    Volume 10
    Chapter 1
    R2D2

"""

from __future__ import annotations

import re

from ..markdown.segmenter import MarkdownSegment

from .processor import RegexProcessor


class NumberLetterProcessor(RegexProcessor):
    """
    Replace digits incorrectly recognized as letters.
    """

    name = "NumberLetter"

    # OCR digit substitutions
    REPLACEMENTS = {
        "0": "o",
        "1": "l",
        "5": "s",
        "8": "b",
    }

    CONFIDENCE = {
        "0": 85.0,
        "1": 80.0,
        "5": 75.0,
        "8": 75.0,
    }

    # ---------------------------------------------------------

    def process(
        self,
        segment: MarkdownSegment,
    ) -> bool:
        """
        Replace numbers inside words.

        Returns:
            True if changed.
        """

        before = segment.current_text

        if not before:

            return False

        after = self._process_words(before)

        if before == after:

            return False

        segment.current_text = after

        self.record_change(
            segment=segment,
            before=before,
            after=after,
            reason="OCR number-letter correction",
            confidence=80.0,
        )

        self.context.increment("number_letter_fixed")

        return True

    # ---------------------------------------------------------

    def _process_words(
        self,
        text: str,
    ) -> str:
        """
        Process only alphabetic word tokens.
        """

        words = text.split()

        result = []

        for word in words:

            result.append(self._fix_word(word))

        return " ".join(result)

    # ---------------------------------------------------------

    def _fix_word(
        self,
        word: str,
    ) -> str:
        """
        Fix digits appearing inside a word.

        """

        if not self._contains_letters_and_digits(word):

            return word

        fixed = word

        for digit, letter in self.REPLACEMENTS.items():

            fixed = fixed.replace(
                digit,
                letter,
            )

        return fixed

    # ---------------------------------------------------------

    def _contains_letters_and_digits(
        self,
        word: str,
    ) -> bool:
        """
        Check whether token contains both
        letters and digits.
        """

        has_letter = bool(
            re.search(
                r"[A-Za-z]",
                word,
            )
        )

        has_digit = bool(
            re.search(
                r"\d",
                word,
            )
        )

        return has_letter and has_digit
