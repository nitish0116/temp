"""Offline regression contracts for canonical subtitle quality improvements."""

import json
import re
from pathlib import Path

from videotranslator.commands.build_clean_transcript import build_clean_transcript
from videotranslator.commands.canonical_timed_text import validate_canonical_timed_text
from videotranslator.commands.map_translation_cues import map_translated_groups
from videotranslator.commands.qa_transcript import analyze
from videotranslator.commands.translate_contextual import translate_contextual


FIXTURE = Path(__file__).parent / "fixtures" / "subtitle_quality_baseline.json"


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def mapped_fixture(target: str = "First synthetic sentence. Second synthetic sentence.") -> dict:
    clean = build_clean_transcript({
        "language": "en", "language_probability": 1.0,
        "task": "transcribe", "output_language": "en",
        "segments": [{
            "id": "source-1", "start": 0.0, "end": 4.0,
            "text": "One complete synthetic source thought.",
            "speaker": "speaker-01",
            "words": [
                {"start": 0.0, "end": 1.0, "word": "One"},
                {"start": 1.4, "end": 2.0, "word": "complete"},
                {"start": 2.1, "end": 4.0, "word": "thought."},
            ],
        }],
    })
    translated = translate_contextual(clean, "es", "fixture-model", lambda request: target)
    return map_translated_groups(translated, maximum_characters=32)


def test_frozen_fixture_exercises_every_original_blocking_category():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    report = analyze(
        fixture["candidate"], 12.0,
        source_transcript=fixture["source_evidence"],
        diarization_report=fixture["diarization_evidence"],
    )
    assert set(fixture["expected_issue_types"]) <= set(report["issue_counts"])


def test_display_mapping_has_valid_order_without_overlap():
    mapped = mapped_fixture()
    timings = [(cue["start"], cue["end"]) for cue in mapped["segments"]]
    assert all(start < end for start, end in timings)
    assert all(left[1] <= right[0] for left, right in zip(timings, timings[1:]))


def test_display_mapping_preserves_all_target_text_exactly_once():
    target = "First synthetic sentence. Second synthetic sentence."
    mapped = mapped_fixture(target)
    reconstructed = " ".join(cue["translated_text"] for cue in mapped["segments"])
    assert compact(reconstructed) == compact(target)


def test_every_display_cue_retains_canonical_lineage():
    mapped = mapped_fixture()
    validate_canonical_timed_text(mapped)
    for cue in mapped["segments"]:
        assert cue["id"]
        assert cue["semantic_group_id"] == "semantic-0001"
        assert cue["source_cue_ids"] == ["source-1"]
        assert cue["speaker"] == "speaker-01"
        assert cue["words"]
        assert cue["provenance"][-1]["stage"] == "display-cue-mapping"


def test_mapping_is_deterministic_for_identical_input():
    first = mapped_fixture()
    second = mapped_fixture()
    assert first == second


def test_qa_contract_blocks_short_long_fast_and_overlapping_cues():
    transcript = {"segments": [
        {"start": 0.0, "end": 13.0, "text": "long"},
        {"start": 12.9, "end": 13.0, "text": "far too fast for its timing"},
    ]}
    report = analyze(transcript, maximum_duration=12.0)
    assert {"long_duration", "overlap", "short_duration", "fast_reading_speed"} <= set(report["issue_counts"])
