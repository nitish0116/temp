"""Report-only discovery and explicit approval of domain vocabulary."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from ..core.stage import PipelineStage, StageResult
from ..markdown.segmenter import MarkdownSegment, split_protected_spans
from ..markdown.markdown import BlockType
from .dictionary import DictionaryManager
from .engine import SymSpellEngine
from .tokens import TERM_PATTERN
from .tokens import WORD_PATTERN
from .vocabulary_classification import classify_candidate
from .vocabulary_store import (
    LEARNED_DESCRIPTION,
    REJECTED_DESCRIPTION,
    load_reviewed_words,
    merge_approved_words,
    merge_learned_words,
    merge_rejected_words,
    merge_words as _merge_words,
    word_list as _word_list,
)


WORD = WORD_PATTERN
TERM = TERM_PATTERN


@dataclass(slots=True)
class VocabularyInventory:
    """Bounded occurrence evidence collected from one document."""

    counts: Counter[str] = field(default_factory=Counter)
    forms: dict[str, Counter[str]] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    lines: dict[str, list[int]] = field(
        default_factory=lambda: defaultdict(list)
    )
    contexts: dict[
        str,
        list[tuple[str | None, str | None]],
    ] = field(default_factory=lambda: defaultdict(list))

    @classmethod
    def collect(cls, text: str) -> "VocabularyInventory":
        """Collect token forms, working lines, and bounded local contexts."""

        inventory = cls()
        inventory.add_text(text)
        return inventory

    @classmethod
    def collect_segments(
        cls,
        segments: Iterable[MarkdownSegment],
    ) -> "VocabularyInventory":
        """Collect evidence only from editable Markdown prose spans."""

        inventory = cls()
        for segment in segments:
            line_offset = 0
            for span in split_protected_spans(segment.get_text()):
                if not span.protected:
                    inventory.add_text(
                        span.text,
                        start_line=segment.start_line + line_offset,
                    )
                line_offset += span.text.count("\n")
        return inventory

    def add_text(self, text: str, *, start_line: int = 1) -> None:
        """Add one text span while retaining its working-document line."""

        for line_number, line in enumerate(text.splitlines(), start_line):
            tokens = WORD.findall(line)
            for index, token in enumerate(tokens):
                key = token.casefold()
                self.counts[key] += 1
                self.forms[key][token] += 1
                if len(self.lines[key]) < 10:
                    self.lines[key].append(line_number)
                if len(self.contexts[key]) < 20:
                    previous = tokens[index - 1] if index else None
                    following = (
                        tokens[index + 1]
                        if index + 1 < len(tokens)
                        else None
                    )
                    self.contexts[key].append(
                        (previous, following)
                    )


class VocabularyCandidateStage(PipelineStage):
    """Discover domain vocabulary for review without silently approving it.

    The report-only workflow counts token forms and source lines, excludes known
    or protected words, and attaches the best dictionary suggestion when one
    exists. Candidates are stored in ``context.metadata['glossary_candidates']``
    and logged as ``pending_review``. The document and glossary remain unchanged.

    A reviewer can later approve a candidate explicitly with::

        python -m markdownCleaner.cli --approve-words sitrep noncoms

    Example:
        ``instance = VocabularyCandidateStage(config)``
        Expected behavior: Discover domain vocabulary for review without silently approving it.
    """

    name = "VocabularyCandidates"
    config_section = "vocabulary_candidates"

    def process(self, context) -> StageResult:
        """Collect and report repeated unknown terms as review-only candidates.

        The configured occurrence threshold suppresses one-off noise, while the
        report limit bounds memory and audit output. ``changes`` in the returned
        result means findings reported—not text mutations—for this stage.

        Example:
            ``result = instance.process(context)``
            Expected behavior: Collect and report repeated unknown terms as review-only candidates.
        """
        manager = DictionaryManager(
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
        manager.load()
        for word in context.config.get("symspell.protected", []) or []:
            manager.protect(str(word))

        rejected = load_reviewed_words(
            context.config.resolve_path(
                context.config.get(
                    "vocabulary_candidates.rejected",
                    "data/rejected_words.json",
                )
            )
        )

        inventory = VocabularyInventory.collect_segments(
            segment
            for segment in context.iter_segments()
            if segment.block_type is BlockType.PARAGRAPH
        )

        engine = SymSpellEngine(
            max_edit_distance=int(context.config.get("symspell.max_edit_distance", 2))
        )
        minimum_frequency = int(
            context.config.get("symspell.minimum_dictionary_frequency", 1)
        )
        for word, frequency in manager.words.items():
            if frequency >= minimum_frequency:
                engine.add_word(word, frequency)

        minimum = int(self.get_config("minimum_occurrences", 3))
        limit = int(self.get_config("report_limit", 200))
        candidates: list[dict] = []
        if limit <= 0:
            context.metadata["glossary_candidates"] = candidates
            return StageResult(stage=self.name, changes=0)
        for key, count in inventory.counts.most_common():
            if (
                count < minimum
                or key in rejected
                or manager.contains(key)
                or manager.is_protected(key)
            ):
                continue
            if len(key) < 4 or not key.isalpha():
                continue
            display = inventory.forms[key].most_common(1)[0][0]
            classification, classification_confidence, classification_basis = (
                classify_candidate(display, inventory.contexts[key])
            )
            suggestions = engine.lookup(key)
            best = suggestions[0] if suggestions else None
            item = {
                "word": display,
                "occurrences": count,
                "lines": inventory.lines[key],
                "suggested_correction": best.corrected if best else None,
                "edit_distance": best.distance if best else None,
                "confidence": round(best.confidence, 2) if best else None,
                "classification": classification,
                "classification_confidence": classification_confidence,
                "classification_basis": classification_basis,
                "status": "pending_review",
            }
            candidates.append(item)
            context.tracker.add(
                stage=self.name,
                block_index=-1,
                segment_index=-1,
                line=(
                    inventory.lines[key][0]
                    if inventory.lines[key]
                    else 0
                ),
                before=display,
                after=display,
                confidence=0.0,
                reason=(
                    f"Candidate only; {count} occurrences. Explicit approval "
                    "is required before adding it to custom_words.json."
                ),
            )
            if len(candidates) >= limit:
                break

        context.metadata["glossary_candidates"] = candidates
        return StageResult(stage=self.name, changes=len(candidates))
