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

from .constants import (
    NUMBER_LETTER_CONFIDENCE,
    NUMBER_LETTER_CONFIG_KEYS,
    NUMBER_LETTER_REPLACEMENTS,
)
from .processor import RegexProcessor


class NumberLetterProcessor(RegexProcessor):
    """Replace digits incorrectly recognized as letters.

    Example:
        ``instance = NumberLetterProcessor(context)``
        Expected behavior: Replace digits incorrectly recognized as letters.
    """

    name = "NumberLetter"

    REPLACEMENTS = NUMBER_LETTER_REPLACEMENTS

    CONFIDENCE = NUMBER_LETTER_CONFIDENCE

    TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]+(?![A-Za-z0-9])")

    # ---------------------------------------------------------

    def process(
        self,
        segment: MarkdownSegment,
    ) -> bool:
        """Replace numbers inside words.

        Returns:
            True if changed.

        Example:
            ``result = instance.process(segment)``
            Expected behavior: Replace numbers inside words.
        """

        before = segment.current_text

        if not before:

            return False

        after, corrected_digits = self._transform(before)

        if before == after:

            return False

        segment.current_text = after

        self.record_change(
            segment=segment,
            before=before,
            after=after,
            reason="OCR number-letter correction",
            confidence=sum(
                self.CONFIDENCE[digit] for digit in corrected_digits
            )
            / len(corrected_digits),
        )

        self.context.increment(
            "number_letter_fixed",
            len(corrected_digits),
        )

        return True

    # ---------------------------------------------------------

    def _transform(
        self,
        text: str,
    ) -> tuple[str, list[str]]:
        """Correct eligible alphanumeric spans without rebuilding whitespace."""

        corrected_digits = []

        def replace(match: re.Match[str]) -> str:
            word = match.group(0)
            fixed = self._fix_word(word)

            if fixed != word:
                digit = next(character for character in word if character.isdigit())
                corrected_digits.append(digit)

            return fixed

        return self.TOKEN_PATTERN.sub(replace, text), corrected_digits

    # ---------------------------------------------------------

    def _process_words(
        self,
        text: str,
    ) -> str:
        """Process only alphabetic word tokens.

        Example:
            ``result = instance._process_words("Example text.")``
            Expected behavior: Process only alphabetic word tokens.
        """

        return self._transform(text)[0]

    # ---------------------------------------------------------

    def _fix_word(
        self,
        word: str,
    ) -> str:
        """Fix digits appearing inside a word.

        Example:
            ``result = instance._fix_word("teh")``
            Expected behavior: Fix digits appearing inside a word.
        """

        if not self._is_ocr_word_candidate(word):

            return word

        digit = next(character for character in word if character.isdigit())

        return word.replace(
            digit,
            self.REPLACEMENTS[digit],
            1,
        )

    # ---------------------------------------------------------

    def _is_ocr_word_candidate(
        self,
        word: str,
    ) -> bool:
        """Reject numeric values and identifier-like alphanumeric tokens."""

        if not self._contains_letters_and_digits(word):
            return False

        digits = [
            (index, character)
            for index, character in enumerate(word)
            if character.isdigit()
        ]

        # Multiple digits strongly indicate an identifier (R2D2, A10) rather
        # than a single OCR-confused character.
        if len(digits) != 1:
            return False

        index, digit = digits[0]
        config_key = NUMBER_LETTER_CONFIG_KEYS.get(digit)

        if config_key is None or not self.correction_enabled(config_key):
            return False

        letters = [character for character in word if character.isalpha()]

        # Preserve short/all-capital identifiers such as R2D.
        if len(letters) < 2 or all(character.isupper() for character in letters):
            return False

        # A confused digit may begin a word (1ife) or occur between letters
        # (l0ve).  A trailing digit is more likely a real version/identifier.
        if index == 0:
            return len(word) >= 3 and word[1:].isalpha()

        return (
            index < len(word) - 1
            and word[index - 1].isalpha()
            and word[index + 1].isalpha()
        )

    # ---------------------------------------------------------

    def _contains_letters_and_digits(
        self,
        word: str,
    ) -> bool:
        """Check whether token contains both
        letters and digits.

        Example:
            ``result = instance._contains_letters_and_digits("teh")``
            Expected behavior: Check whether token contains both.
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
