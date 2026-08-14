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


def test_unresolved_disagreement_retries_with_stronger_model():
    scores = {
        ("서울에 그런 곳도 있었니?", "Seattle also had such a place?"): 0.08,
        ("서울에 그런 곳도 있었니?", "Some unrelated answer."): 0.06,
        ("Seattle also had such a place?", "Some unrelated answer."): 0.10,
        ("서울에 그런 곳도 있었니?", "Was there a place like that in Seoul?"): 0.14,
    }
    output, report = enforce_translation_agreement(
        translated_document(), lambda request: "Some unrelated answer.",
        lambda left, right: scores[(left, right)],
        independent_model="independent",
        retry_translate=lambda request: "Was there a place like that in Seoul?",
        retry_model="stronger",
    )
    check = report["checks"][0]
    assert report["passed"] is True
    assert check["selected"] == "retry"
    assert check["source_retry_similarity"] == 0.14
    assert output["segments"][0]["translated_text"].endswith("Seoul?")
    assert output["segments"][0]["provenance"][-1]["method"] == "stronger-model-retry"


def test_stronger_service_failure_is_reported_as_unresolved():
    def unavailable(_request):
        raise RuntimeError("service unavailable")

    _output, report = enforce_translation_agreement(
        translated_document(), lambda request: "Some unrelated answer.",
        lambda left, right: 0.1, independent_model="independent",
        retry_translate=unavailable, retry_model="stronger",
    )
    check = report["checks"][0]
    assert report["passed"] is False
    assert check["passed"] is False
    assert check["retry_error"].startswith("RuntimeError: service unavailable")


def test_source_language_echo_cannot_be_promoted_as_english():
    scores = {
        ("서울에 그런 곳도 있었니?", "Seattle also had such a place?"): 0.08,
        ("서울에 그런 곳도 있었니?", "서울에 그런 곳도 있었니?"): 1.0,
        ("Seattle also had such a place?", "서울에 그런 곳도 있었니?"): 0.1,
        ("서울에 그런 곳도 있었니?", "Was there a place like that in Seoul?"): 0.14,
    }
    output, report = enforce_translation_agreement(
        translated_document(), lambda request: "서울에 그런 곳도 있었니?",
        lambda left, right: scores[(left, right)], independent_model="independent",
        retry_translate=lambda request: "Was there a place like that in Seoul?",
        retry_model="stronger",
    )
    check = report["checks"][0]
    assert "independent_output_contract_failure" in check["issues"]
    assert check["selected"] == "retry"
    assert output["segments"][0]["translated_text"].endswith("Seoul?")


def test_stronger_model_can_confirm_a_valid_primary_paraphrase():
    primary = "Where are you?"
    document = translated_document(primary)
    document["segments"][0]["source_text"] = "어디야?"
    scores = {
        ("어디야?", primary): 0.79,
        ("어디야?", "Where is it?"): 0.75,
        (primary, "Where is it?"): 0.60,
    }
    output, report = enforce_translation_agreement(
        document, lambda request: "Where is it?",
        lambda left, right: scores[(left, right)], independent_model="independent",
        retry_translate=lambda request: primary, retry_model="stronger",
    )
    assert report["passed"] is True
    assert report["checks"][0]["selected"] == "retry-confirmed-primary"
    assert output["segments"][0]["translated_text"] == primary


def test_systematically_invalid_independent_backend_stops_expensive_retries():
    document = translated_document()
    document["segments"] = [
        {**document["segments"][0], "id": f"group-{index}",
         "semantic_group_id": f"group-{index}"}
        for index in range(4)
    ]
    retry_calls = []

    def retry(_request):
        retry_calls.append("called")
        return "A valid retry."

    _output, report = enforce_translation_agreement(
        document, lambda request: request.current_text,
        lambda left, right: 1.0, independent_model="broken",
        retry_translate=retry, retry_model="stronger",
    )
    assert report["passed"] is False
    assert report["backend_issue"] == "independent_backend_output_contract_failure"
    assert report["invalid_independent_candidate_rate"] == 1.0
    assert retry_calls == []


def test_late_backend_health_failure_rolls_back_tentative_promotions():
    document = translated_document("Original primary.")
    document["segments"] = [
        {**document["segments"][0], "id": f"group-{index}",
         "semantic_group_id": f"group-{index}"}
        for index in range(10)
    ]

    def independent(request):
        index = int(request.group_id.rsplit("-", 1)[1])
        return request.current_text if index >= 6 else "Better independent."

    def score(left, right):
        if right == "Better independent.":
            return 0.30
        if left == "Original primary." and right == "Better independent.":
            return 0.10
        return 0.10

    output, report = enforce_translation_agreement(
        document, independent, score, independent_model="unstable",
    )
    assert report["backend_issue"] == "independent_backend_output_contract_failure"
    assert report["failed_group_count"] == 10
    assert all(check["selected"] == "primary" for check in report["checks"])
    assert all(segment["translated_text"] == "Original primary." for segment in output["segments"])
