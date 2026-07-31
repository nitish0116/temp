"""
modules/regex/repeated_characters.py

Fix standalone repeated-character OCR noise.

Examples:

    aaaa  -> a
    bbbb  -> b

Internal runs such as ``helllo`` are deliberately left for dictionary-backed
correction because reducing them without lexical evidence can damage valid
words. Standalone valid Roman numerals and configured protected vocabulary are
also retained.

"""

from __future__ import annotations

import re

from ..markdown.segmenter import MarkdownSegment
from ..symspell.dictionary import DictionaryManager

from .processor import RegexProcessor

from .constants import (
    REPEATED_CHARACTER_PATTERN,
    REPEATED_CHARACTER_CONFIDENCE,
)

ROMAN_NUMERAL_PATTERN = re.compile(
    r"M{0,3}(?:CM|CD|D?C{0,3})"
    r"(?:XC|XL|L?X{0,3})"
    r"(?:IX|IV|V?I{0,3})"
)


class RepeatedCharacterProcessor(RegexProcessor):
    """Reduce accidental OCR duplication without damaging protected tokens.

    Example:
        ``instance = RepeatedCharacterProcessor(context)``
        Expected behavior: Reduce accidental OCR character duplication.
    """

    name = "RepeatedCharacters"

    def __init__(self, context) -> None:
        super().__init__(context)
        self._protected_vocabulary: DictionaryManager | None = None

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

        count = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal count
            if self._should_preserve(match.group(0)):
                return match.group(0)
            count += 1
            return self._reduce_repeat(match)

        after = REPEATED_CHARACTER_PATTERN.sub(replace, before)

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

    def _should_preserve(self, token: str) -> bool:
        """Keep valid Roman numerals and explicitly protected vocabulary."""

        if token.isupper() and ROMAN_NUMERAL_PATTERN.fullmatch(token):
            return True
        return self._protected_terms().is_protected(token)

    def _protected_terms(self) -> DictionaryManager:
        """Load configured protected terms once, without the main dictionary."""

        if self._protected_vocabulary is not None:
            return self._protected_vocabulary

        manager = DictionaryManager(
            glossary_path=self.config.resolve_path(
                self.config.get("symspell.glossary")
            ),
            learned_path=self.config.resolve_path(
                self.config.get("symspell.learned")
            ),
        )
        manager.load()
        for entry in self.config.get("symspell.protected", []) or []:
            manager.protect_entry(str(entry))
        self._protected_vocabulary = manager
        return manager

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
