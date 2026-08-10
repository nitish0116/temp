"""Focused unit tests for deterministic video-translator pipeline helpers."""

import json
from pathlib import Path

import pytest
import qa_transcript

from auto_prepare_script import clean_translation_repetition, make_approval, nllb_code, passes_gate, passes_translation_gate, quality_metrics, split_words, translation_coverage
from generate_dub import generate_dub, rate_to_length_scale
from diarize_speakers import assign_voices, voice_style
from assemble_dub import build_alignment_graph, tempo_filters
from qa_final import stem_leakage
from force_align import (
    align_transcript,
    build_reconciled_transcript,
    interval_overlap,
    normalize_language,
    reconciliation_candidates,
    select_alignment_route,
    whisper_timestamp_alignment,
)
from diarize_pyannote import assign_turns
from match_speaker_voices import match_profiles
from prepare_speaker_references import source_to_persistent_speakers
from synthesize_xtts import select_pilot
from translate_constrained import available_windows, character_budget, deduplicate_adjacent_cues, estimated_duration
from synthesize_constrained import active_sample_bounds, next_length_scale, permitted_duration, stable_segment_id
from align_active_speaker import bounded_onset_offset, dominant_track, intersection_over_union, timeline_safe_offset
from qa_dubbing_pipeline import dialogue_overlaps, evidence_coverage, maximum_native_tempo, speaker_reassignments
from recover_missing_speech import merge_intervals, merge_recovered, recover_uncovered_words, recovery_regions, subtract_intervals
from pipeline import RUNNABLE_STAGES, load_config, paths, stage_command
from qa_transcript import analyze, malformed_text_reasons, required_line_count, source_speech_coverage
from segment_utterances import join_words, merge_fragments, segment_words


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


def test_utterance_segmentation_uses_punctuation_pause_and_speaker_changes():
    """Natural sentence and speaker boundaries split otherwise short dialogue."""
    words = [
        {"start": 0.0, "end": 0.3, "word": "Are", "speaker": "one"},
        {"start": 0.3, "end": 0.6, "word": "you?", "speaker": "one"},
        {"start": 0.8, "end": 1.0, "word": "Yes.", "speaker": "one"},
        {"start": 1.01, "end": 1.3, "word": "Really?", "speaker": "two"},
    ]

    chunks = segment_words(words)

    assert [join_words(chunk) for chunk in chunks] == ["Are you?", "Yes.", "Really?"]
    assert [word["word"] for chunk in chunks for word in chunk] == [word["word"] for word in words]


def test_utterance_text_does_not_add_spaces_to_cjk_tokens():
    """Chinese, Japanese, and Korean token sequences retain native spacing."""
    words = [
        {"start": 0.0, "end": 0.2, "word": "你"},
        {"start": 0.2, "end": 0.4, "word": "好"},
        {"start": 0.4, "end": 0.6, "word": "。"},
    ]
    assert join_words(words) == "你好。"


def test_incomplete_fragment_merges_with_same_speaker_neighbor():
    """A dangling phrase is repaired without dropping its words or timing."""
    groups = [
        [{"start": 1.0, "end": 1.3, "word": "But,", "speaker": "one"}],
        [{"start": 1.7, "end": 2.4, "word": "wait.", "speaker": "one"}],
    ]
    merged = merge_fragments(groups)
    assert len(merged) == 1
    assert join_words(merged[0]) == "But, wait."
    assert (merged[0][0]["start"], merged[0][-1]["end"]) == (1.0, 2.4)


def test_fragment_merge_preserves_short_sentences_and_speaker_boundaries():
    """Intentional replies and different speakers are never merged away."""
    short_reply = [
        [{"start": 0.0, "end": 0.25, "word": "No!", "speaker": "one"}],
        [{"start": 0.3, "end": 1.0, "word": "Leave.", "speaker": "one"}],
    ]
    different_speakers = [
        [{"start": 2.0, "end": 2.2, "word": "I", "speaker": "one"}],
        [{"start": 2.21, "end": 2.8, "word": "know.", "speaker": "two"}],
    ]
    assert len(merge_fragments(short_reply)) == 2
    assert len(merge_fragments(different_speakers)) == 2


def test_fragment_merge_obeys_hard_duration_and_character_limits():
    """Semantic cleanup cannot recreate oversized subtitle cues."""
    groups = [
        [{"start": 0.0, "end": 0.2, "word": "And,", "speaker": "one"}],
        [{"start": 0.3, "end": 9.0, "word": "later.", "speaker": "one"}],
    ]
    assert len(merge_fragments(groups, maximum_duration=8.0)) == 2


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


