"""
modules/symspell/stage.py

Dictionary based OCR correction stage.
"""

from __future__ import annotations

import re

from ..core import context
from .engine import (
    SymSpellEngine,
)
from ..core.stage import (
    PipelineStage,
    StageResult,
)

from .dictionary import (
    DictionaryManager,
)

from .candidate import (
    CorrectionCandidate,
)

from .processor import (
    SymSpellProcessor,
)


class SymSpellStage(PipelineStage):
    """
    SymSpell dictionary correction.
    """

    name = "SymSpell"

    config_section = "symspell"

    WORD_PATTERN = re.compile(
    r"[A-Za-z][A-Za-z'-]*"
)

    def __init__(
        self,
        config,
    ):

        super().__init__(config)

        self.dictionary = None

        self.engine = None

    # ---------------------------------------------------------

    def initialize(
        self,
        context,
    ):
        """
        Initialize dictionary.
        """
        self.context = context

        dictionary_path = context.config.get("symspell.dictionary")
        glossary_path = context.config.get("symspell.glossary")
        learned_path = context.config.get("symspell.learned")
        max_edit_distance = context.config.get("symspell.max_edit_distance", 2,)

        self.dictionary = DictionaryManager(
            dictionary_path=dictionary_path,
            glossary_path=glossary_path,
            learned_path=learned_path   ,
        )

        self.dictionary.load()

        self.engine = SymSpellEngine(max_edit_distance=max_edit_distance)

        #
        # Add dictionary words
        #

        for word, frequency in self.dictionary.words.items():

            self.engine.add_word(
                word,
                frequency,
            )

    # ---------------------------------------------------------

    def process(
        self,
        context,
    ) -> StageResult:
        """
        Run dictionary correction.
        """

        if not self.config.get(
            "symspell.enabled",
            True,
            ):
            return StageResult(
        stage=self.name,
        changes=0,
    )

        if self.dictionary is None:

            self.initialize(context)

        start_changes = context.total_changes

        threshold = self.get_config("confidence_threshold", 85,)

        for segment in context.iter_segments():

            segment.current_text = self._process_text(
                segment,
                threshold,
            )

        changes = context.total_changes - start_changes

        return StageResult(
            stage=self.name,
            changes=changes,
        )

    # ---------------------------------------------------------

    def _process_text(
        self,
        segment,
        threshold,
    ):

        text = segment.current_text

        def replace_word(match):

            word = match.group(0)

            corrected = self._correct_word(
                word,
                segment,
                threshold,
            )

            return corrected

        return self.WORD_PATTERN.sub(
            replace_word,
            text,
        )

    # ---------------------------------------------------------

    def _correct_word(
        self,
        word,
        segment,
        threshold,
    ):
        """
        Correct one word.
        """

        #
        # Keep known words
        #

        if self.dictionary.contains(word):

            return word

        #
        # Keep protected words
        #

        if self.dictionary.is_protected(word):

            return word

        candidates = self._generate_candidates(word)

        if not candidates:

            return word

        best = max(candidates, key=lambda c: c.confidence)

        if not best.is_safe(threshold):

            return word

        self.record_change(
    segment=segment,
    before=word,
    after=best.corrected,
    confidence=best.confidence,
    reason="SymSpell dictionary correction",
)

        return best.corrected

    # ---------------------------------------------------------

    def _generate_candidates(
        self,
        word,
    ):
        """
        Generate SymSpell candidates.
        """

        if self.engine is None:

            return []

        return self.engine.lookup(word)
