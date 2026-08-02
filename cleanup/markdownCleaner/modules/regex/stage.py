"""Ordered deterministic OCR correction stage."""

from __future__ import annotations

from ..core.context import ProcessingContext
from ..core.processor import SegmentProcessor
from ..core.stage import SegmentProcessingStage
from .broken_words import BrokenWordProcessor
from .number_letter import NumberLetterProcessor
from .repeated_characters import RepeatedCharacterProcessor


class RegexStage(SegmentProcessingStage):
    """Apply configured deterministic corrections before dictionary guessing."""

    name = "RegexOCR"
    config_section = "regex"
    skip_empty_segments = True

    def build_processors(
        self,
        context: ProcessingContext,
    ) -> list[SegmentProcessor]:
        """Construct only processors with at least one enabled correction."""

        number_letters = NumberLetterProcessor(context)
        broken_words = BrokenWordProcessor(context)
        repeated_characters = RepeatedCharacterProcessor(context)

        processors: list[SegmentProcessor] = []
        if any(
            number_letters.correction_enabled(key)
            for key in (
                "zero_to_o",
                "one_to_l",
                "five_to_s",
                "eight_to_b",
            )
        ):
            processors.append(number_letters)
        if broken_words.correction_enabled("broken_words"):
            processors.append(broken_words)
        # Hyphenated line breaks are resolved later by SymSpell, where both
        # joined-word and genuine-compound evidence are available.
        if repeated_characters.correction_enabled("repeated_characters"):
            processors.append(repeated_characters)
        return processors
