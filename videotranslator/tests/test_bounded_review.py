"""Tests for durable, bounded human subtitle review artifacts."""

import pytest

from videotranslator.commands.bounded_review import (
    apply_review_decisions, attach_audio_clips, build_accepted_audit,
    build_bounded_review, build_reliability_audit, random_accepted_group_ids,
    stratified_accepted_group_ids, zero_error_sample_size,
)
from videotranslator.pipeline import main
from videotranslator.tests.test_translation_agreement import translated_document


def test_review_contains_only_unresolved_groups_and_versioned_approval_key():
    document = translated_document()
    segment = document["segments"][0]
    segment["metadata"]["dedicated_mt"] = {"text": "A place in Seoul?"}
    segment["metadata"]["speech_translation"] = {"text": "Where in Seoul?"}
    report = {
        "protocol_version": 3,
        "checks": [{
            "semantic_group_id": "group-1", "passed": False,
            "proposed_translation": "Was there a place like that in Seoul?",
            "error": "source_clause_omission", "reason": "blocked",
        }],
    }
    review = build_bounded_review(
        document, report, sample_id="sample", source_media="sample-data/source.mp4",
        media_sha256="a" * 64, adjudication_model="qwen3:14b",
    )
    assert review["review_item_count"] == 1 and review["status"] == "pending"
    item = review["items"][0]
    assert item["terminal_state"] == "unresolved"
    assert item["candidates"]["dedicated_mt"].endswith("Seoul?")
    assert item["proposed_correction"].endswith("Seoul?")
    assert len(item["approval_key"]) == 64


def test_review_omits_verified_groups():
    document = translated_document()
    report = {"protocol_version": 3, "checks": [{
        "semantic_group_id": "group-1", "passed": True,
    }]}
    review = build_bounded_review(
        document, report, sample_id="sample", source_media="source.mp4",
        media_sha256="b" * 64, adjudication_model="fixture",
    )
    assert review["items"] == [] and review["status"] == "resolved"


def test_review_audio_clips_are_relative_and_hashed(tmp_path):
    document = translated_document()
    report = {"protocol_version": 3, "checks": [{
        "semantic_group_id": "group-1", "passed": False,
        "proposed_translation": None, "error": None, "reason": "ambiguous",
    }]}
    review = build_bounded_review(
        document, report, sample_id="sample", source_media="sample-data/source.mp4",
        media_sha256="c" * 64, adjudication_model="fixture",
    )
    calls = []
    def extract(start, end, path):
        calls.append((start, end))
        path.write_bytes(b"audio")
    attach_audio_clips(review, tmp_path / "clips", "outputs/review/clips", extract)
    item = review["items"][0]
    assert calls == [(0.5, 3.5)]
    assert item["audio_clip"] == "outputs/review/clips/group-1.wav"
    assert len(item["audio_clip_sha256"]) == 64


def unresolved_review():
    document = translated_document()
    report = {"protocol_version": 3, "checks": [{
        "semantic_group_id": "group-1", "passed": False,
        "proposed_translation": None, "error": None, "reason": "ambiguous",
    }]}
    review = build_bounded_review(
        document, report, sample_id="sample", source_media="sample-data/source.mp4",
        media_sha256="d" * 64, adjudication_model="fixture",
    )
    return document, review


def test_matching_bilingual_decision_updates_text_and_provenance():
    document, review = unresolved_review()
    item = review["items"][0]
    decisions = {"sample_id": "sample", "decisions": [{
        "semantic_group_id": "group-1", "approval_key": item["approval_key"],
        "status": "bilingual_verified", "translation": "Was there such a place in Seoul?",
        "reviewer": "reviewer@example", "reviewed_at": "2026-08-16T02:00:00+05:30",
        "reviewer_languages": ["ko", "en"],
    }]}
    output, report = apply_review_decisions(document, review, decisions)
    assert report["passed"] is True and report["bilingual_verified_count"] == 1
    assert output["segments"][0]["translated_text"].endswith("Seoul?")
    assert output["segments"][0]["provenance"][-1]["stage"] == "bounded-bilingual-review"


def test_review_decision_rejects_wrong_key_or_stale_evidence():
    document, review = unresolved_review()
    decisions = {"sample_id": "sample", "decisions": [{
        "semantic_group_id": "group-1", "approval_key": "wrong",
        "status": "unable_to_verify", "reviewer": "reviewer@example",
        "reviewed_at": "2026-08-16T02:00:00Z",
        "reviewer_languages": ["en"],
    }]}
    with pytest.raises(ValueError, match="approval key"):
        apply_review_decisions(document, review, decisions)
    document["segments"][0]["source_text"] = "changed source"
    with pytest.raises(ValueError, match="stale"):
        apply_review_decisions(document, review, {"sample_id": "sample", "decisions": []})


def test_review_decision_rejects_stale_review_schema():
    document, review = unresolved_review()
    review["schema_version"] = 1
    with pytest.raises(ValueError, match="regenerate"):
        apply_review_decisions(document, review, {"sample_id": "sample", "decisions": []})


