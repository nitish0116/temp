"""Tests for durable, bounded human subtitle review artifacts."""

import pytest

from videotranslator.commands.bounded_review import (
    apply_review_decisions, attach_audio_clips, build_accepted_audit, build_bounded_review,
    stratified_accepted_group_ids,
)
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


def test_matching_human_decision_updates_text_and_provenance():
    document, review = unresolved_review()
    item = review["items"][0]
    decisions = {"sample_id": "sample", "decisions": [{
        "semantic_group_id": "group-1", "approval_key": item["approval_key"],
        "status": "human_verified", "translation": "Was there such a place in Seoul?",
        "reviewer": "reviewer@example", "reviewed_at": "2026-08-16T02:00:00+05:30",
    }]}
    output, report = apply_review_decisions(document, review, decisions)
    assert report["passed"] is True and report["human_verified_count"] == 1
    assert output["segments"][0]["translated_text"].endswith("Seoul?")
    assert output["segments"][0]["provenance"][-1]["stage"] == "bounded-human-review"


def test_review_decision_rejects_wrong_key_or_stale_evidence():
    document, review = unresolved_review()
    decisions = {"sample_id": "sample", "decisions": [{
        "semantic_group_id": "group-1", "approval_key": "wrong",
        "status": "unresolved", "reviewer": "reviewer@example",
        "reviewed_at": "2026-08-16T02:00:00Z",
    }]}
    with pytest.raises(ValueError, match="approval key"):
        apply_review_decisions(document, review, decisions)
    document["segments"][0]["source_text"] = "changed source"
    with pytest.raises(ValueError, match="stale"):
        apply_review_decisions(document, review, {"sample_id": "sample", "decisions": []})


def test_review_decision_rejects_invalid_human_translation():
    document, review = unresolved_review()
    item = review["items"][0]
    decisions = {"sample_id": "sample", "decisions": [{
        "semantic_group_id": "group-1", "approval_key": item["approval_key"],
        "status": "human_verified", "translation": "x", "reviewer": "reviewer@example",
        "reviewed_at": "2026-08-16T02:00:00Z",
    }]}
    with pytest.raises(ValueError, match="integrity checks"):
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
