"""Context-aware OCR boundary validation without loading a real model."""

from __future__ import annotations

import json

import pytest

from markdownCleaner.modules.core.config import PipelineConfig
from markdownCleaner.modules.core.context import ProcessingContext
from markdownCleaner.modules.symspell.broken_words import (
    BrokenWordEvaluator,
    BrokenWordMerger,
    MergeCandidate,
    MergeDecision,
    MergeEvidence,
    MergeEvidenceKind,
)
from markdownCleaner.modules.symspell.context_validator import (
    BoundaryContextValidator,
    ContextValidatorSettings,
    ScoringVariant,
)
from markdownCleaner.modules.symspell.decisions import (
    AcceptedBoundary,
    BrokenWordDecisions,
)
from markdownCleaner.modules.symspell.dictionary import DictionaryManager
from markdownCleaner.modules.symspell.frequency import WordfreqScorer
from markdownCleaner.modules.symspell.ocr_candidates import (
    OCRBoundaryCandidates,
)
from markdownCleaner.modules.symspell.settings import SymSpellSettings
from markdownCleaner.modules.symspell.stage import SymSpellStage


class ContextScores:
    """Deterministic substitute for localized transformer likelihoods."""

    def score(self, variants: list[ScoringVariant]) -> list[float]:
        scores = []
        for variant in variants:
            if "could because" in variant.text:
                scores.append(-3.0)
            elif "could be cause" in variant.text:
                scores.append(-0.2)
            elif "because they" in variant.text:
                scores.append(-0.1)
            elif "be cause they" in variant.text:
                scores.append(-2.0)
            else:
                scores.append(-1.0)
        return scores


def _settings() -> SymSpellSettings:
    return SymSpellSettings(
        wordfreq_enabled=False,
        broken_word_merge_minimum_frequency=50_000,
    )


def _merger() -> BrokenWordMerger:
    dictionary = DictionaryManager()
    for word in ("be", "cause", "because"):
        dictionary.add_word(word, 1_000_000)
    candidates = OCRBoundaryCandidates({"be cause": "because"})
    evaluator = BrokenWordEvaluator(
        dictionary,
        WordfreqScorer(enabled=False),
        _settings(),
        BrokenWordDecisions(),
        candidates,
    )
    validator = BoundaryContextValidator(
        ContextValidatorSettings(enabled=True, merge_margin=0.35),
        scorer=ContextScores(),
    )
    return BrokenWordMerger(
        evaluator,
        _settings(),
        context_validator=validator,
    )


def test_candidate_store_is_recall_only_without_context_validator():
    merger = _merger()
    merger.context_validator = None

    result = merger.merge_inline("It happened be cause they left.")

    assert result.text == "It happened be cause they left."
    assert result.decisions == ()


def test_corpus_suppression_prevents_automatic_candidate_merge():
    dictionary = DictionaryManager()
    for word in ("be", "cause", "because"):
        dictionary.add_word(word, 1_000_000)
    evaluator = BrokenWordEvaluator(
        dictionary,
        WordfreqScorer(enabled=False),
        _settings(),
        BrokenWordDecisions(),
        OCRBoundaryCandidates({}, frozenset({"be cause"})),
    )

    assert evaluator.evaluate("be", "cause") is None


def test_human_acceptance_overrides_corpus_suppression():
    dictionary = DictionaryManager()
    decisions = BrokenWordDecisions(
        accepted={"be cause": AcceptedBoundary("because")}
    )
    evaluator = BrokenWordEvaluator(
        dictionary,
        WordfreqScorer(enabled=False),
        _settings(),
        decisions,
        OCRBoundaryCandidates({}, frozenset({"be cause"})),
    )

    decision = evaluator.evaluate("be", "cause")

    assert decision is not None
    assert decision.replacement == "because"
    assert decision.evidence.kind is MergeEvidenceKind.REVIEWED_DECISION


