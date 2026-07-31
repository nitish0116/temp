"""Dictionary-validated OCR broken-word merging."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import re

from .dictionary import DictionaryManager
from .frequency import WordfreqScorer
from .settings import SymSpellSettings
from ..markdown.markdown import BlockType
from ..markdown.segmenter import protected_span_ranges


INLINE_MERGE_REASON = "Dictionary-validated OCR broken-word merge"
CROSS_BLOCK_MERGE_REASON = (
    "Dictionary-validated OCR cross-block broken-word merge"
)


class MergeEvidenceKind(str, Enum):
    """The lexical rule that justified a broken-word merge."""

    PROTECTED_TERM = "protected-term"
    DICTIONARY_FREQUENCY = "dictionary-frequency"
    WORDFREQ = "wordfreq"
    JOINED_OCR_CORRECTION = "joined-ocr-correction"
    REGULAR_PLURAL = "regular-plural"
    ADVERB_DERIVATION = "adverb-derivation"
    PRODUCTIVE_OUT_VERB = "productive-out-verb"
    DETACHED_OCR_SUFFIX = "detached-ocr-suffix"
    CONTEXTUAL_COMMON_WORD = "contextual-common-word"


@dataclass(frozen=True, slots=True)
class MergeEvidence:
    """Corpus or morphology evidence supporting one replacement."""

    kind: MergeEvidenceKind
    term: str
    rank: int
    dictionary_frequency: int = 0
    zipf: float = 0.0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class MergeDecision:
    """A lexical decision independent from its position in Markdown."""

    broken_word: str
    replacement: str
    rank: int
    confidence: float
    reason: str
    evidence: MergeEvidence


@dataclass(frozen=True, slots=True)
class MergeCandidate:
    """A positioned decision used for overlap resolution."""

    pair_index: int
    start: int
    end: int
    decision: MergeDecision


@dataclass(frozen=True, slots=True)
class MergeResult:
    """The result of applying zero or more inline merge decisions."""

    text: str
    decisions: tuple[MergeDecision, ...] = ()

    @property
    def changes(self) -> int:
        return len(self.decisions)


@dataclass(frozen=True, slots=True)
class CrossBlockMerge:
    """One structural paragraph-boundary merge for logging."""

    before: str
    after: str
    line: int
    decision: MergeDecision


class BrokenWordEvaluator:
    """Evaluate one adjacent token pair using ordered lexical rules."""

    DETACHED_OCR_SUFFIXES = frozenset({"tion", "tions"})
    CONTEXTUAL_COMMON_MERGES = {
        ("be", "cause"): "because",
        ("upperclass", "men"): "upperclassmen",
    }
    JOINED_OCR_CORRECTIONS = {
        "expressinless": "expressionless",
    }
    COMMON_MERGE_BLOCKING_PREVIOUS = frozenset(
        {
            "can",
            "could",
            "may",
            "might",
            "must",
            "shall",
            "should",
            "will",
            "would",
        }
    )
    BE_CAUSE_BLOCKING_FOLLOWING = frozenset(
        {"enough", "for", "of", "to"}
    )

    def __init__(
        self,
        dictionary: DictionaryManager,
        frequency_scorer: WordfreqScorer,
        settings: SymSpellSettings,
    ) -> None:
        self.dictionary = dictionary
        self.frequency_scorer = frequency_scorer
        self.settings = settings

    def evaluate(
        self,
        left: str,
        right: str,
        *,
        previous: str = "",
        following: str = "",
        reason: str = INLINE_MERGE_REASON,
        allow_contextual: bool = True,
    ) -> MergeDecision | None:
        """Return a typed decision when joining the pair is sufficiently safe."""

        if self._is_acronym_fragment(left) or self._is_acronym_fragment(right):
            return None
        if self._context_blocks_merge(
            left,
            right,
            previous=previous,
            following=following,
        ):
            return None

        combined_source = left + right
        combined = combined_source.lower()
        protected = self.dictionary.is_protected(combined)
        if protected:
            evidence = MergeEvidence(
                kind=MergeEvidenceKind.PROTECTED_TERM,
                term=combined,
                rank=max(
                    self.dictionary.frequency(combined),
                    self.settings.broken_word_merge_minimum_frequency,
                ),
                dictionary_frequency=self.dictionary.frequency(combined),
                detail="explicit custom or learned term",
            )
            return self._decision(
                left,
                right,
                combined_source,
                evidence,
                reason,
            )

        both_fragments_protected = (
            self.dictionary.is_protected(left)
            and self.dictionary.is_protected(right)
        )
        both_fragments_known = (
            self.dictionary.contains(left)
            and self.dictionary.contains(right)
        )
        if not both_fragments_protected and not both_fragments_known:
            decision = self._evaluate_joined_form(
                left,
                right,
                combined_source,
                combined,
                reason,
            )
            if decision is not None:
                return decision

        if allow_contextual:
            return self._evaluate_contextual(
                left,
                right,
                previous,
                following,
                reason,
            )
        return None

    def _evaluate_joined_form(
        self,
        left: str,
        right: str,
        combined_source: str,
        combined: str,
        reason: str,
    ) -> MergeDecision | None:
        evidence = self._corpus_evidence(combined)
        if evidence is not None:
            return self._decision(
                left,
                right,
                combined_source,
                evidence,
                reason,
            )

        corrected_join = self.JOINED_OCR_CORRECTIONS.get(combined)
        if corrected_join:
            corrected_evidence = self._corpus_evidence(corrected_join)
            if corrected_evidence is not None:
                evidence = MergeEvidence(
                    kind=MergeEvidenceKind.JOINED_OCR_CORRECTION,
                    term=corrected_join,
                    rank=corrected_evidence.rank,
                    dictionary_frequency=(
                        corrected_evidence.dictionary_frequency
                    ),
                    zipf=corrected_evidence.zipf,
                    detail=f"{combined} -> {corrected_join}",
                )
                return self._decision(
                    left,
                    right,
                    self._match_case(combined_source, corrected_join),
                    evidence,
                    reason,
                )

        if combined.endswith("s"):
            singular = combined[:-1]
            singular_evidence = self._corpus_evidence(singular)
            if singular_evidence is not None:
                evidence = self._derived_evidence(
                    MergeEvidenceKind.REGULAR_PLURAL,
                    combined,
                    singular,
                    singular_evidence,
                )
                return self._decision(
                    left,
                    right,
                    combined_source,
                    evidence,
                    reason,
                )

        if combined.endswith("ly"):
            base = combined[:-2]
            base_evidence = self._corpus_evidence(base)
            if base_evidence is not None:
                evidence = self._derived_evidence(
                    MergeEvidenceKind.ADVERB_DERIVATION,
                    combined,
                    base,
                    base_evidence,
                )
                return self._decision(
                    left,
                    right,
                    combined_source,
                    evidence,
                    reason,
                )

        out_verb_evidence = self._productive_out_verb_evidence(combined)
        if out_verb_evidence is not None:
            return self._decision(
                left,
                right,
                combined_source,
                out_verb_evidence,
                reason,
            )

        suffix_match = (
            right.lower() in self.DETACHED_OCR_SUFFIXES
            and len(left) >= 4
            and not self.dictionary.contains(left)
            and not self.dictionary.contains(right)
        )
        if suffix_match:
            evidence = MergeEvidence(
                kind=MergeEvidenceKind.DETACHED_OCR_SUFFIX,
                term=combined,
                rank=self.settings.broken_word_merge_minimum_frequency,
                detail=f"detached suffix {right.lower()}",
            )
            return self._decision(
                left,
                right,
                combined_source,
                evidence,
                reason,
            )
        return None

    def _evaluate_contextual(
        self,
        left: str,
        right: str,
        previous: str,
        following: str,
        reason: str,
    ) -> MergeDecision | None:
        pair = (left.lower(), right.lower())
        replacement = self.CONTEXTUAL_COMMON_MERGES.get(pair)
        if replacement is None:
            return None
        source_evidence = self._corpus_evidence(replacement)
        if source_evidence is None:
            return None
        evidence = MergeEvidence(
            kind=MergeEvidenceKind.CONTEXTUAL_COMMON_WORD,
            term=replacement,
            rank=source_evidence.rank,
            dictionary_frequency=source_evidence.dictionary_frequency,
            zipf=source_evidence.zipf,
            detail=f"contextual pair {left} {right}",
        )
        return self._decision(
            left,
            right,
            self._match_case(left + right, replacement),
            evidence,
            reason,
        )

    def _context_blocks_merge(
        self,
        left: str,
        right: str,
        *,
        previous: str,
        following: str,
    ) -> bool:
        """Reject an ambiguous pair when neighboring words form valid prose."""

        if (left.casefold(), right.casefold()) != ("be", "cause"):
            return False
        return (
            previous.casefold()
            in self.COMMON_MERGE_BLOCKING_PREVIOUS | {"to"}
            or following.casefold() in self.BE_CAUSE_BLOCKING_FOLLOWING
        )

    def _corpus_evidence(self, term: str) -> MergeEvidence | None:
        dictionary_frequency = self.dictionary.frequency(term)
        zipf = self.frequency_scorer.zipf(term)
        wordfreq_rank = (
            self.frequency_scorer.rank(term)
            if zipf >= self.settings.wordfreq_minimum_zipf
            else 0
        )
        if zipf >= self.settings.wordfreq_minimum_zipf:
            return MergeEvidence(
                kind=MergeEvidenceKind.WORDFREQ,
                term=term,
                rank=max(dictionary_frequency, wordfreq_rank),
                dictionary_frequency=dictionary_frequency,
                zipf=zipf,
            )
        if (
            dictionary_frequency
            >= self.settings.broken_word_merge_minimum_frequency
        ):
            return MergeEvidence(
                kind=MergeEvidenceKind.DICTIONARY_FREQUENCY,
                term=term,
                rank=dictionary_frequency,
                dictionary_frequency=dictionary_frequency,
            )
        return None

    def _productive_out_verb_evidence(
        self,
        combined: str,
    ) -> MergeEvidence | None:
        if not (
            combined.startswith("out")
            and combined.endswith(("ed", "ing"))
        ):
            return None
        prefixed_base = (
            combined[:-2] if combined.endswith("ed") else combined[:-3]
        )
        root = prefixed_base[3:]
        if len(root) < 3:
            return None
        root_evidence = self._corpus_evidence(root)
        if root_evidence is None:
            return None
        return self._derived_evidence(
            MergeEvidenceKind.PRODUCTIVE_OUT_VERB,
            combined,
            root,
            root_evidence,
        )

    @staticmethod
    def _derived_evidence(
        kind: MergeEvidenceKind,
        term: str,
        base: str,
        source: MergeEvidence,
    ) -> MergeEvidence:
        return MergeEvidence(
            kind=kind,
            term=term,
            rank=source.rank,
            dictionary_frequency=source.dictionary_frequency,
            zipf=source.zipf,
            detail=f"derived from {base}",
        )

    @staticmethod
    def _decision(
        left: str,
        right: str,
        replacement: str,
        evidence: MergeEvidence,
        reason: str,
    ) -> MergeDecision:
        return MergeDecision(
            broken_word=f"{left} {right}",
            replacement=replacement,
            rank=evidence.rank,
            confidence=97.0,
            reason=reason,
            evidence=evidence,
        )

    @staticmethod
    def _is_acronym_fragment(word: str) -> bool:
        return word.isupper() and len(word) > 1

    @staticmethod
    def _match_case(original: str, replacement: str) -> str:
        if original.isupper():
            return replacement.upper()
        if original[:1].isupper() and original[1:].islower():
            return replacement.capitalize()
        return replacement


class BrokenWordMerger:
    """Scan prose, resolve overlapping joins, and merge safe block boundaries."""

    TOKEN_PATTERN = re.compile(r"[A-Za-z]{2,}")
    INLINE_CODE_PATTERN = re.compile(r"(`+)[^\n]*?\1")
    LEFT_BOUNDARY_PATTERN = re.compile(r"\b([A-Za-z]{2,})([ \t]*)$")
    RIGHT_BOUNDARY_PATTERN = re.compile(r"^([a-z]{2,})\b")
    SAFE_CROSS_BLOCK_EVIDENCE = frozenset(
        {
            MergeEvidenceKind.PROTECTED_TERM,
            MergeEvidenceKind.DICTIONARY_FREQUENCY,
            MergeEvidenceKind.WORDFREQ,
            MergeEvidenceKind.JOINED_OCR_CORRECTION,
        }
    )

    def __init__(
        self,
        evaluator: BrokenWordEvaluator,
        settings: SymSpellSettings,
    ) -> None:
        self.evaluator = evaluator
        self.settings = settings

    def merge_inline(self, text: str) -> MergeResult:
        """Merge adjacent OCR fragments in editable prose."""

        current = text
        applied: list[MergeDecision] = []
        for _ in range(self.settings.maximum_merge_passes):
            candidates = self.scan_candidates(current)
            selected = self.resolve_overlaps(candidates)
            if not selected:
                break
            selected_in_text_order = sorted(
                selected,
                key=lambda item: item.start,
            )
            updated = current
            for candidate in sorted(
                selected_in_text_order,
                key=lambda item: item.start,
                reverse=True,
            ):
                updated = (
                    updated[: candidate.start]
                    + candidate.decision.replacement
                    + updated[candidate.end :]
                )
            if updated == current:
                break
            applied.extend(
                candidate.decision for candidate in selected_in_text_order
            )
            current = updated
        return MergeResult(text=current, decisions=tuple(applied))

    def scan_candidates(self, text: str) -> list[MergeCandidate]:
        """Return all positioned candidates before overlap resolution."""

        words = list(self.TOKEN_PATTERN.finditer(text))
        protected_spans = protected_span_ranges(text)
        candidates: list[MergeCandidate] = []
        for pair_index, (left_match, right_match) in enumerate(
            zip(words, words[1:])
        ):
            start = left_match.start()
            end = right_match.end()
            if self._overlaps_any(start, end, protected_spans):
                continue
            between = text[left_match.end() : right_match.start()]
            if re.fullmatch(r"[ \t]+", between) is None:
                continue
            previous = (
                words[pair_index - 1].group(0)
                if pair_index
                else ""
            )
            following = (
                words[pair_index + 2].group(0)
                if pair_index + 2 < len(words)
                else ""
            )
            decision = self.evaluator.evaluate(
                left_match.group(0),
                right_match.group(0),
                previous=previous,
                following=following,
            )
            if decision is None:
                continue
            decision = replace(
                decision,
                broken_word=text[start:end],
            )
            candidates.append(
                MergeCandidate(
                    pair_index=pair_index,
                    start=start,
                    end=end,
                    decision=decision,
                )
            )
        return candidates

    @staticmethod
    def resolve_overlaps(
        candidates: list[MergeCandidate],
    ) -> list[MergeCandidate]:
        """Prefer stronger joins when adjacent candidates share a token."""

        selected: list[MergeCandidate] = []
        used_word_indices: set[int] = set()
        for candidate in sorted(
            candidates,
            key=lambda item: (
                item.decision.rank,
                item.pair_index,
                item.start,
                item.end,
                item.decision.broken_word,
            ),
            reverse=True,
        ):
            pair_words = {
                candidate.pair_index,
                candidate.pair_index + 1,
            }
            if pair_words & used_word_indices:
                continue
            used_word_indices.update(pair_words)
            selected.append(candidate)
        return selected

    def merge_cross_blocks(self, context) -> tuple[CrossBlockMerge, ...]:
        """Merge only safe PARAGRAPH/BLANK/PARAGRAPH boundaries.

        Segment edits are synchronized into the parsed document before any
        boundary inspection. Protected blocks and Markdown syntax are never
        searched or rewritten by a raw-document regular expression.
        """

        if context.document is None:
            return ()
        context.update_markdown()
        blocks = context.document.blocks
        changes: list[CrossBlockMerge] = []
        index = 0
        while index + 2 < len(blocks):
            left_block, separator, right_block = blocks[index : index + 3]
            if not self._is_safe_block_boundary(
                left_block,
                separator,
                right_block,
            ):
                index += 1
                continue

            left_text = left_block.current_text
            right_text = right_block.current_text
            left_match = self.LEFT_BOUNDARY_PATTERN.search(left_text)
            right_match = self.RIGHT_BOUNDARY_PATTERN.match(right_text)
            if left_match is None or right_match is None:
                index += 1
                continue
            if self._overlaps_any(
                left_match.start(1),
                left_match.end(1),
                protected_span_ranges(left_text),
            ) or self._overlaps_any(
                right_match.start(1),
                right_match.end(1),
                protected_span_ranges(right_text),
            ):
                index += 1
                continue

            previous_words = self.TOKEN_PATTERN.findall(
                left_text[: left_match.start(1)]
            )
            decision = self.evaluator.evaluate(
                left_match.group(1),
                right_match.group(1),
                previous=previous_words[-1] if previous_words else "",
                reason=CROSS_BLOCK_MERGE_REASON,
                allow_contextual=False,
            )
            if (
                decision is None
                or decision.evidence.kind
                not in self.SAFE_CROSS_BLOCK_EVIDENCE
            ):
                index += 1
                continue

            broken_word = (
                left_text[left_match.start(1) :]
                + "\n"
                + separator.current_text
                + "\n"
                + right_text[: right_match.end(1)]
            )
            decision = replace(decision, broken_word=broken_word)
            before = broken_word
            merged_text = (
                left_text[: left_match.start(1)]
                + decision.replacement
                + right_text[right_match.end(1) :]
            )
            line = left_block.start_line + left_text[
                : left_match.start(1)
            ].count("\n")
            changes.append(
                CrossBlockMerge(
                    before=before,
                    after=decision.replacement,
                    line=line,
                    decision=decision,
                )
            )
            left_block.content = merged_text
            del blocks[index + 1 : index + 3]

        if changes:
            context.replace_markdown(context.document.to_markdown())
        return tuple(changes)

    @staticmethod
    def _is_safe_block_boundary(left, separator, right) -> bool:
        return (
            left.block_type is BlockType.PARAGRAPH
            and left.editable
            and separator.block_type is BlockType.BLANK
            and not separator.editable
            and not separator.current_text.strip()
            and right.block_type is BlockType.PARAGRAPH
            and right.editable
        )

    @staticmethod
    def _overlaps_any(
        start: int,
        end: int,
        spans: tuple[tuple[int, int], ...],
    ) -> bool:
        return any(
            start < span_end and end > span_start
            for span_start, span_end in spans
        )
