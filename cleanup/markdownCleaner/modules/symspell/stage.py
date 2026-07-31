"""Conservative dictionary-based OCR correction stage."""

from __future__ import annotations

from collections import Counter

from .broken_words import (
    BrokenWordEvaluator,
    BrokenWordMerger,
)
from .corrector import SpellCorrection, SpellCorrector
from .dictionary import DictionaryManager
from .engine import SymSpellEngine
from .frequency import WordfreqScorer
from .settings import SymSpellSettings
from ..core.stage import PipelineStage, StageResult


class SymSpellStage(PipelineStage):
    """Orchestrate dictionary loading, word merging, and spell correction.

    Lexical policy lives in :class:`BrokenWordEvaluator` and
    :class:`SpellCorrector`; Markdown-aware mutation lives in
    :class:`BrokenWordMerger`. The stage retains its previous public attributes
    and helper methods for callers that inject a frequency scorer or inspect
    candidates directly.
    """

    name = "SymSpell"
    config_section = "symspell"
    WORD_PATTERN = SpellCorrector.WORD_PATTERN
    DETACHED_OCR_SUFFIXES = BrokenWordEvaluator.DETACHED_OCR_SUFFIXES
    CONTEXTUAL_COMMON_MERGES = (
        BrokenWordEvaluator.CONTEXTUAL_COMMON_MERGES
    )
    JOINED_OCR_CORRECTIONS = BrokenWordEvaluator.JOINED_OCR_CORRECTIONS
    COMMON_MERGE_BLOCKING_PREVIOUS = (
        BrokenWordEvaluator.COMMON_MERGE_BLOCKING_PREVIOUS
    )

    def __init__(self, config):
        """Initialize immutable settings before dictionaries are loaded."""

        super().__init__(config)
        self.settings = SymSpellSettings.from_config(config)
        self.dictionary: DictionaryManager | None = None
        self.engine: SymSpellEngine | None = None
        self.frequency_scorer = WordfreqScorer(enabled=False)

    def initialize(self, context) -> None:
        """Load vocabularies, protect document terms, and build the index."""

        self.context = context
        self.dictionary = DictionaryManager(
            dictionary_path=context.config.resolve_path(
                self.settings.dictionary
            ),
            glossary_path=context.config.resolve_path(
                self.settings.glossary
            ),
            learned_path=context.config.resolve_path(
                self.settings.learned
            ),
        )
        self.dictionary.load()

        for word in self.settings.protected_terms:
            self.dictionary.protect(word)

        if self.settings.auto_protect_proper_nouns:
            self._protect_document_terms(context.current_markdown)

        self.engine = SymSpellEngine(
            max_edit_distance=self.settings.max_edit_distance
        )
        self.frequency_scorer = WordfreqScorer(
            enabled=self.settings.wordfreq_enabled,
            language=self.settings.wordfreq_language,
            wordlist=self.settings.wordfreq_wordlist,
        )
        for word, frequency in self.dictionary.words.items():
            if frequency >= self.settings.minimum_dictionary_frequency:
                self.engine.add_word(word, frequency)

    def _protect_document_terms(self, text: str) -> None:
        """Protect repeated proper nouns and mixed-case document terms."""

        assert self.dictionary is not None
        tokens = self.WORD_PATTERN.findall(text)
        counts = Counter(tokens)
        for token, count in counts.items():
            if (
                count < self.settings.proper_noun_minimum_occurrences
                or self.dictionary.contains(token)
            ):
                continue
            mixed_case = any(character.isupper() for character in token[1:])
            title_case = (
                token[:1].isupper()
                and token[1:].islower()
            )
            if mixed_case or title_case:
                self.dictionary.protect(token)

    def process(self, context) -> StageResult:
        """Merge broken words, then correct safe misspellings in prose."""

        if self.dictionary is None or self.engine is None:
            self.initialize(context)

        start_changes = context.total_changes
        merger = self._make_merger()
        corrector = self._make_corrector()
        self._merge_cross_block_broken_words(
            context,
            merger=merger,
        )
        for segment in context.iter_segments():
            segment.current_text = self._merge_broken_words(
                segment,
                merger=merger,
            )
            segment.current_text = self._process_text(
                segment,
                self.settings.confidence_threshold,
                corrector=corrector,
            )
        return StageResult(
            stage=self.name,
            changes=context.total_changes - start_changes,
        )

    def _make_merger(self) -> BrokenWordMerger:
        """Build a merger from the currently injected dependencies."""

        assert self.dictionary is not None
        evaluator = BrokenWordEvaluator(
            dictionary=self.dictionary,
            frequency_scorer=self.frequency_scorer,
            settings=self.settings,
        )
        return BrokenWordMerger(evaluator, self.settings)

    def _make_corrector(self) -> SpellCorrector:
        """Build a corrector from the currently injected lookup engine."""

        assert self.dictionary is not None
        assert self.engine is not None
        return SpellCorrector(
            dictionary=self.dictionary,
            engine=self.engine,
            settings=self.settings,
        )

    def _merge_cross_block_broken_words(
        self,
        context,
        *,
        merger: BrokenWordMerger | None = None,
    ) -> None:
        """Repair safe word splits across adjacent editable paragraphs."""

        active_merger = merger or self._make_merger()
        changes = active_merger.merge_cross_blocks(context)
        for change in changes:
            context.tracker.add(
                stage=self.name,
                block_index=-1,
                segment_index=-1,
                line=change.line,
                before=change.before,
                after=change.after,
                confidence=change.decision.confidence,
                reason=change.decision.reason,
                broken_word=change.decision.broken_word,
            )
        if changes:
            context.increment("broken_words_fixed", len(changes))

    def _merge_broken_words(
        self,
        segment,
        *,
        merger: BrokenWordMerger | None = None,
    ) -> str:
        """Merge high-confidence OCR spaces inside one editable segment."""

        active_merger = merger or self._make_merger()
        result = active_merger.merge_inline(segment.current_text)
        if not result.decisions:
            return result.text

        context = self.context
        context.tracker.add(
            stage=self.name,
            block_index=segment.block_index,
            segment_index=segment.segment_index,
            line=segment.start_line,
            before=segment.current_text,
            after=result.text,
            confidence=min(
                decision.confidence for decision in result.decisions
            ),
            reason=result.decisions[0].reason,
            broken_word=", ".join(
                decision.broken_word for decision in result.decisions
            ),
        )
        context.increment("broken_words_fixed", result.changes)
        return result.text

    def _process_text(
        self,
        segment,
        threshold: float,
        *,
        corrector: SpellCorrector | None = None,
    ) -> str:
        """Correct eligible word tokens within one segment."""

        active_corrector = corrector or self._make_corrector()
        return active_corrector.process_text(
            segment.current_text,
            lambda correction: self._record_spell_correction(
                segment,
                correction,
            ),
            threshold=threshold,
        )

    def _record_spell_correction(
        self,
        segment,
        correction: SpellCorrection,
    ) -> None:
        self.record_change(
            segment=segment,
            before=correction.original,
            after=correction.replacement,
            confidence=correction.confidence,
            reason=correction.reason,
        )

    def _correct_word(
        self,
        word: str,
        segment,
        threshold: float,
    ) -> str:
        """Compatibility wrapper returning a corrected or original token."""

        correction = self._make_corrector().correct_word(
            word,
            threshold=threshold,
        )
        if correction is None:
            return word
        self._record_spell_correction(segment, correction)
        return correction.replacement

    @staticmethod
    def _match_case(original: str, corrected: str) -> str:
        """Transfer uppercase or title-case style to a correction."""

        return SpellCorrector.match_case(original, corrected)

    def _generate_candidates(self, word: str):
        """Return edit-distance candidates from the active engine."""

        if self.engine is None:
            return []
        return self.engine.lookup(word)
