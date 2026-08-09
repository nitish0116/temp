"""Focused unit tests for deterministic video-translator pipeline helpers."""

import json
from pathlib import Path

import pytest

from auto_prepare_script import make_approval, nllb_code, passes_gate, quality_metrics, split_words
from generate_dub import generate_dub, rate_to_length_scale
from diarize_speakers import assign_voices, voice_style
from assemble_dub import build_alignment_graph, tempo_filters
from qa_final import stem_leakage
from pipeline import RUNNABLE_STAGES, load_config, paths, stage_command
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
    assert "separate" in RUNNABLE_STAGES
    assert "tts" in RUNNABLE_STAGES


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


def test_pipeline_defaults_to_detected_source_and_english_target(tmp_path: Path):
    """Absent source-language input is omitted while English remains the target."""
    config = {
        "project_id": "example",
        "input_video": str(tmp_path / "episode.mp4"),
        "output_root": str(tmp_path / "outputs"),
        "translation": {"target_language": "en", "model": "small"},
    }
    artifact_paths = paths(config)

    command, _ = stage_command("translate", config, artifact_paths)

    assert "--language" not in command
    assert command[command.index("--target-language") + 1] == "en"
    assert artifact_paths["transcript_json"].name.endswith(".auto.en.json")


def test_final_assembly_uses_separated_accompaniment(tmp_path: Path):
    """Pipeline assembly replaces source vocals when an accompaniment exists."""
    config = {
        "project_id": "example",
        "input_video": str(tmp_path / "episode.mp4"),
        "output_root": str(tmp_path / "outputs"),
        "translation": {"target_language": "en", "model": "small"},
        "dubbing": {},
    }
    artifact_paths = paths(config)

    command, outputs = stage_command("assemble", config, artifact_paths)

    assert command[command.index("--background") + 1] == str(artifact_paths["accompaniment"])
    assert artifact_paths["dubbed"] in outputs


def test_tts_rejects_scripts_without_automatic_approval(tmp_path: Path):
    """Voice generation cannot bypass the automatic transcript quality gate."""
    unapproved = {
        "project_id": "example",
        "approval": {"status": "draft"},
        "segments": [],
    }

    with pytest.raises(ValueError, match="automatically approved"):
        generate_dub(unapproved, tmp_path, "en", None, "+0%", 1)


def test_language_and_speech_rate_defaults_are_deterministic():
    """Common target languages and percentage rates map to local model settings."""
    assert nllb_code("fr-FR", None) == "fra_Latn"
    assert rate_to_length_scale("+0%") == 1.0
    assert rate_to_length_scale("+25%") == 0.8


def test_distinct_speakers_receive_distinct_style_matched_voices():
    """Voice assignment stays unique and prefers pitch-compatible name markers."""
    voices = ["en_US-amy-medium", "en_GB-alan-medium", "en_US-ryan-medium"]

    assigned = assign_voices(["high", "low", "low"], voices)

    assert len(set(assigned)) == 3
    assert voice_style(assigned[0]) == "high"
    assert voice_style(assigned[1]) == "low"


def test_alignment_speeds_only_clips_that_overrun_their_window():
    """Long speech is fitted into its cue while short speech keeps natural pace."""
    clips = [
        {"start": 1.0, "end": 2.0, "generated_duration": 3.0},
        {"start": 4.0, "end": 6.0, "generated_duration": 1.0},
    ]

    graph = build_alignment_graph(clips, 10.0)

    assert "atempo=2,atempo=1.500000" in graph
    assert "[0:a]" in graph
    assert "[1:a]" in graph
    assert "[2:a]" not in graph
    assert "adelay=1000:all=1" in graph
    assert "adelay=4000:all=1" in graph
    assert tempo_filters(1.0) == []


def test_final_qa_stage_is_runnable(tmp_path: Path):
    """The orchestrator exposes automatic QA after final assembly."""
    config = {"project_id": "x", "input_video": str(tmp_path / "x.mp4"), "output_root": str(tmp_path), "translation": {}}
    command, outputs = stage_command("final_qa", config, paths(config))
    assert command[1].endswith("qa_final.py")
    assert outputs == [paths(config)["final_qa"]]
