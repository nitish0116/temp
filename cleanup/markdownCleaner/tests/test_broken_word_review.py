"""Contracts for cached broken-word discovery and explicit promotion."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from markdownCleaner.commands.broken_word_review import (
    ReviewResources,
    promote_review,
    records_from_cache,
    run_review_pipeline,
    scan_markdown_file,
    update_cache,
)
from markdownCleaner.modules.core.config import PipelineConfig
from markdownCleaner.modules.symspell.decisions import BrokenWordDecisions
from markdownCleaner.modules.symspell.dictionary import DictionaryManager
from markdownCleaner.modules.symspell.frequency import WordfreqScorer
from markdownCleaner.modules.symspell.settings import SymSpellSettings


SCORES = {
    "because": 6.03,
    "be cause": 5.33,
    "inside": 5.19,
    "in side": 5.49,
    "noone": 1.74,
    "no one": 6.10,
    "experience": 5.27,
    "experi ence": 1.79,
}


def resources(*, decisions: BrokenWordDecisions | None = None) -> ReviewResources:
    dictionary = DictionaryManager()
    for word in (
        "be",
        "cause",
        "because",
        "in",
        "side",
        "inside",
        "no",
        "one",
        "noone",
        "experience",
    ):
        dictionary.add_word(word, 200_000)
    scorer = WordfreqScorer(
        lookup=lambda word, _language, **_kwargs: SCORES.get(word.casefold(), 0.0)
    )
    return ReviewResources(
        dictionary=dictionary,
        scorer=scorer,
        settings=SymSpellSettings(wordfreq_minimum_zipf=2.5),
        decisions=decisions or BrokenWordDecisions(),
        fingerprint="test-lexicon",
    )


def test_scanner_keeps_only_plausible_editable_word_boundaries(tmp_path):
    source = tmp_path / "book.md"
    source.write_text(
        "He stopped be cause it mattered.\n"
        "`be cause` stays protected.\n"
        "```\nbe cause in code\n```\n"
        "Ordinary adjacent prose remains intact.\n",
        encoding="utf-8",
    )

    result = scan_markdown_file(source, resources())

    assert [item["key"] for item in result["pairs"]] == ["be cause"]
    assert result["pairs"][0]["count"] == 1
    assert result["pairs"][0]["contexts"][0]["line"] == 1


def test_cached_records_split_confident_and_ambiguous_unresolved_pairs(tmp_path):
    cached = [
        (
            "book.md",
            {
                "sha256": "unique",
                "words": {
                        "because": 20,
                        "inside": 3,
                        "noone": 0,
                },
                "pairs": [
                    {
                        "key": "be cause",
                        "count": 2,
                        "variants": {"be cause": 2},
                        "contexts": [{"line": 1, "text": "be cause"}],
                    },
                    {
                        "key": "in side",
                        "count": 3,
                        "variants": {"in side": 3},
                        "contexts": [{"line": 2, "text": "in side"}],
                    },
                    {
                        "key": "no one",
                        "count": 6,
                        "variants": {"no one": 6},
                        "contexts": [{"line": 3, "text": "no one"}],
                    },
                ],
            },
        )
    ]

    main, ambiguous = records_from_cache(
        tmp_path,
        cached,
        resources(),
        max_contexts=2,
        output_base=tmp_path,
    )

    assert [(item["broken_word"], item["status"]) for item in main["candidates"]] == [
        ("be cause", "accepted"),
        ("no one", "rejected"),
    ]
    assert [item["broken_word"] for item in ambiguous["candidates"]] == [
        "in side"
    ]
    assert main["candidates"][0]["evidence"]["joined_occurrences"] == 20
    assert main["insufficient_evidence_skipped"] == 0
    assert ambiguous["insufficient_evidence_skipped"] == 0


def test_low_counts_cannot_trigger_lexical_only_decisions(tmp_path):
    """Strong Zipf scores cannot decide a pair without corpus support."""
    cached = [
        (
            "book.md",
            {
                "sha256": "unique",
                "words": {"inside": 1, "noone": 0},
                "pairs": [
                    {
                        "key": "in side",
                        "count": 1,
                        "variants": {"in side": 1},
                        "contexts": [],
                    },
                    {
                        "key": "no one",
                        "count": 2,
                        "variants": {"no one": 2},
                        "contexts": [],
                    },
                ],
            },
        )
    ]

    main, ambiguous = records_from_cache(
        tmp_path,
        cached,
        resources(),
        max_contexts=2,
        output_base=tmp_path,
    )

    assert main["candidates"] == []
    assert ambiguous["candidates"] == []
    assert main["insufficient_evidence_skipped"] == 2


def test_portable_cache_reuses_only_unchanged_file_content(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    source = library / "book.md"
    source.write_text("It happened be cause of this.", encoding="utf-8")
    cache_path = tmp_path / "records" / "cache.json.gz"

    _, first = update_cache(library, cache_path, resources())
    _, second = update_cache(library, cache_path, resources())
    source.write_text("It happened be cause of that.", encoding="utf-8")
    _, third = update_cache(library, cache_path, resources())

    assert first == {"cache_hits": 0, "cache_misses": 1, "cache_removed": 0}
    assert second == {"cache_hits": 1, "cache_misses": 0, "cache_removed": 0}
    assert third == {"cache_hits": 0, "cache_misses": 1, "cache_removed": 0}
    with gzip.open(cache_path, "rt", encoding="utf-8") as handle:
        cache = json.load(handle)
    assert cache["library"] == "../library"
    assert list(cache["files"]) == ["book.md"]


def test_promotion_updates_decisions_and_ignores_unresolved_candidates(tmp_path):
    review = tmp_path / "review.json"
    decisions = tmp_path / "broken_word_decisions.json"
    review.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "broken_word": "be cause",
                        "replacement": "because",
                        "status": "accepted",
                        "blocked_previous": ["could"],
                    },
                    {"broken_word": "in side", "status": "rejected"},
                    {
                        "broken_word": "for ever",
                        "replacement": "forever",
                        "status": "review",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    decisions.write_text(
        json.dumps(
            {
                "accepted": {"in side": "inside"},
                "rejected": ["be cause", "to one"],
            }
        ),
        encoding="utf-8",
    )

    result = promote_review(review, decisions)
    stored = json.loads(decisions.read_text(encoding="utf-8"))

    assert result == {"promoted": 2, "ignored": 1}
    assert stored["accepted"] == {
        "be cause": {
            "replacement": "because",
            "blocked_previous": ["could"],
            "blocked_following": [],
        }
    }
    assert stored["rejected"] == ["in side", "to one"]
    loaded = BrokenWordDecisions.load(decisions)
    assert loaded.accepted_replacement("be", "cause") == "because"
    assert loaded.accepted_replacement(
        "be", "cause", previous="could"
    ) is None


def test_sentence_initial_candidate_uses_case_neutral_replacement(tmp_path):
    cached = [
        (
            "book.md",
            {
                "sha256": "unique",
                "words": {"because": 30},
                "pairs": [
                    {
                        "key": "be cause",
                        "count": 1,
                        "variants": {"Be cause": 1},
                        "contexts": [{"line": 1, "text": "Be cause matters."}],
                    }
                ],
            },
        )
    ]

    main, _ = records_from_cache(
        tmp_path,
        cached,
        resources(),
        max_contexts=1,
        output_base=tmp_path,
    )

    assert main["candidates"][0]["broken_word"] == "Be cause"
    assert main["candidates"][0]["replacement"] == "because"


def test_integrated_pipeline_writes_records_and_reuses_cache(tmp_path):
    dictionary = tmp_path / "dictionary.txt"
    dictionary.write_text(
        "be 300000\ncause 200000\nbecause 500000\n",
        encoding="utf-8",
    )
    library = tmp_path / "library"
    library.mkdir()
    (library / "book.md").write_text(
        "It happened be cause of that.",
        encoding="utf-8",
    )
    config = PipelineConfig(
        {
            "symspell": {
                "dictionary": "dictionary.txt",
                "broken_word_decisions": "data/decisions.json",
                "wordfreq_enabled": False,
            }
        },
        base_dir=tmp_path,
    )
    main = tmp_path / "data/review.json"
    ambiguous = tmp_path / "data/ambiguous.json"
    cache = tmp_path / "data/cache.json.gz"

    first = run_review_pipeline(
        library,
        config=config,
        main_output=main,
        ambiguous_output=ambiguous,
        cache_path=cache,
    )
    second = run_review_pipeline(
        library,
        config=config,
        main_output=main,
        ambiguous_output=ambiguous,
        cache_path=cache,
    )

    assert first["cache_misses"] == 1
    assert second["cache_hits"] == 1
    assert json.loads(main.read_text(encoding="utf-8"))["candidates"] == []
    unresolved = json.loads(ambiguous.read_text(encoding="utf-8"))
    assert unresolved["candidates"] == []
    assert unresolved["insufficient_evidence_skipped"] == 1
    assert first["insufficient"] == 1
    assert unresolved["source_root"] == "../library"
