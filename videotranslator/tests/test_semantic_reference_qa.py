"""Tests for optional headless semantic-reference promotion checks."""

import json
from pathlib import Path

import pytest

from videotranslator.commands.create_subtitles import parse_args
from videotranslator.commands.qa_semantic_reference import (
    cue_at,
    evaluate_manifest,
    evaluate_semantic_references,
    references_from_manifest,
    semantic_group_text,
)


def canonical(text: str = "There was a place like that in Seoul?") -> dict:
    """Build one valid canonical translated cue for isolated QA tests."""
    return {
        "schema_version": 1,
        "artifact_type": "canonical_timed_text",
        "stage": "translated",
        "source_language": "ko",
        "output_language": "en",
        "language_probability": 1.0,
        "metadata": {},
        "segments": [{
            "id": "cue-1", "semantic_group_id": "group-1",
            "source_cue_ids": [1], "start": 8.0, "end": 10.0,
            "source_text": "source", "translated_text": text,
            "speaker": "speaker-01", "words": [], "confidence": {},
            "provenance": [], "metadata": {},
        }],
    }


def reference(**overrides) -> dict:
    """Build a reviewed reference centered inside the synthetic cue."""
    return {
        "timestamp_seconds": 9.0,
        "required_terms": ["Seoul"],
        "forbidden_terms": ["Seattle"],
        **overrides,
    }


def test_reference_gate_passes_required_terms_and_absent_forbidden_terms():
    report = evaluate_semantic_references(canonical(), [reference()])
    assert report["passed"] is True
    assert report["failed_reference_count"] == 0
    assert report["checks"][0]["cue_id"] == "cue-1"


def test_reference_gate_blocks_semantic_substitution():
    report = evaluate_semantic_references(
        canonical("Seattle also had such a place?"), [reference()]
    )
    assert report["passed"] is False
    assert report["checks"][0]["missing_required_terms"] == ["Seoul"]
    assert report["checks"][0]["present_forbidden_terms"] == ["Seattle"]


def test_reference_gate_blocks_a_timestamp_without_a_cue():
    report = evaluate_semantic_references(
        canonical(), [reference(timestamp_seconds=20.0)]
    )
    assert report["passed"] is False
    assert "missing_cue_at_reference_timestamp" in report["checks"][0]["issues"]


def test_cue_selection_uses_the_containing_timing_window():
    assert cue_at(canonical(), 9.0)["id"] == "cue-1"
    assert cue_at(canonical(), 12.0) is None


def test_reference_checks_the_complete_semantic_group():
    document = canonical("There was a place")
    document["segments"].append({
        **document["segments"][0],
        "id": "cue-2", "start": 10.0, "end": 11.0,
        "translated_text": "like that in Seoul?",
    })
    assert semantic_group_text(document, document["segments"][0]) == (
        "There was a place like that in Seoul?"
    )
    assert evaluate_semantic_references(document, [reference()])["passed"] is True


def test_manifest_supports_direct_sidecar_and_review_sample_selection(tmp_path: Path):
    direct = {"schema_version": 1, "references": [reference()]}
    assert references_from_manifest(direct) == direct["references"]

    review = {"samples": [{
        "output_directory": "episode",
        "manual_review": {"verified_defects": [reference()]},
    }]}
    path = tmp_path / "review.json"
    path.write_text(json.dumps(review), encoding="utf-8")
    assert evaluate_manifest(canonical(), path, "episode")["passed"] is True
    with pytest.raises(ValueError, match="No semantic references"):
        evaluate_manifest(canonical(), path, "different-episode")


def test_subtitle_cli_accepts_semantic_reference_sidecar():
    args = parse_args(["video.mp4", "--semantic-reference", "review.json"])
    assert args.semantic_reference == Path("review.json")
