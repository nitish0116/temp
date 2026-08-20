"""Tests for calibrated, fail-closed automatic translation review."""

import pytest

from videotranslator.commands.qa_machine_review import (
    CalibrationFixture,
    GroundingClaim,
    MachineReviewPolicy,
    SourceGroundingRule,
    TerminologyRule,
    calibrate_machine_reviewer,
    entity_consensus_issues,
    grounding_claim_issues,
    review_candidate,
    terminology_consensus_issues,
    terminology_issues,
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


def test_grounded_evidence_can_override_low_quality_score():
    source = "条约来源"
    translation = "The Treaty of Shimonoseki was signed with Japan."
    routes = {
        "primary": translation,
        "dedicated_mt": "Japan signed the Treaty of Shimonoseki.",
    }
    result = review_candidate(
        source, translation, routes,
        source_language="zh", target_language="en",
        estimate_quality=lambda source, target: 0.28,
        semantic_similarity=lambda left, right: 0.95,
        back_translate=lambda text, source, target: "条约来源",
        policy=POLICY, calibration_id="grounded-v1",
        terminology_rules=(TerminologyRule(
            "treaty", (source,), ("Japan", "Treaty", "Shimonoseki"),
        ),),
        grounding_rules=(SourceGroundingRule(
            "treaty", (source,), (GroundingClaim(
                "signing", ("signed with Japan", "Japan signed"),
            ),),
        ),),
    )
    assert result["status"] == "machine_verified"
    assert result["quality_overridden"] is True
    assert result["quality_score"] == 0.28


def test_low_quality_remains_blocking_without_source_grounding():
    result = review_candidate(
        "未知来源", "A plausible translation.",
        {"primary": "A plausible translation.", "dedicated_mt": "A plausible translation."},
        source_language="zh", target_language="en",
        estimate_quality=lambda source, target: 0.28,
        semantic_similarity=lambda left, right: 0.95,
        back_translate=lambda text, source, target: "未知来源",
        policy=POLICY, calibration_id="grounded-v1",
    )
    assert result["status"] == "unresolved"
    assert result["quality_overridden"] is False
    assert {failure["type"] for failure in result["failures"]} == {"quality_score"}


def test_source_triggered_terminology_blocks_plausible_wrong_synonym():
    rules = (TerminologyRule("cute", ("かわいい",), ("cute",), ("lovely",)),)
    assert terminology_issues("かわいい", "It is cute.", rules) == []
    issues = terminology_issues("かわいい", "It is lovely.", rules)
    assert issues[0]["missing"] == ["cute"]
    assert issues[0]["forbidden"] == ["lovely"]


def test_entity_consensus_blocks_minority_place_name_variant():
    routes = {
        "primary": "There was a place like that in Seattle?",
        "dedicated_mt": "Was there such a place in Seoul?",
        "speech": "There was a similar place in Seoul?",
    }
    assert entity_consensus_issues("Was there such a place in Seoul?", routes) == []
    assert entity_consensus_issues("There was a place like that in Seattle?", routes) == [{
        "type": "entity_consensus_mismatch", "missing": ["Seoul"],
    }]


def test_source_grounded_terms_require_independent_route_consensus():
    source = "Taiwan Penghu Liaodong treaty source"
    rule = TerminologyRule(
        "shimonoseki", ("treaty source",),
        ("Japan", "Treaty", "Shimonoseki"),
    )
    routes = {
        "primary": "Japan signed the Treaty of Shimonoseki.",
        "dedicated_mt": "The Treaty of Shimonoseki was signed with Japan.",
        "speech": "A treaty was signed.",
    }
    assert terminology_consensus_issues(
        source, routes["primary"], routes, (rule,), minimum_routes=2,
    ) == []


def test_source_grounded_terms_report_single_route_assertions():
    source = "Taiwan Penghu Liaodong treaty source"
    rule = TerminologyRule(
        "shimonoseki", ("treaty source",),
        ("Japan", "Treaty", "Shimonoseki"),
    )
    routes = {
        "primary": "Japan signed the Treaty of Shimonoseki.",
        "dedicated_mt": "An agreement was signed.",
        "speech": "A treaty was signed.",
    }
    issues = terminology_consensus_issues(
        source, routes["primary"], routes, (rule,), minimum_routes=2,
    )
    assert issues == [{
        "type": "terminology_consensus_mismatch",
        "rule_id": "shimonoseki",
        "minimum_routes": 2,
        "unsupported": [
            {"term": "Japan", "supporting_routes": ["primary"]},
            {"term": "Shimonoseki", "supporting_routes": ["primary"]},
        ],
    }]


def test_grounding_claim_requires_relation_and_route_support():
    source = "treaty source"
    rule = SourceGroundingRule("treaty", (source,), (GroundingClaim(
        "signing", ("signed with Japan", "Japan signed"),
        ("did not sign", "never signed"),
    ),))
    routes = {
        "primary": "The Treaty of Shimonoseki was signed with Japan.",
        "dedicated_mt": "Japan signed the Treaty of Shimonoseki.",
        "speech": "A treaty was signed.",
    }
    assert grounding_claim_issues(
        source, routes["primary"], routes, (rule,), minimum_routes=2,
    ) == []


@pytest.mark.parametrize("translation", [
    "Japan did not sign the Treaty of Shimonoseki.",
    "The Treaty of Shimonoseki signed Japan.",
])
def test_grounding_claim_blocks_negation_and_role_swap(translation):
    source = "treaty source"
    rule = SourceGroundingRule("treaty", (source,), (GroundingClaim(
        "signing", ("signed with Japan", "Japan signed"),
        ("did not sign", "never signed"),
    ),))
    issues = grounding_claim_issues(
        source, translation, {"primary": translation}, (rule,), minimum_routes=2,
    )
    assert issues[0]["type"] == "source_grounding_mismatch"
    assert issues[0]["claim_id"] == "signing"


def test_machine_review_applies_terminology_and_entity_gates():
    result = review_candidate(
        "서울에 그런 곳도 있었니?", "There was a place like that in Seattle?",
        {
            "primary": "There was a place like that in Seattle?",
            "dedicated_mt": "Was there such a place in Seoul?",
            "speech": "There was a similar place in Seoul?",
        },
        source_language="ko", target_language="en",
        estimate_quality=lambda source, target: 0.99,
        semantic_similarity=lambda left, right: 0.99,
        back_translate=lambda text, source, target: "서울에 그런 곳도 있었니?",
        policy=POLICY, calibration_id="reviewed-fixtures-v2",
        terminology_rules=(TerminologyRule(
            "seoul", ("서울",), ("Seoul",), ("Seattle",),
        ),),
    )
    assert result["status"] == "unresolved"
    issue_types = {
        issue["type"] for failure in result["failures"]
        if failure["type"] == "deterministic_integrity" for issue in failure["issues"]
    }
    assert issue_types == {
        "terminology_mismatch", "entity_consensus_mismatch",
        "terminology_consensus_mismatch",
    }


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


def test_calibration_terminology_blocks_high_scoring_known_defect():
    fixture = CalibrationFixture(
        "cute", "かわいい", "Cute", ("Lovely",),
        required_terms=("cute",), forbidden_terms=("lovely",),
    )
    report = calibrate_machine_reviewer([fixture], lambda source, target: 0.95, POLICY)
    assert report["passed"] is True
    assert report["results"][0]["rejected"][0]["deterministic_issues"][0]["type"] == "terminology_mismatch"


@pytest.mark.parametrize("field", ["minimum_quality_score", "minimum_semantic_similarity", "minimum_round_trip_score"])
def test_policy_rejects_invalid_score_thresholds(field):
    with pytest.raises(ValueError, match=field):
        MachineReviewPolicy(**{field: 1.1})