def test_subtitle_qa_rejects_fast_short_long_and_malformed_cues():
    """Readability and malformed-text failures are blocking QA findings."""
    transcript = {"segments": [
        {"start": 0.0, "end": 0.3, "text": "This is much too fast,"},
        {"start": 1.0, "end": 3.0, "text": "one two three four five six seven eight nine"},
    ]}
    report = analyze(
        transcript,
        8.0,
        maximum_characters=30,
        maximum_line_characters=12,
        maximum_lines=2,
        maximum_characters_per_second=20,
    )
    assert report["passed"] is False
    assert report["issue_counts"]["short_duration"] == 1
    assert report["issue_counts"]["fast_reading_speed"] == 1
    assert report["issue_counts"]["malformed_text"] == 1
    assert report["issue_counts"]["long_text"] == 1
    assert report["issue_counts"]["excessive_lines"] >= 1


def test_subtitle_line_calculation_handles_words_and_cjk():
    """Line QA wraps at words for Latin text and characters for CJK text."""
    assert required_line_count("one two three four", 10) == 2
    assert required_line_count("你好世界你好世界", 4) == 2
    assert required_line_count("unbreakable", 5) > 2


def test_subtitle_qa_rejects_missing_source_speech_coverage():
    """Target cues must cover independent source dialogue events and time."""
    target = {"segments": [{"start": 0.0, "end": 1.0, "text": "First."}]}
    source = {"segments": [
        {"start": 0.0, "end": 1.0, "text": "one"},
        {"start": 2.0, "end": 3.0, "text": "two"},
    ]}
    event_coverage, time_coverage = source_speech_coverage(target, source)
    report = analyze(target, 8.0, source_transcript=source)
    assert (event_coverage, time_coverage) == (0.5, 0.5)
    assert report["issue_counts"] == {"missing_source_events": 1, "missing_source_time": 1}
    assert malformed_text_reasons("unfinished,") == ["incomplete_ending"]