def test_review_decision_rejects_invalid_human_translation():
    document, review = unresolved_review()
    item = review["items"][0]
    decisions = {"sample_id": "sample", "decisions": [{
        "semantic_group_id": "group-1", "approval_key": item["approval_key"],
        "status": "bilingual_verified", "translation": "x", "reviewer": "reviewer@example",
        "reviewed_at": "2026-08-16T02:00:00Z",
        "reviewer_languages": ["ko", "en"],
    }]}
    with pytest.raises(ValueError, match="integrity checks"):
        apply_review_decisions(document, review, decisions)


def test_english_only_reviewer_cannot_semantically_verify_source():
    document, review = unresolved_review()
    item = review["items"][0]
    decisions = {"sample_id": "sample", "decisions": [{
        "semantic_group_id": "group-1", "approval_key": item["approval_key"],
        "status": "bilingual_verified", "translation": "Was there such a place in Seoul?",
        "reviewer": "reviewer@example", "reviewed_at": "2026-08-16T02:00:00Z",
        "reviewer_languages": ["en"],
    }]}
    with pytest.raises(ValueError, match="source language ko"):
        apply_review_decisions(document, review, decisions)


@pytest.mark.parametrize("status", ["target_language_reviewed", "unable_to_verify"])
def test_non_bilingual_decisions_are_recorded_but_do_not_promote(status):
    document, review = unresolved_review()
    item = review["items"][0]
    decisions = {"sample_id": "sample", "decisions": [{
        "semantic_group_id": "group-1", "approval_key": item["approval_key"],
        "status": status, "reviewer": "reviewer@example",
        "reviewed_at": "2026-08-16T02:00:00Z", "reviewer_languages": ["en"],
    }]}
    _output, report = apply_review_decisions(document, review, decisions)
    assert report["passed"] is False
    assert report["unresolved_count"] == 1
    assert report[f"{status}_count"] == 1


def test_target_language_review_requires_output_language_capability():
    document, review = unresolved_review()
    item = review["items"][0]
    decisions = {"sample_id": "sample", "decisions": [{
        "semantic_group_id": "group-1", "approval_key": item["approval_key"],
        "status": "target_language_reviewed", "reviewer": "reviewer@example",
        "reviewed_at": "2026-08-16T02:00:00Z", "reviewer_languages": ["ko"],
    }]}
    with pytest.raises(ValueError, match="output language en"):
        apply_review_decisions(document, review, decisions)


def test_accepted_audit_selection_is_deterministic_and_stratified():
    report = {"checks": [
        {"semantic_group_id": f"group-{index:02d}", "passed": index not in {4, 14}}
        for index in range(18)
    ]}
    selected = stratified_accepted_group_ids(report, sample_size=6)
    assert selected == stratified_accepted_group_ids(report, sample_size=6)
    numeric = [int(value.rsplit("-", 1)[1]) for value in selected]
    assert len(selected) == 6 and min(numeric) < 6 and max(numeric) >= 12
    assert not {"group-04", "group-14"} & set(selected)


def test_accepted_audit_contains_only_selected_verified_groups():
    document = translated_document()
    report = {"protocol_version": 3, "checks": [{
        "semantic_group_id": "group-1", "passed": True,
        "selected_translation": "Was there such a place in Seoul?",
        "proposed_translation": "Was there such a place in Seoul?",
        "error": None, "reason": "verified",
    }]}
    audit = build_accepted_audit(
        document, report, sample_id="sample", source_media="sample-data/source.mp4",
        media_sha256="e" * 64, adjudication_model="fixture", sample_size=8,
    )
    assert audit["artifact_type"] == "accepted_subtitle_audit"
    assert audit["selection"]["selected_size"] == 1
    assert audit["items"][0]["terminal_state"] == "adjudicator_verified"
    assert audit["items"][0]["review"]["status"] == "audit_pending"


def test_zero_error_reliability_sample_requires_59_reviews():
    assert zero_error_sample_size(0.95, 0.95) == 59


def test_seeded_random_selection_is_reproducible_and_excludes_rejections():
    report = {"checks": [
        {"semantic_group_id": f"group-{index:02d}", "passed": index != 7}
        for index in range(20)
    ]}
    selected = random_accepted_group_ids(report, 6, seed="published-seed")
    assert selected == random_accepted_group_ids(report, 6, seed="published-seed")
    assert "group-07" not in selected
    assert selected != random_accepted_group_ids(report, 6, seed="different-seed")


def test_reliability_audit_records_precommitted_statistical_target():
    document = translated_document()
    report = {"protocol_version": 3, "checks": [{
        "semantic_group_id": "group-1", "passed": True,
        "selected_translation": "Was there such a place in Seoul?",
        "proposed_translation": "Was there such a place in Seoul?",
        "error": None, "reason": "verified",
    }]}
    audit = build_reliability_audit(
        document, report, sample_id="sample", source_media="sample-data/source.mp4",
        media_sha256="e" * 64, adjudication_model="fixture", sample_size=1,
        selection_seed="published-seed",
    )
    assert audit["artifact_type"] == "accepted_subtitle_reliability_audit"
    assert audit["selection"]["method"] == "seeded_random"
    assert audit["selection"]["seed"] == "published-seed"
    assert audit["statistical_target"]["required_total_sample_size"] == 59


def test_pipeline_dispatches_reliability_audit_help(monkeypatch):
    monkeypatch.setattr("sys.argv", ["videotranslator", "prepare-reliability-audit", "--help"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 0