def test_context_validator_accepts_and_rejects_same_pair_by_sentence():
    merger = _merger()

    accepted = merger.merge_inline("It happened be cause they left.")
    rejected = merger.merge_inline("That could be cause for concern.")

    assert accepted.text == "It happened because they left."
    assert (
        accepted.decisions[0].evidence.kind
        is MergeEvidenceKind.TRANSFORMER_CONTEXT
    )
    assert "margin=1.900" in accepted.decisions[0].reason
    assert rejected.text == "That could be cause for concern."
    assert rejected.decisions == ()
    assert rejected.rejected[0].broken_word == "be cause"
    assert "margin=-2.800" in rejected.rejected[0].reason


def test_reviewed_and_protected_decisions_bypass_model_scoring():
    class UnexpectedScorer:
        def score(self, _variants):
            raise AssertionError("trusted evidence was sent to the model")

    decision = MergeDecision(
        broken_word="Ley win",
        replacement="Leywin",
        rank=100_000,
        confidence=97.0,
        reason="reviewed",
        evidence=MergeEvidence(
            MergeEvidenceKind.REVIEWED_DECISION,
            "leywin",
            100_000,
        ),
    )
    candidate = MergeCandidate(0, 7, 14, decision)
    validator = BoundaryContextValidator(
        ContextValidatorSettings(enabled=True),
        scorer=UnexpectedScorer(),
    )

    outcome = validator.validate("Arthur Ley win arrived.", [candidate])

    assert outcome.accepted == (candidate,)
    assert outcome.rejected == ()


def test_candidate_file_is_validated_and_case_insensitive(tmp_path):
    source = tmp_path / "candidates.json"
    source.write_text(
        json.dumps({"candidates": {"Pro fessor": "professor"}}),
        encoding="utf-8",
    )

    candidates = OCRBoundaryCandidates.load(source)

    assert candidates.replacement("pro", "FESSOR") == "professor"

    source.write_text(
        json.dumps({"candidates": {"three part word": "word"}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Invalid OCR boundary candidate"):
        OCRBoundaryCandidates.load(source)


def test_context_validator_settings_are_strict_and_bounded():
    with pytest.raises(
        ValueError,
        match="context_validator.enabled must be true or false",
    ):
        ContextValidatorSettings.from_config(
            PipelineConfig(
                {"context_validator": {"enabled": "false"}}
            )
        )

    settings = ContextValidatorSettings.from_config(
        PipelineConfig(
            {
                "context_validator": {
                    "enabled": True,
                    "batch_size": 8,
                    "merge_margin": 0.5,
                }
            }
        )
    )
    settings.validate()

    assert settings.enabled is True
    assert settings.batch_size == 8
    assert settings.merge_margin == 0.5


def test_stage_preserves_and_logs_transformer_rejection(tmp_path):
    dictionary = tmp_path / "dictionary.txt"
    dictionary.write_text(
        "be 1000000\ncause 1000000\nbecause 1000000\n",
        encoding="utf-8",
    )
    candidates = tmp_path / "candidates.json"
    candidates.write_text(
        json.dumps({"candidates": {"be cause": "because"}}),
        encoding="utf-8",
    )
    source = tmp_path / "book.md"
    source.write_text("That could be cause for concern.", encoding="utf-8")
    config = PipelineConfig(
        {
            "paths": {"output_directory": str(tmp_path / "output")},
            "backup": {"enabled": False},
            "symspell": {
                "dictionary": str(dictionary),
                "wordfreq_enabled": False,
                "auto_protect_proper_nouns": False,
            },
            "context_validator": {
                "enabled": True,
                "candidate_file": str(candidates),
            },
        }
    )
    context = ProcessingContext(config)
    context.load_markdown(source)
    stage = SymSpellStage(config)
    stage.initialize(context)
    stage.context_validator = BoundaryContextValidator(
        stage.context_validator_settings,
        scorer=ContextScores(),
    )
    stage.initialize = lambda _context: None

    result = stage.execute(context)

    assert result.success
    assert context.get_markdown() == "That could be cause for concern."
    record = context.tracker.records[0]
    assert record.applied is False
    assert record.broken_word == "be cause"
    assert "Transformer rejected" in record.reason
    assert "spaced=-0.200" in record.reason