def test_subtitle_qa_cli_writes_report_and_exits_nonzero_on_failure(tmp_path, monkeypatch):
    """A failed gate leaves diagnostics but prevents downstream promotion."""
    transcript = tmp_path / "bad.json"
    report = tmp_path / "qa.json"
    transcript.write_text(
        json.dumps({"segments": [{"start": 0.0, "end": 0.1, "text": "unfinished,"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["qa_transcript.py", str(transcript), "-o", str(report)])
    with pytest.raises(SystemExit) as error:
        qa_transcript.main()
    assert error.value.code == 1
    assert json.loads(report.read_text(encoding="utf-8"))["passed"] is False


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


def test_translation_gate_requires_every_source_cue_and_timestamp():
    """Target language does not affect preservation of canonical source timing."""
    source = {"segments": [{"start": 1.0, "end": 2.0, "text": "源"}, {"start": 3.0, "end": 4.0, "text": "語"}]}
    complete = {"segments": [{"start": 1.0, "end": 2.0, "text": "one"}, {"start": 3.0, "end": 4.0, "text": "two"}]}
    incomplete = {"segments": complete["segments"][:1]}

    assert passes_translation_gate(translation_coverage(source, complete))
    assert not passes_translation_gate(translation_coverage(source, incomplete))


def test_runaway_translation_repetition_is_collapsed_language_independently():
    """Repeated decoder clauses are retained twice at most."""
    assert clean_translation_repetition("Yes, Yes, Yes, Yes, continue.") == "Yes, Yes, continue."


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
        {"start": 2.0, "end": 6.0, "generated_duration": 1.0},
    ]

    graph = build_alignment_graph(clips, 10.0)

    assert "atempo=2,atempo=1.500000" in graph
    assert "[0:a]" in graph
    assert "[1:a]" in graph
    assert "[2:a]" not in graph
    assert "adelay=1000:all=1" in graph
    assert "adelay=2000:all=1" in graph
    assert tempo_filters(1.0) == []


def test_native_tempo_assembly_never_adds_atempo_filters():
    """Duration-constrained clips retain their generated delivery during assembly."""
    clips = [{"start": 1.0, "end": 1.2, "generated_duration": 1.0}]
    graph = build_alignment_graph(clips, 10.0, preserve_native_tempo=True)
    assert "atempo=" not in graph
    assert "atrim=duration=1.000000" in graph


def test_final_qa_stage_is_runnable(tmp_path: Path):
    """The orchestrator exposes automatic QA after final assembly."""
    config = {"project_id": "x", "input_video": str(tmp_path / "x.mp4"), "output_root": str(tmp_path), "translation": {}}
    command, outputs = stage_command("final_qa", config, paths(config))
    assert command[1].endswith("qa_final.py")
    assert outputs == [paths(config)["final_qa"]]


def test_reconciliation_preserves_reference_speech_missing_from_alignment():
    """Reference cues with little aligned-word coverage remain reconciliation candidates."""
    aligned = [{"words": [{"start": 1.0, "end": 2.0, "word": "one"}]}]
    reference = [
        {"start": 1.0, "end": 2.0, "text": "covered"},
        {"start": 4.0, "end": 5.0, "text": "retain"},
    ]

    missing = reconciliation_candidates(reference, aligned)

    assert [segment["text"] for segment in missing] == ["retain"]
    assert interval_overlap(0.0, 3.0, [(1.0, 2.0)]) == 1.0


def test_reconciled_transcript_records_alignment_and_reference_provenance():
    """Canonical reconciliation inserts uncovered reference cues audibly and visibly."""
    aligned = [{"words": [{"start": 1.0, "end": 1.5, "word": "aligned", "score": 0.9}]}]
    retained = [{"start": 3.0, "end": 3.5, "text": "retained"}]

    result = build_reconciled_transcript({"language": "xx"}, aligned, retained)

    assert [segment["provenance"] for segment in result["segments"]] == [
        "large-v3-forced-alignment",
        "retained-reference-no-word-overlap",
    ]


def test_multilingual_alignment_routes_names_locales_and_supported_languages():
    """Detected language variants select their language-specific CTC model."""
    assert normalize_language("Hindi") == "hi"
    assert normalize_language("zh-CN") == "zh"
    for language in ("en", "fr", "de", "es", "hi", "ja", "zh", "ar", "ko"):
        route = select_alignment_route({"language": language, "language_probability": 0.99})
        assert route["mode"] == "ctc"
        assert route["model"]


def test_alignment_uses_whisper_words_for_unknown_or_uncertain_language(tmp_path):
    """Unsupported and low-confidence languages retain timings without loading CTC."""
    segment = {
        "start": 1.0, "end": 2.0, "text": "saluton",
        "words": [{"start": 1.1, "end": 1.8, "word": "saluton", "probability": 0.91}],
    }
    transcript = {"language": "eo", "language_probability": 0.99, "segments": [segment]}
    aligned, reconciled, report = align_transcript(transcript, transcript, tmp_path / "absent.wav", None, 0.75)

    assert aligned["segments"][0]["alignment_status"] == "whisper-timestamps"
    assert report["alignment_route_reason"] == "unsupported-language"
    assert report["whisper_fallback_segments"] == 1
    assert reconciled["segments"][0]["start"] == 1.1
    assert select_alignment_route({"language": "fr", "language_probability": 0.2})["mode"] == "whisper"


def test_whisper_fallback_rejects_segments_without_word_timestamps():
    """A rough cue boundary is never mislabeled as word-level alignment evidence."""
    result = whisper_timestamp_alignment({"start": 0, "end": 1, "text": "missing"}, "fallback")
    assert result["alignment_status"] == "failed"


def test_dedicated_diarization_assigns_maximum_overlap_and_records_fallback():
    """Exclusive turns map deterministically without pitch-based speaker splitting."""
    segments = [{"start": 1.0, "end": 2.0}, {"start": 5.0, "end": 6.0}]
    turns = [
        {"start": 0.5, "end": 2.5, "speaker": "SPEAKER_01"},
        {"start": 7.0, "end": 8.0, "speaker": "SPEAKER_02"},
    ]

    assigned, report = assign_turns(segments, turns)

    assert assigned[0]["speaker"] == "speaker-01"
    assert assigned[0]["speaker_assignment"]["method"] == "maximum-overlap"
    assert assigned[1]["speaker_assignment"]["method"] == "nearest-turn-fallback"
    assert report["fallback_assignment_count"] == 1


def test_diarization_splits_one_cue_at_a_speaker_boundary():
    """Words spoken by different characters never remain in one subtitle cue."""
    segments = [{
        "start": 1.0, "end": 3.0, "text": "Hello. Hi.",
        "words": [
            {"start": 1.0, "end": 1.6, "word": "Hello."},
            {"start": 2.0, "end": 2.5, "word": "Hi."},
        ],
    }]
    turns = [
        {"start": 0.8, "end": 1.8, "speaker": "A"},
        {"start": 1.9, "end": 2.8, "speaker": "B"},
    ]

    assigned, report = assign_turns(segments, turns)

    assert [item["text"] for item in assigned] == ["Hello.", "Hi."]
    assert [item["speaker"] for item in assigned] == ["speaker-01", "speaker-02"]
    assert report["input_segment_count"] == 1
    assert report["utterance_segment_count"] == 2


def test_multi_feature_voice_matching_is_unique_and_not_name_based():
    """Global acoustic matching assigns the nearest distinct profiles."""
    low = {"log_pitch": 1.0, "pitch_range": 1.0, "log_centroid": 1.0, "log_bandwidth": 1.0, "energy_range": 1.0}
    high = {"log_pitch": 3.0, "pitch_range": 3.0, "log_centroid": 3.0, "log_bandwidth": 3.0, "energy_range": 3.0}
    assignments, distances = match_profiles(
        {"speaker-01": low, "speaker-02": high},
        {"voice-arbitrary-a": high, "voice-arbitrary-b": low},
    )
    assert assignments == {"speaker-01": "voice-arbitrary-b", "speaker-02": "voice-arbitrary-a"}
    assert set(distances) == {"speaker-01", "speaker-02"}


def test_duration_budget_borrows_only_bounded_trailing_silence():
    """Translation capacity cannot consume an arbitrarily long scene pause."""
    segments = [
        {"start": 1.0, "end": 2.0},
        {"start": 10.0, "end": 11.0},
    ]
    windows = available_windows(segments, maximum_extension=0.75)
    assert windows == [1.75, 1.0]
    assert character_budget(2.0, 10.0, 1.15) == 23
    assert estimated_duration("1234567890", 10.0) == 1.0


def test_adjacent_same_speaker_text_fragments_merge_before_translation():
    """Alignment fragments merge by Unicode text containment without language rules."""
    segments = [
        {"start": 1.0, "end": 1.1, "text": "달", "speaker": "speaker-01"},
        {"start": 1.11, "end": 1.8, "text": "달쌌니?", "speaker": "speaker-01"},
        {"start": 1.9, "end": 2.1, "text": "other", "speaker": "speaker-02"},
    ]
    merged = deduplicate_adjacent_cues(segments)
    assert len(merged) == 2
    assert merged[0]["text"] == "달쌌니?"
    assert (merged[0]["start"], merged[0]["end"]) == (1.0, 1.8)


def test_ultrashort_same_speaker_fragment_merges_into_following_utterance():
    """Tiny CTC fragments do not become separate robotic TTS clips."""
    segments = [
        {"start": 1.0, "end": 1.02, "text": "hey", "speaker": "one"},
        {"start": 1.02, "end": 1.8, "text": "you", "speaker": "one"},
    ]
    merged = deduplicate_adjacent_cues(segments)
    assert len(merged) == 1
    assert merged[0]["text"] == "hey you"


def test_constrained_synthesis_uses_recorded_window_and_stable_fallback_id():
    """TTS consumes step-5 timing metadata without requiring legacy approval IDs."""
    segment = {
        "start": 1.0,
        "end": 2.0,
        "duration_constraint": {"available_seconds": 1.6},
    }
    assert permitted_duration(segment) == 1.6
    assert stable_segment_id(segment, 4) == "seg-0005"


def test_native_tts_retry_scale_is_bounded_and_targets_the_window():
    """A long clip is regenerated with bounded Piper prosody, not audio tempo."""
    assert next_length_scale(1.0, 2.0, 1.0, 0.85) == 0.85
    assert next_length_scale(1.0, 1.1, 1.0, 0.85) == 0.8818


def test_silence_trim_preserves_padding_and_internal_pauses():
    """Only waveform edges are removed; quiet samples between speech remain."""
    samples = [0, 0, 500, 0, 0, 600, 0, 0]
    assert active_sample_bounds(samples, threshold=100, padding_samples=1) == (1, 7)


def test_active_speaker_selection_requires_dominant_motion():
    """Close multi-face motion scores remain ambiguous instead of being guessed."""
    assert dominant_track({0: 0.04, 1: 0.02}, 1.35)[0] == 0
    assert dominant_track({0: 0.04, 1: 0.035}, 1.35)[0] is None


def test_visual_onset_correction_is_bounded():
    """Lip-motion timing cannot move dialogue by more than the configured cap."""
    assert bounded_onset_offset(2.0, 2.1, 0.25) == 0.1
    assert bounded_onset_offset(2.0, 2.8, 0.25) == 0.25
    assert intersection_over_union((0, 0, 10, 10), (5, 0, 10, 10)) == 1 / 3


def test_visual_offset_cannot_overlap_neighboring_audio():
    """Visual alignment yields to the synthesized dialogue timeline."""
    assert timeline_safe_offset(0.2, 2.0, 1.0, None, 3.1) == (0.1, True)
    assert timeline_safe_offset(-0.2, 2.0, 1.0, 1.9, None) == (-0.1, True)


def test_pipeline_qa_detects_speaker_reassignment_and_audio_overlap(tmp_path: Path):
    """Persistent voices and non-overlapping generated speech are blocking checks."""
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"audio")
    script = {"segments": [{"speaker": "one", "voice": "voice-a"}]}
    manifest = {
        "clips": [{
            "segment_id": "seg-0001", "voice": "voice-b", "audio_path": str(audio),
            "status": "generated", "generated_duration": 1.2, "start": 0.0,
        }, {"segment_id": "seg-0002", "start": 1.0, "generated_duration": 0.5}],
    }
    assert speaker_reassignments(script, manifest)[1] == ["one"]
    assert dialogue_overlaps(manifest) == ["seg-0001"]


def test_pipeline_qa_measures_native_tts_tempo():
    """The QA rate uses Piper's synthesis scale rather than a later audio filter."""
    report = {"segments": [{"segment_id": "one", "status": "fits", "attempts": [{"length_scale": 0.85}]}]}
    tempo, segments = maximum_native_tempo(report)
    assert round(tempo, 4) == 1.1765
    assert segments == ["one"]


def test_missing_speech_regions_subtract_canonical_cues():
    """Independent diarization exposes speech outside the accepted timeline."""
    turns = [{"start": 1.0, "end": 4.0}]
    cues = [{"start": 1.5, "end": 2.5}]
    assert subtract_intervals([(1.0, 4.0)], [(1.5, 2.5)]) == [(1.0, 1.5), (2.5, 4.0)]
    assert recovery_regions(turns, cues, minimum_duration=0.25, merge_gap=0.1) == [(1.0, 1.5), (2.5, 4.0)]
    assert merge_intervals([(1.0, 1.2), (1.3, 2.0)], maximum_gap=0.1) == [(1.0, 2.0)]


def test_recovered_dialogue_is_promoted_without_nearby_duplicates():
    """Targeted recovery adds new speech but suppresses repeated canonical text."""
    canonical = {"segments": [{"start": 1.0, "end": 2.0, "text": "hello"}]}
    candidates = [
        {"start": 1.2, "end": 1.8, "text": "hello"},
        {"start": 3.0, "end": 4.0, "text": "new line"},
    ]
    merged, promoted = merge_recovered(canonical, candidates)
    assert [segment["text"] for segment in promoted] == ["new line"]
    assert len(merged["segments"]) == 2


def test_strong_asr_words_missing_from_timeline_become_fallback_cues():
    """A CTC timing failure cannot silently discard a recognized spoken word."""
    strong = {"segments": [{"words": [
        {"start": 1.0, "end": 1.3, "word": "kept"},
        {"start": 3.0, "end": 3.3, "word": "missing"},
    ]}]}
    canonical = {"segments": [{"start": 0.9, "end": 1.4, "text": "kept"}]}
    recovered = recover_uncovered_words(strong, canonical)
    assert len(recovered) == 1
    assert recovered[0]["text"] == "missing"
    assert recovered[0]["provenance"] == "retained-large-v3-word-coverage"


def test_source_evidence_coverage_is_independent_of_generated_clips():
    """QA detects source speech that never entered the canonical script."""
    script = {"segments": [{"start": 1.0, "end": 2.0}]}
    strong = {"segments": [{"words": [
        {"start": 1.2, "end": 1.4}, {"start": 3.0, "end": 3.2},
    ]}]}
    diarization = {"turns": [{"start": 1.0, "end": 2.0}, {"start": 3.0, "end": 4.0}]}
    word_coverage, turn_coverage = evidence_coverage(script, strong, diarization)
    assert word_coverage == 0.5
    assert turn_coverage == 0.5


def test_reference_mapping_uses_diarization_provenance():
    """Reference extraction follows stable speakers rather than pitch labels."""
    script = {"segments": [{"speaker": "speaker-02", "speaker_assignment": {"source_label": "SPEAKER_A"}}]}
    assert source_to_persistent_speakers(script) == {"SPEAKER_A": "speaker-02"}


def test_xtts_pilot_contains_distinct_speakers():
    """A pilot samples characters before adding extra lines from one speaker."""
    segments = [
        {"start": 0, "end": 5, "speaker": "one"},
        {"start": 5, "end": 7, "speaker": "one"},
        {"start": 7, "end": 10, "speaker": "two"},
    ]
    assert {item[1]["speaker"] for item in select_pilot(segments, 2)} == {"one", "two"}
