import json
from pathlib import Path

from auto_prepare_script import split_words
from create_approval_script import create_draft
from pipeline import RUNNABLE_STAGES, load_config, paths
from qa_transcript import analyze


def test_split_words_uses_pause_and_duration_boundaries():
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


def test_approval_draft_carries_qa_notes():
    transcript = {"segments": [{"start": 1.0, "end": 20.0, "text": "Review me"}]}
    qa_report = {
        "issues": [{"type": "long_duration", "segment": 0, "duration": 19.0}]
    }

    draft = create_draft("example", transcript, qa_report)

    assert draft["approval"]["status"] == "draft"
    assert draft["segments"][0]["id"] == "seg-0001"
    assert "long duration" in draft["segments"][0]["notes"]


def test_pipeline_config_paths_are_relative_to_config(tmp_path: Path):
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
