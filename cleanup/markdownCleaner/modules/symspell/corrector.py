"""Conservative single-token spelling correction."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import re

from .candidate import CorrectionCandidate
from .dictionary import DictionaryManager
from .engine import SymSpellEngine
from .settings import SymSpellSettings
from .tokens import WORD_PATTERN as TOKEN_WORD_PATTERN
from ..markdown.segmenter import split_protected_spans


@dataclass(frozen=True, slots=True)
class SpellCorrection:
    """An accepted, auditable spelling correction."""

    original: str
    replacement: str
    confidence: float
    reason: str
    distance: int
    frequency: int


class SpellCorrector:
    """Filter SymSpell candidates and preserve source capitalization."""

    WORD_PATTERN = TOKEN_WORD_PATTERN
    INLINE_CODE_PATTERN = re.compile(r"(`+)[^\n]*?\1")

    def __init__(
        self,
        dictionary: DictionaryManager,
        engine: SymSpellEngine,
        settings: SymSpellSettings,
    ) -> None:
        self.dictionary = dictionary
        self.engine = engine
        self.settings = settings

    def process_text(
        self,
        text: str,
        on_correction: Callable[[SpellCorrection], None] | None = None,
        *,
        threshold: float | None = None,
    ) -> str:
        """Correct prose tokens while leaving inline-code spans untouched."""

        return "".join(
            span.text
            if span.protected
            else self._process_prose(
                span.text,
                on_correction,
                threshold,
            )
            for span in split_protected_spans(text)
        )

    def _process_prose(
        self,
        text: str,
        on_correction: Callable[[SpellCorrection], None] | None,
        threshold: float | None,
    ) -> str:
        def replace(match: re.Match[str]) -> str:
            correction = self.correct_word(
                match.group(0),
                threshold=threshold,
            )
            if correction is None:
                return match.group(0)
            if on_correction is not None:
                on_correction(correction)
            return correction.replacement

        return self.WORD_PATTERN.sub(replace, text)

    def correct_word(
        self,
        word: str,
        *,
        threshold: float | None = None,
    ) -> SpellCorrection | None:
        """Return an accepted correction, or None when the token is unsafe."""

        if self.dictionary.contains(word) or self.dictionary.is_protected(word):
            return None
        if len(word) < self.settings.minimum_word_length:
            return None
        if (
            "-" in word
            or "'" in word
            or "\u2019" in word
            or "\u00e2\u20ac\u2122" in word
        ):
            return None
        if word.isupper() and len(word) > 1:
            return None
        if any(character.isupper() for character in word[1:]):
            return None

        candidates = self.filter_candidates(
            word,
            self.generate_candidates(word),
        )
        if not candidates:
            return None

        candidates.sort(
            key=lambda candidate: (
                -candidate.confidence,
                -candidate.frequency,
                candidate.corrected,
            )
        )
        best = candidates[0]
        required_confidence = (
            self.settings.confidence_threshold
            if threshold is None
            else threshold
        )
        if not best.is_safe(required_confidence):
            return None
        if (
            len(candidates) > 1
            and (best.confidence - candidates[1].confidence)
            < self.settings.ambiguity_margin
        ):
            return None

        replacement = self.match_case(word, best.corrected)
        return SpellCorrection(
            original=word,
            replacement=replacement,
            confidence=best.confidence,
            reason=(
                "Safe SymSpell correction "
                f"(distance={best.distance}, frequency={best.frequency})"
            ),
            distance=best.distance,
            frequency=best.frequency,
        )

    def filter_candidates(
        self,
        word: str,
        candidates: Iterable[CorrectionCandidate],
    ) -> list[CorrectionCandidate]:
        """Apply distance, frequency, and plural-safety policies."""

        accepted = [
            candidate
            for candidate in candidates
            if (
                candidate.distance
                <= self.settings.maximum_auto_edit_distance
                and candidate.frequency
                >= self.settings.minimum_candidate_frequency
            )
        ]
        lowered = word.lower()
        if lowered.endswith("s") and len(lowered) > 3:
            accepted = [
                candidate
                for candidate in accepted
                if candidate.corrected.lower() != lowered[:-1]
            ]
        return accepted

    def generate_candidates(self, word: str) -> list[CorrectionCandidate]:
        """Return candidates from the configured lookup engine."""

        return self.engine.lookup(word)

    @staticmethod
    def match_case(original: str, corrected: str) -> str:
        """Transfer uppercase or title-case style to a correction."""

        if original.isupper():
            return corrected.upper()
        if original[:1].isupper() and original[1:].islower():
            return corrected.capitalize()
        return corrected
