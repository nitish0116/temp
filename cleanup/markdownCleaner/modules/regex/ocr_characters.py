"""
modules/regex/ocr_characters.py

Compatibility interface for the former OCR character processor.

Digit/letter correction now has one implementation in NumberLetterProcessor.
RegexStage registers that processor directly; this subclass remains available
for callers that imported OCRCharacterProcessor in earlier releases.
"""

from __future__ import annotations

import re

from .constants import OCR_CHARACTER_REPLACEMENTS
from .number_letter import NumberLetterProcessor


class OCRCharacterProcessor(NumberLetterProcessor):
    """Backward-compatible name delegating to NumberLetterProcessor."""

    name = "OCRCharacters"

    def _replace_number_letters(
        self,
        text: str,
    ) -> str:
        """Delegate the historical helper to the sole digit implementation."""

        return self._process_words(text)

    def _replace_character_patterns(
        self,
        text: str,
    ) -> str:
        """Retain the legacy alphabet-confusion helper for API compatibility."""

        result = text

        for old, new in OCR_CHARACTER_REPLACEMENTS.items():
            pattern = re.compile(rf"\b\w*{re.escape(old)}\w*\b")

            def replace(match: re.Match[str]) -> str:
                word = match.group(0)
                return word if len(word) < 4 else word.replace(old, new)

            result = pattern.sub(replace, result)

        return result
