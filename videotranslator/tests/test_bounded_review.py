"""Tests for durable, bounded human subtitle review artifacts."""

from videotranslator.commands.bounded_review import attach_audio_clips, build_bounded_review
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
