"""Focused behavior tests for the refactored SymSpell components."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from markdownCleaner.modules.core.config import PipelineConfig
from markdownCleaner.modules.core.context import ProcessingContext
from markdownCleaner.modules.symspell.broken_words import (
    BrokenWordEvaluator,
    BrokenWordMerger,
    MergeDecision,
    MergeEvidence,
    MergeEvidenceKind,
)
from markdownCleaner.modules.symspell.corrector import (
    SpellCorrection,
    SpellCorrector,
)
from markdownCleaner.modules.symspell.dictionary import DictionaryManager
from markdownCleaner.modules.symspell.engine import SymSpellEngine
from markdownCleaner.modules.symspell.frequency import WordfreqScorer
from markdownCleaner.modules.symspell.settings import SymSpellSettings
from markdownCleaner.modules.symspell.vocabulary import VocabularyInventory
from markdownCleaner.modules.symspell.vocabulary_store import (
    merge_approved_words,
)


def _settings(**overrides) -> SymSpellSettings:
    values = {
        "wordfreq_enabled": False,
        "broken_word_merge_minimum_frequency": 50_000,
    }
    values.update(overrides)
    return SymSpellSettings(**values)


def _merger(
    dictionary: DictionaryManager,
    settings: SymSpellSettings | None = None,
) -> BrokenWordMerger:
    active_settings = settings or _settings()
    evaluator = BrokenWordEvaluator(
        dictionary,
        WordfreqScorer(enabled=False),
        active_settings,
    )
    return BrokenWordMerger(evaluator, active_settings)


def test_settings_are_immutable_and_parsed_to_typed_values():
    config = PipelineConfig(
        {
            "symspell": {
                "confidence_threshold": "94",
                "protected": ["Arthur Leywin"],
                "wordfreq_enabled": False,
            }
        }
    )

    settings = SymSpellSettings.from_config(config)

    assert settings.confidence_threshold == 94.0
    assert settings.protected_terms == ("Arthur Leywin",)
    with pytest.raises(FrozenInstanceError):
        settings.confidence_threshold = 90.0


def test_settings_reject_ambiguous_string_booleans():
    with pytest.raises(
        ValueError,
        match="symspell.wordfreq_enabled must be true or false",
    ):
        SymSpellSettings.from_config(
            PipelineConfig(
                {"symspell": {"wordfreq_enabled": "false"}}
            )
        )


def test_evaluator_returns_typed_evidence_and_final_joined_replacement():
    dictionary = DictionaryManager()
    dictionary.add_word("attention", 40_000_000)
    dictionary.add_word("expressionless", 55_876)
    evaluator = BrokenWordEvaluator(
        dictionary,
        WordfreqScorer(enabled=False),
        _settings(),
    )

    dictionary_decision = evaluator.evaluate("atten", "tion")
    corrected_join = evaluator.evaluate("expres", "sinless")

    assert isinstance(dictionary_decision, MergeDecision)
    assert isinstance(dictionary_decision.evidence, MergeEvidence)
    assert dictionary_decision.replacement == "attention"
    assert (
        dictionary_decision.evidence.kind
        is MergeEvidenceKind.DICTIONARY_FREQUENCY
    )
    assert corrected_join is not None
    assert corrected_join.replacement == "expressionless"
    assert (
        corrected_join.evidence.kind
        is MergeEvidenceKind.JOINED_OCR_CORRECTION
    )


def test_merger_resolves_overlaps_by_rank_and_protects_inline_code():
    dictionary = DictionaryManager()
    dictionary.add_word("attention", 40_000_000)
    dictionary.add_word("manama", 164_807)
    dictionary.add_word("manipulation", 4_744_083)
    merger = _merger(dictionary)

    result = merger.merge_inline(
        "mana ma nipulation fixed atten tion, not `atten tion`."
    )

    assert result.text == (
        "mana manipulation fixed attention, not `atten tion`."
    )
    assert {
        decision.broken_word for decision in result.decisions
    } == {"ma nipulation", "atten tion"}


def test_merger_preserves_exact_boundary_text_and_document_order():
    dictionary = DictionaryManager()
    dictionary.add_word("attention", 40_000_000)
    dictionary.add_word("situation", 30_000_000)
    merger = _merger(dictionary)

    result = merger.merge_inline("situa  tion before atten\ttion")

    assert result.text == "situation before attention"
    assert [decision.broken_word for decision in result.decisions] == [
        "situa  tion",
        "atten\ttion",
    ]


def test_merger_preserves_valid_be_cause_context():
    """A common joined word cannot override a valid noun phrase."""
    dictionary = DictionaryManager()
    dictionary.add_word("because", 40_000_000)
    merger = _merger(dictionary)

    result = merger.merge_inline(
        "It appeared to be cause for concern, be cause they left."
    )

    assert result.text == (
        "It appeared to be cause for concern, because they left."
    )
    assert [decision.broken_word for decision in result.decisions] == [
        "be cause"
    ]


def test_cross_block_merger_syncs_segments_and_skips_protected_blocks(
    tmp_path,
):
    source = tmp_path / "sample.md"
    source.write_text(
        "l0ve inner ener \n\n"
        "gy was used.\n\n"
        "# ener\n\n"
        "gy remains separate after a heading.\n\n"
        "```text\n"
        "ener\n\n"
        "gy\n"
        "```\n",
        encoding="utf-8",
    )
    config = PipelineConfig(
        {
            "paths": {"output_directory": str(tmp_path / "out")},
            "backup": {"enabled": False},
        }
    )
    context = ProcessingContext(config)
    context.load_markdown(source)
    context.segments[0].current_text = "love inner ener "

    dictionary = DictionaryManager()
    dictionary.add_word("energy", 50_000_000)
    changes = _merger(dictionary).merge_cross_blocks(context)
    cleaned = context.get_markdown()

    assert len(changes) == 1
    assert changes[0].decision.broken_word == "ener \n\ngy"
    assert "love inner energy was used." in cleaned
    assert "# ener\n\ngy remains separate after a heading." in cleaned
    assert "```text\nener\n\ngy\n```" in cleaned


def test_spell_corrector_filters_candidates_matches_case_and_skips_code():
    dictionary = DictionaryManager()
    dictionary.add_word("because", 10_000_000)
    engine = SymSpellEngine(max_edit_distance=2)
    engine.add_word("because", 10_000_000)
    corrector = SpellCorrector(
        dictionary,
        engine,
        _settings(
            confidence_threshold=92.0,
            minimum_candidate_frequency=1_000,
        ),
    )

    correction = corrector.correct_word("Becuse")
    cleaned = corrector.process_text("becuse `becuse`")

    assert isinstance(correction, SpellCorrection)
    assert correction.replacement == "Because"
    assert cleaned == "because `becuse`"


def test_symspell_engine_counts_adjacent_transposition_as_one_edit():
    engine = SymSpellEngine(max_edit_distance=1)
    engine.add_word("the", 10_000_000)

    candidates = engine.lookup("teh")

    assert engine.edit_distance("teh", "the") == 1
    assert [(candidate.corrected, candidate.distance) for candidate in candidates] == [
        ("the", 1)
    ]


@pytest.mark.parametrize("misspelling", ["helo", "helllo"])
def test_symspell_engine_finds_insertions_and_deletions_at_distance_limit(
    misspelling,
):
    engine = SymSpellEngine(max_edit_distance=1)
    engine.add_word("hello", 10_000_000)

    candidates = engine.lookup(misspelling)

    assert [(candidate.corrected, candidate.distance) for candidate in candidates] == [
        ("hello", 1)
    ]


def test_symspell_engine_rejects_negative_distance_and_keeps_peak_frequency():
    with pytest.raises(ValueError, match="cannot be negative"):
        SymSpellEngine(max_edit_distance=-1)

    engine = SymSpellEngine(max_edit_distance=1)
    engine.add_word("hello", 100)
    engine.add_word("HELLO", 10)

    assert engine.words["hello"] == 100


def test_vocabulary_inventory_collects_bounded_context_and_line_evidence():
    inventory = VocabularyInventory.collect(
        "Captain Degurechaff spoke.\n"
        "The armored vehicle followed Degurechaff."
    )

    assert inventory.counts["degurechaff"] == 2
    assert inventory.lines["degurechaff"] == [1, 2]
    assert inventory.contexts["armored"] == [("The", "vehicle")]


def test_reviewed_vocabulary_rejects_multiline_terms(tmp_path):
    target = tmp_path / "custom_words.json"

    with pytest.raises(ValueError, match="Invalid vocabulary word"):
        merge_approved_words(target, ["Arthur\nLeywin"])


def test_dictionary_loads_structured_glossary_without_metadata_keys(tmp_path):
    glossary = tmp_path / "custom_words.json"
    glossary.write_text(
        '{"_description": "Reviewed names", "words": ["Arthur Leywin"]}\n',
        encoding="utf-8",
    )

    dictionary = DictionaryManager(glossary_path=glossary)
    dictionary.load()

    assert dictionary.is_protected("Arthur Leywin")
    assert dictionary.is_protected("Arthur")
    assert dictionary.is_protected("Leywin")
    assert not dictionary.contains("_description")
    assert not dictionary.contains("words")
