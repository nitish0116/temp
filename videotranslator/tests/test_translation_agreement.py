"""Tests for independent translation agreement and stronger-candidate promotion."""

from pathlib import Path

from videotranslator.commands.qa_translation_agreement import (
    agreement_issues,
    enforce_translation_agreement,
)


def translated_document(text: str = "Seattle also had such a place?") -> dict:
    """Return one valid translated semantic group for agreement tests."""
    return {
        "schema_version": 1, "artifact_type": "canonical_timed_text",
        "stage": "translated", "source_language": "ko", "output_language": "en",
        "language_probability": 1.0, "metadata": {},
        "segments": [{
            "id": "group-1", "semantic_group_id": "group-1",
            "source_cue_ids": [1], "start": 1.0, "end": 3.0,
            "source_text": "서울에 그런 곳도 있었니?", "translated_text": text,
            "speaker": "speaker-01", "words": [], "confidence": {},
            "provenance": [], "metadata": {},
        }],
    }


def test_low_evidence_identical_candidates_do_not_create_false_confidence():
    issues = agreement_issues(
        "source", "wrong", "wrong", 0.05, 0.05, 1.0,
    )
    assert "low_evidence_consensus" in issues


def test_number_and_polarity_disagreements_are_explicit():
    issues = agreement_issues(
        "source", "I did not take 2.", "I took 3.", 0.5, 0.5, 0.9,
    )
    assert {"number_disagreement", "polarity_disagreement"} <= set(issues)


def test_higher_source_similarity_promotes_independent_candidate(tmp_path: Path):
    scores = {
        ("서울에 그런 곳도 있었니?", "Seattle also had such a place?"): 0.08,
        ("서울에 그런 곳도 있었니?", "Was there a place like that in Seoul?"): 0.14,
        ("Seattle also had such a place?", "Was there a place like that in Seoul?"): 0.50,
    }
    calls = []

    def translate(request):
        calls.append(request.group_id)
        return "Was there a place like that in Seoul?"

    output, report = enforce_translation_agreement(
        translated_document(), translate, lambda left, right: scores[(left, right)],
        independent_model="stronger", cache_directory=tmp_path,
    )
    assert report["passed"] is True
    assert report["checks"][0]["selected"] == "independent"
    assert output["segments"][0]["translated_text"].endswith("Seoul?")
    assert output["segments"][0]["provenance"][-1]["stage"] == "translation-agreement"

    second, second_report = enforce_translation_agreement(
        translated_document(), translate, lambda left, right: scores[(left, right)],
        independent_model="stronger", cache_directory=tmp_path,
    )
    assert calls == ["agreement-group-1"]
    assert second_report["checks"][0]["cache_hit"] is True
    assert second["segments"][0]["translated_text"].endswith("Seoul?")


def test_unresolved_disagreement_blocks_promotion():
    output, report = enforce_translation_agreement(
        translated_document(), lambda request: "Some unrelated answer.",
        lambda left, right: 0.1, independent_model="independent",
    )
    assert report["passed"] is False
    assert report["failed_group_count"] == 1
    assert output["segments"][0]["translated_text"].startswith("Seattle")
