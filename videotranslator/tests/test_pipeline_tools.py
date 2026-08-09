"""Focused unit tests for deterministic video-translator pipeline helpers."""

import json
from pathlib import Path

from auto_prepare_script import make_approval, passes_gate, quality_metrics, split_words
from pipeline import RUNNABLE_STAGES, load_config, paths
from qa_transcript import analyze


def test_split_words_uses_pause_and_duration_boundaries():
    """A one-second speech pause starts a new subtitle cue."""
    words = [
        {"start": 0.0, "end": 0.5, "word": "Hello"},
        {"start": 0.6, "end": 1.0, "word": " world"},
        {"start": 2.2, "end": 2.6, "word": "Again"},
    ]

    chunks = split_words(words, maximum_duration=8.0, maximum_chars=84)

    assert [[word["word"] for word in chunk] for chunk in chunks] == [
        ["Hello", " world"],
        ["Again"],
    ]


def test_qa_reports_invalid_long_and_overlapping_segments():
    """QA reports every independent timing fault in a transcript."""
    transcript = {
        "segments": [
            {"start": 0.0, "end": 3.0, "text": "One"},
            {"start": 2.0, "end": 20.0, "text": "Two"},
            {"start": 21.0, "end": 21.0, "text": "Three"},
        ]
    }

    report = analyze(transcript, maximum_duration=8.0)

    assert report["passed"] is False
    assert report["issue_counts"] == {
        "overlap": 1,
        "long_duration": 1,
        "invalid_duration": 1,
    }


def test_pipeline_config_paths_are_relative_to_config(tmp_path: Path):
    """Relative configuration paths resolve from the configuration directory."""
    config_path = tmp_path / "pipeline.json"
    config_path.write_text(
        json.dumps(
            {
                "project_id": "example",
                "input_video": "input.mp4",
                "output_root": "outputs/example",
                "translation": {"target_language": "en", "model": "small"},
            }
        ),
        encoding="utf-8",
    )

    artifact_paths = paths(load_config(config_path))

    assert artifact_paths["video"] == tmp_path / "input.mp4"
    assert artifact_paths["root"] == tmp_path / "outputs" / "example"
    assert artifact_paths["transcript_json"].name == "input.auto.en.json"
    assert "translate" in RUNNABLE_STAGES


def test_automatic_gate_approves_metrics_within_thresholds():
    """Automatic approval accepts a nonempty, valid, sufficiently confident pass."""
    metrics = {
        "accepted_segments": 10,
        "invalid_timing_segments": 0,
        "low_confidence_ratio": 0.1,
        "rejection_ratio": 0.0,
    }

    assert passes_gate(metrics, 0.2, 0.05)


def test_automatic_approval_records_the_deciding_model():
    """Approved scripts identify the deterministic gate and selected model."""
    approval = make_approval(
        "example",
        {"segments": [{"start": 0.0, "end": 1.0, "text": "Hello"}]},
        [""],
        "medium",
    )

    assert approval["approval"]["status"] == "approved"
    assert approval["approval"]["approved_by"] == "automatic-quality-gate"
    assert "medium" in approval["approval"]["notes"]


def test_quality_metrics_are_json_serializable():
    """Model-derived numeric values are normalized before writing decisions JSON."""
    transcript = {"segments": [{"start": 0.0, "end": 1.0, "text": "Hello"}]}
    decisions = {"rejected_segments": []}

    metrics = quality_metrics(transcript, decisions, [""])

    assert json.loads(json.dumps(metrics))["score"] == 0
