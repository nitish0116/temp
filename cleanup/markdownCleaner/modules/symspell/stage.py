"""Conservative dictionary-based OCR correction stage."""

from __future__ import annotations

from collections import Counter
import re

from .engine import SymSpellEngine
from .frequency import WordfreqScorer
from ..core.stage import PipelineStage, StageResult
from .dictionary import DictionaryManager


class SymSpellStage(PipelineStage):
    """Correct high-confidence misspellings while protecting fiction terms.

    Workflow:
        1. Load the frequency dictionary, custom glossary, and learned words.
        2. Protect explicitly configured terms and repeated proper nouns.
        3. Build the edit-distance lookup index from sufficiently frequent words.
        4. Generate candidates for unknown tokens in editable segments.
        5. Reject unsafe distance, frequency, plural, or ambiguity cases.
        6. Apply only candidates meeting the configured confidence threshold.

    For example, a frequent one-edit OCR typo may be corrected, while a name
    such as ``Degurechaff`` and a specialist plural such as ``noncoms`` remain
    unchanged. Every accepted correction is recorded with its evidence.
    """

    name = "SymSpell"
    config_section = "symspell"
    WORD_PATTERN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+|-[A-Za-z]+)*")
    DETACHED_OCR_SUFFIXES = {"tion", "tions"}
    CONTEXTUAL_COMMON_MERGES = {
        ("be", "cause"): "because",
        ("upperclass", "men"): "upperclassmen",
    }
    JOINED_OCR_CORRECTIONS = {"expressinless": "expressionless"}
    COMMON_MERGE_BLOCKING_PREVIOUS = {
        "can", "could", "may", "might", "must", "shall", "should", "will", "would"
    }

    def __init__(self, config):
        """Initialize the correction stage before dictionaries are loaded.

        Example:
            ``instance = SymSpellStage(config)``
            Expected behavior: Initialize the correction stage before dictionaries are loaded.
        """
        super().__init__(config)
        self.dictionary: DictionaryManager | None = None
        self.engine: SymSpellEngine | None = None
        self.frequency_scorer = WordfreqScorer(enabled=False)

    def initialize(self, context) -> None:
        """Load vocabularies, protect document terms, and build the lookup index.

        The dictionary sources are resolved relative to configuration. Entries
        below ``minimum_dictionary_frequency`` remain known to the manager but
        are omitted from the candidate-generating engine.

        Example:
            ``instance.initialize(context)``
            Expected behavior: Load vocabularies, protect document terms, and build the lookup index.
        """
        self.context = context
        self.dictionary = DictionaryManager(
            dictionary_path=context.config.resolve_path(
                context.config.get("symspell.dictionary", "builtin:en-82k")
            ),
            glossary_path=context.config.resolve_path(
                context.config.get("symspell.glossary")
            ),
            learned_path=context.config.resolve_path(
                context.config.get("symspell.learned")
            ),
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
        self.frequency_scorer = WordfreqScorer(
            enabled=bool(context.config.get("symspell.wordfreq_enabled", True)),
            language=str(context.config.get("symspell.wordfreq_language", "en")),
            wordlist=str(context.config.get("symspell.wordfreq_wordlist", "large")),
        )
        minimum_dictionary_frequency = int(
            context.config.get("symspell.minimum_dictionary_frequency", 1)
        )
        for word, frequency in self.dictionary.words.items():
            if frequency >= minimum_dictionary_frequency:
                self.engine.add_word(word, frequency)

    def _protect_document_terms(self, text: str) -> None:
        """Protect repeated proper nouns and mixed-case document terms.

        Example:
            ``result = instance._protect_document_terms("Example text.")``
            Expected behavior: Protect repeated proper nouns and mixed-case document terms.
        """
        assert self.dictionary is not None
        min_occurrences = int(
            self.config.get("symspell.proper_noun_min_occurrences", 2)
        )
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
        """Apply conservative dictionary corrections to editable segments.

        Known and protected words bypass candidate lookup. Accepted corrections
        preserve the source token's capitalization and increment the shared
        change tracker; rejected candidates never mutate the document.

        Example:
            ``result = instance.process(context)``
            Expected behavior: Apply conservative dictionary corrections to editable segments.
        """
        if self.dictionary is None or self.engine is None:
            self.initialize(context)

        start_changes = context.total_changes
        threshold = float(self.get_config("confidence_threshold", 92))
        for segment in context.iter_segments():
            segment.current_text = self._merge_broken_words(segment)
            segment.current_text = self._process_text(segment, threshold)
        return StageResult(
            stage=self.name, changes=context.total_changes - start_changes
        )

    def _process_text(self, segment, threshold: float) -> str:
        """Correct eligible word tokens within one segment.

        Example:
            ``result = instance._process_text(segment, 92.0)``
            Expected behavior: Correct eligible word tokens within one segment.
        """
        return self.WORD_PATTERN.sub(
            lambda match: self._correct_word(match.group(0), segment, threshold),
            segment.current_text,
        )

    def _merge_broken_words(self, segment) -> str:
        """Merge high-confidence OCR spaces inside dictionary words.

        A pair is merged only when its concatenation is a frequent dictionary
        word and at least one fragment is unknown. Requiring an unknown fragment
        preserves legitimate pairs such as ``in side`` and ``some one``.
        """
        assert self.dictionary is not None
        minimum_frequency = int(
            self.get_config("broken_word_merge_minimum_frequency", 100_000)
        )
        minimum_zipf = float(self.get_config("wordfreq_minimum_zipf", 2.5))
        changes = 0

        def merge_score(left: str, right: str) -> int:
            combined = left + right
            if self.dictionary.is_protected(left) and self.dictionary.is_protected(right):
                return 0
            if self.dictionary.contains(left) and self.dictionary.contains(right):
                return 0
            combined_frequency = self.dictionary.frequency(combined)
            combined_zipf = self.frequency_scorer.zipf(combined)
            if combined_zipf >= minimum_zipf:
                return max(combined_frequency, self.frequency_scorer.rank(combined))
            if combined_frequency >= minimum_frequency:
                return combined_frequency
            corrected_join = self.JOINED_OCR_CORRECTIONS.get(combined.lower())
            if corrected_join:
                corrected_frequency = self.dictionary.frequency(corrected_join)
                if corrected_frequency >= minimum_frequency:
                    return corrected_frequency
            # Some dictionaries contain a singular but omit its regular plural,
            # for example "augmenter" but not "augmenters".
            if combined.lower().endswith("s"):
                singular_frequency = self.dictionary.frequency(combined[:-1])
                singular_zipf = self.frequency_scorer.zipf(combined[:-1])
                if singular_zipf >= minimum_zipf:
                    return max(
                        singular_frequency,
                        self.frequency_scorer.rank(combined[:-1]),
                    )
                if singular_frequency >= minimum_frequency:
                    return singular_frequency
            if combined.lower().endswith("ly"):
                base_frequency = self.dictionary.frequency(combined[:-2])
                base_zipf = self.frequency_scorer.zipf(combined[:-2])
                if base_zipf >= minimum_zipf:
                    return max(
                        base_frequency,
                        self.frequency_scorer.rank(combined[:-2]),
                    )
                if base_frequency >= minimum_frequency:
                    return base_frequency
            # The compact dictionary omits some valid derivatives such as
            # "petrification". A detached suffix is still strong extraction
            # evidence when neither fragment is independently known.
            suffix_match = (
                right.lower() in self.DETACHED_OCR_SUFFIXES
                and len(left) >= 4
                and not self.dictionary.contains(left)
                and not self.dictionary.contains(right)
            )
            return minimum_frequency if suffix_match else 0

        before = segment.current_text
        # Repeat because repairing one pair can expose another OCR fragment.
        after = before
        for _ in range(3):
            words = list(re.finditer(r"[A-Za-z]{2,}", after))
            candidates: list[tuple[int, int, int, int]] = []
            for pair_index, (left_match, right_match) in enumerate(
                zip(words, words[1:])
            ):
                between = after[left_match.end() : right_match.start()]
                if not re.fullmatch(r"[ \t]+", between):
                    continue
                score = merge_score(left_match.group(0), right_match.group(0))
                pair = (left_match.group(0).lower(), right_match.group(0).lower())
                if not score and pair in self.CONTEXTUAL_COMMON_MERGES:
                    previous = words[pair_index - 1].group(0).lower() if pair_index else ""
                    blocked = (
                        pair == ("be", "cause")
                        and previous in self.COMMON_MERGE_BLOCKING_PREVIOUS
                    )
                    if not blocked:
                        score = self.dictionary.frequency(
                            self.CONTEXTUAL_COMMON_MERGES[pair]
                        )
                if score:
                    candidates.append(
                        (score, pair_index, left_match.end(), right_match.start())
                    )
            # Competing overlaps are resolved by joined-word frequency. This
            # makes ``mana ma nipulation`` choose ``ma+nipulation`` rather than
            # the lower-frequency dictionary entry ``mana+ma`` ("manama").
            spaces_to_remove: list[tuple[int, int]] = []
            used_word_indices: set[int] = set()
            for _, pair_index, start, end in sorted(candidates, reverse=True):
                pair_words = {pair_index, pair_index + 1}
                if pair_words & used_word_indices:
                    continue
                used_word_indices.update(pair_words)
                spaces_to_remove.append((start, end))
            if not spaces_to_remove:
                break
            updated = after
            for start, end in sorted(spaces_to_remove, reverse=True):
                updated = updated[:start] + updated[end:]
            changes += len(spaces_to_remove)
            if updated == after:
                break
            after = updated

        if after != before:
            self.context.tracker.add(
                stage=self.name,
                block_index=segment.block_index,
                segment_index=segment.segment_index,
                line=segment.start_line,
                before=before,
                after=after,
                confidence=97.0,
                reason="Dictionary-validated OCR broken-word merge",
            )
            self.context.increment("broken_words_fixed", changes)
        return after

    def _correct_word(self, word: str, segment, threshold: float) -> str:
        """Return a safe correction or preserve the original word.

        Example:
            ``result = instance._correct_word("teh", segment, 92.0)``
            Expected behavior: Return a safe correction or preserve the original word.
        """
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
            c
            for c in candidates
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
        if (
            len(candidates) > 1
            and (best.confidence - candidates[1].confidence) < ambiguity_margin
        ):
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
        """Transfer uppercase or title-case style to a correction.

        Examples:
            ``_match_case("TEH", "the")`` returns ``"THE"`` and
            ``_match_case("Teh", "the")`` returns ``"The"``.
        """
        if original.isupper():
            return corrected.upper()
        if original[:1].isupper() and original[1:].islower():
            return corrected.capitalize()
        return corrected

    def _generate_candidates(self, word: str):
        """Return ranked edit-distance candidates from the active engine.

        Example:
            ``result = instance._generate_candidates("teh")``
            Expected behavior: Return ranked edit-distance candidates from the active engine.
        """
        if self.engine is None:
            return []
        return self.engine.lookup(word)
