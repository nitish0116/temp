"""Tests for calibrated, fail-closed automatic translation review."""

import pytest

from videotranslator.commands.qa_machine_review import (
    CalibrationFixture,
    MachineReviewPolicy,
    calibrate_machine_reviewer,
    review_candidate,
)


POLICY = MachineReviewPolicy()


def test_machine_review_requires_all_gates():
    result = review_candidate(
        "Dalsan-ri? 서울에 그런 곳도 있었니?",
        "Dalsan-ri? Was there such a place in Seoul?",
        {
            "primary": "Dalsan-ri? Was there such a place in Seoul?",
            "dedicated_mt": "Dalsan-ri? Was a place like that in Seoul?",
            "speech_translation": "Was there such a place in Seoul?",
        },
        source_language="ko", target_language="en",
        estimate_quality=lambda source, target: 0.95,
        semantic_similarity=lambda left, right: 0.94 if "Dalsan-ri" in right else 0.70,
        back_translate=lambda text, source, target: "Dalsan-ri? 서울에 그런 곳도 있었니?",
        policy=POLICY, calibration_id="reviewed-fixtures-v1",
    )
    assert result["status"] == "machine_verified"
    assert result["agreeing_routes"] == ["primary", "dedicated_mt"]
    assert result["calibration_id"] == "reviewed-fixtures-v1"


def test_machine_review_fails_closed_on_omission_despite_high_model_scores():
    result = review_candidate(
        "Dalsan-ri? 서울에 그런 곳도 있었니?",
        "Was there such a place in Seoul?",
        {
            "primary": "Was there such a place in Seoul?",
            "dedicated_mt": "Was there such a place in Seoul?",
        },
        source_language="ko", target_language="en",
        estimate_quality=lambda source, target: 0.99,
        semantic_similarity=lambda left, right: 0.99,
        back_translate=lambda text, source, target: "서울에 그런 곳도 있었니?",
        policy=POLICY, calibration_id="reviewed-fixtures-v1",
    )
    assert result["status"] == "unresolved"
    assert "deterministic_integrity" in {failure["type"] for failure in result["failures"]}


def test_machine_review_requires_two_independent_routes_and_round_trip():
    result = review_candidate(
        "かわいい", "Cute", {"primary": "Cute", "dedicated_mt": "Lovely"},
        source_language="ja", target_language="en",
        estimate_quality=lambda source, target: 0.95,
        semantic_similarity=lambda left, right: 0.40,
        back_translate=lambda text, source, target: "別の意味",
        policy=POLICY, calibration_id="reviewed-fixtures-v1",
    )
    failures = {failure["type"] for failure in result["failures"]}
    assert failures == {"insufficient_independent_agreement", "round_trip_score"}


def test_adversarial_calibration_blocks_activation_when_semantic_defect_scores_high():
    fixture = CalibrationFixture(
        "cute", "かわいい", "Cute", ("Lovely", "Cruel"),
    )
    scores = {"Cute": 0.96, "Lovely": 0.90, "Cruel": 0.10}
    report = calibrate_machine_reviewer(
        [fixture], lambda source, target: scores[target], POLICY,
    )
    assert report["passed"] is False
    assert report["results"][0]["rejected"][0]["blocked"] is False


def test_adversarial_calibration_accepts_good_and_rejects_all_corruptions():
    fixtures = [CalibrationFixture(
        "seoul", "서울에 그런 곳도 있었니?", "Was there such a place in Seoul?",
        ("Was there such a place?",),
    )]
    report = calibrate_machine_reviewer(
        fixtures,
        lambda source, target: 0.96 if "Seoul" in target else 0.20,
        POLICY,
    )
    assert report["passed"] is True


@pytest.mark.parametrize("field", ["minimum_quality_score", "minimum_semantic_similarity", "minimum_round_trip_score"])
def test_policy_rejects_invalid_score_thresholds(field):
    with pytest.raises(ValueError, match=field):
        MachineReviewPolicy(**{field: 1.1})
