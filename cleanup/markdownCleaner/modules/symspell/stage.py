"""Conservative dictionary-based OCR correction stage."""
from __future__ import annotations

from collections import Counter
import re

from .engine import SymSpellEngine
from ..core.stage import PipelineStage, StageResult
from .dictionary import DictionaryManager


class SymSpellStage(PipelineStage):
    """Correct likely OCR misspellings while protecting fiction vocabulary."""

    name = "SymSpell"
    config_section = "symspell"
    WORD_PATTERN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+|-[A-Za-z]+)*")

    def __init__(self, config):
        super().__init__(config)
        self.dictionary: DictionaryManager | None = None
        self.engine: SymSpellEngine | None = None

    def initialize(self, context) -> None:
        self.context = context
        self.dictionary = DictionaryManager(
            dictionary_path=context.config.resolve_path(
                context.config.get("symspell.dictionary", "builtin:en-82k")
            ),
            glossary_path=context.config.resolve_path(context.config.get("symspell.glossary")),
            learned_path=context.config.resolve_path(context.config.get("symspell.learned")),
        )
        self.dictionary.load()

        # Explicit protected terms from config.
        for word in context.config.get("symspell.protected", []) or []:
            self.dictionary.protect(str(word))

        # Protect repeated unknown proper nouns / mixed-case terms discovered in
        # this book. This is important for names such as Momonga and HeroHero.
        if context.config.get("symspell.auto_protect_proper_nouns", True):
            self._protect_document_terms(context.current_markdown)

        self.engine = SymSpellEngine(
            max_edit_distance=int(context.config.get("symspell.max_edit_distance", 2))
        )
        minimum_dictionary_frequency = int(
            context.config.get("symspell.minimum_dictionary_frequency", 1)
        )
        for word, frequency in self.dictionary.words.items():
            if frequency >= minimum_dictionary_frequency:
                self.engine.add_word(word, frequency)

    def _protect_document_terms(self, text: str) -> None:
        assert self.dictionary is not None
        min_occurrences = int(self.config.get("symspell.proper_noun_min_occurrences", 2))
        tokens = self.WORD_PATTERN.findall(text)
        counts = Counter(tokens)
        for token, count in counts.items():
            if count < min_occurrences or self.dictionary.contains(token):
                continue
            # Protect CamelCase/mixed-case and repeated TitleCase unknown words.
            mixed_case = any(ch.isupper() for ch in token[1:])
            title_case = token[:1].isupper() and token[1:].islower()
            if mixed_case or title_case:
                self.dictionary.protect(token)

    def process(self, context) -> StageResult:
        if self.dictionary is None or self.engine is None:
            self.initialize(context)

        start_changes = context.total_changes
        threshold = float(self.get_config("confidence_threshold", 92))
        for segment in context.iter_segments():
            segment.current_text = self._process_text(segment, threshold)
        return StageResult(stage=self.name, changes=context.total_changes - start_changes)

    def _process_text(self, segment, threshold: float) -> str:
        return self.WORD_PATTERN.sub(
            lambda match: self._correct_word(match.group(0), segment, threshold),
            segment.current_text,
        )

    def _correct_word(self, word: str, segment, threshold: float) -> str:
        assert self.dictionary is not None
        if self.dictionary.contains(word) or self.dictionary.is_protected(word):
            return word

        min_length = int(self.get_config("minimum_word_length", 4))
        if len(word) < min_length:
            return word

        # Be conservative with punctuation compounds, acronyms and mixed-case
        # identifiers/names. They are much more likely to be legitimate fiction
        # vocabulary than OCR errors.
        if "-" in word or "'" in word or "’" in word:
            return word
        if word.isupper() and len(word) > 1:
            return word
        if any(ch.isupper() for ch in word[1:]):
            return word

        candidates = self._generate_candidates(word)
        if not candidates:
            return word

        max_auto_distance = int(self.get_config("max_auto_edit_distance", 1))
        min_frequency = int(self.get_config("minimum_candidate_frequency", 1000))
        candidates = [
            c for c in candidates
            if c.distance <= max_auto_distance and c.frequency >= min_frequency
        ]
        # Specialist dictionaries often contain a singular but omit its regular
        # plural. A trailing plural "s" is not an OCR error: for example,
        # "noncoms" must not be changed to "noncom".
        lowered = word.lower()
        if lowered.endswith("s") and len(lowered) > 3:
            candidates = [
                candidate
                for candidate in candidates
                if candidate.corrected.lower() != lowered[:-1]
            ]
        if not candidates:
            return word

        candidates.sort(key=lambda c: (-c.confidence, -c.frequency, c.corrected))
        best = candidates[0]
        if not best.is_safe(threshold):
            return word

        # Reject ambiguous corrections where the runner-up is almost as good.
        ambiguity_margin = float(self.get_config("ambiguity_margin", 2.0))
        if len(candidates) > 1 and (best.confidence - candidates[1].confidence) < ambiguity_margin:
            return word

        corrected = self._match_case(word, best.corrected)
        self.record_change(
            segment=segment,
            before=word,
            after=corrected,
            confidence=best.confidence,
            reason=f"Safe SymSpell correction (distance={best.distance}, frequency={best.frequency})",
        )
        return corrected

    @staticmethod
    def _match_case(original: str, corrected: str) -> str:
        if original.isupper():
            return corrected.upper()
        if original[:1].isupper() and original[1:].islower():
            return corrected.capitalize()
        return corrected

    def _generate_candidates(self, word: str):
        if self.engine is None:
            return []
        return self.engine.lookup(word)
