"""Focused unit tests for deterministic video-translator pipeline helpers."""

import json
import subprocess
from pathlib import Path

import pytest
from videotranslator.commands import qa_transcript

from videotranslator.commands.auto_prepare_script import clean_translation_repetition, make_approval, nllb_code, passes_gate, passes_translation_gate, quality_metrics, split_words, translated_document, translation_coverage
from videotranslator.commands.generate_dub import generate_dub, piper_models_dir, rate_to_length_scale
from videotranslator.commands.diarize_speakers import assign_voices, voice_style
from videotranslator.commands.assemble_dub import build_alignment_graph, tempo_filters
from videotranslator.commands.qa_final import stem_leakage
from videotranslator.commands.force_align import (
    align_transcript,
    build_reconciled_transcript,
    interval_overlap,
    normalize_language,
    reconciliation_candidates,
    select_alignment_route,
    whisper_timestamp_alignment,
)
from videotranslator.commands.diarize_pyannote import assign_turns, reconcile_unmatched_turns
from videotranslator.commands.match_speaker_voices import match_profiles
from videotranslator.commands.prepare_speaker_references import source_to_persistent_speakers
from videotranslator.commands.synthesize_xtts import select_pilot
from videotranslator.commands.translate_constrained import available_windows, character_budget, deduplicate_adjacent_cues, estimated_duration
from videotranslator.commands.synthesize_constrained import active_sample_bounds, next_length_scale, permitted_duration, stable_segment_id
from videotranslator.commands.synthesize_constrained import synthesis_text
from videotranslator.commands.prepare_canonical_tts import canonical_is_approved, prepare_canonical_tts
from videotranslator.commands.align_active_speaker import bounded_onset_offset, dominant_track, intersection_over_union, timeline_safe_offset
from videotranslator.commands.qa_dubbing_pipeline import dialogue_overlaps, evidence_coverage, maximum_native_tempo, speaker_reassignments
from videotranslator.commands.recover_missing_speech import merge_intervals, merge_recovered, preserve_speech_envelopes, recover_uncovered_words, recovery_regions, subtract_intervals
from videotranslator.pipeline import RUNNABLE_STAGES, load_config, paths, stage_command
from videotranslator.commands.qa_transcript import analyze, diarized_speech_coverage, malformed_text_reasons, required_line_count, source_speech_coverage
from videotranslator.commands.segment_utterances import join_words, merge_fragments, segment_words
from videotranslator.commands import runtime_device
from videotranslator.commands.canonical_timed_text import (
    adapt_legacy_transcript,
    to_legacy_transcript,
    validate_canonical_timed_text,
)
from videotranslator.commands.build_clean_transcript import build_clean_transcript
from videotranslator.install_dependencies import parse_compute_capability, select_profile
from videotranslator.commands.create_subtitles import (
    artifact_paths as subtitle_artifact_paths,
    hugging_face_token_available,
    prepare_runtime_environment,
    quality_score,
    recovery_candidates,
    run_recovery_with_fallbacks,
    shared_ffmpeg_bin,
)
from videotranslator.commands.translate_subtitles import write_srt as write_translated_srt
from videotranslator.commands.translate_contextual import (
    LazyContextTranslator,
    TranslationRequest,
    cache_key,
    normalize_translation_response,
    translate_cached_request,
    translate_contextual,
    translation_prompt,
    translation_request,
)
from videotranslator.commands.qa_translation_integrity import (
    enforce_translation_integrity,
    integrity_issues,
    semantic_pieces,
)
from videotranslator.commands.project_history import render_history, update_handoff
from videotranslator.commands.map_translation_cues import (
    allocate_boundaries,
    map_translated_groups,
    pause_boundaries,
)


def test_display_mapping_keeps_short_multi_chunk_cues_valid():
    clean = build_clean_transcript({
        "language": "en", "task": "transcribe", "output_language": "en",
        "segments": [{"start": 0.0, "end": 0.184, "text": "short", "speaker": "one"}],
    })
    translated = translate_contextual(
        clean, "en", "model", lambda request: "A" * 64 + " " + "B" * 64
    )
    mapped = map_translated_groups(translated, maximum_characters=64)
    assert len(mapped["segments"]) == 2
    assert all(item["end"] > item["start"] for item in mapped["segments"])
from videotranslator.commands.finalize_subtitles import finalize
from videotranslator.commands.repair_subtitles import repair, split_cue, text_chunks


FIXTURES = Path(__file__).parent / "fixtures"


def test_project_history_combines_handoff_and_live_git_evidence(
    tmp_path: Path, monkeypatch,
):
    """A new assistant receives durable prose plus current repository evidence."""
    handoff = tmp_path / "handoff.md"
    handoff.write_text("# Durable handoff\n\nNext: qualify the model.\n", encoding="utf-8")
    responses = {
        ("status", "-sb"): "## main...origin/main [ahead 1]\n",
        ("log", "-3", "--date=short", "--format=%h %ad %s"): "abc 2026-08-14 update\n",
        ("log", "--oneline", "@{upstream}..HEAD"): "abc update\n",
    }

    def run(command, cwd, check, capture_output, text):
        key = tuple(command[1:])
        return subprocess.CompletedProcess(command, 0, stdout=responses[key])

    monkeypatch.setattr(subprocess, "run", run)
    rendered = render_history(tmp_path, handoff, commit_count=3)
    assert "Next: qualify the model." in rendered
    assert "main...origin/main [ahead 1]" in rendered
    assert "abc 2026-08-14 update" in rendered
    assert "Commits not in upstream" in rendered


def test_project_handoff_update_validates_and_replaces_atomically(tmp_path: Path):
    """Only a complete, correctly titled handoff can replace the durable file."""
    destination = tmp_path / "project-handoff.md"
    destination.write_text("old content\n", encoding="utf-8")
    source = tmp_path / "handoff-next.md"
    source.write_text(
        "# Video Translator project handoff\n\nNext action: qualify a model.\n",
        encoding="utf-8",
    )

    assert update_handoff(source, destination) == destination.resolve()
    assert destination.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert not (tmp_path / ".project-handoff.md.tmp").exists()


def test_project_handoff_update_rejects_invalid_document(tmp_path: Path):
    """Invalid input leaves the existing durable handoff untouched."""
    destination = tmp_path / "project-handoff.md"
    destination.write_text("existing handoff\n", encoding="utf-8")
    source = tmp_path / "invalid.md"
    source.write_text("# Unrelated notes\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must start with"):
        update_handoff(source, destination)
    assert destination.read_text(encoding="utf-8") == "existing handoff\n"


def test_lazy_translator_does_not_construct_backend_until_called():
    constructed = []
    lazy = LazyContextTranslator(
        lambda: constructed.append("loaded") or (lambda request: "translated")
    )
    assert constructed == []
    request = TranslationRequest("g1", "ko", "en", "source", (), ())
    assert lazy(request) == "translated"
    assert lazy(request) == "translated"
    assert constructed == ["loaded"]


def test_auxiliary_translation_uses_versioned_cache(tmp_path: Path):
    calls = []
    request = TranslationRequest(
        "compression-g1", "ko", "en", "source", (), (), maximum_characters=20,
    )

    def translate(_request):
        calls.append("called")
        return " concise output "

    assert translate_cached_request(request, translate, "model", tmp_path) == "concise output"
    assert translate_cached_request(request, translate, "model", tmp_path) == "concise output"
    assert calls == ["called"]


def test_diarization_coverage_filters_unsupported_music_labels_but_keeps_speech():
    subtitles = {"segments": [{"start": 0.0, "end": 1.0, "text": "speech"}]}
    diarization = {"turns": [
        {"start": 0.0, "end": 1.0, "speaker": "one"},
        {"start": 5.0, "end": 8.0, "speaker": "music-like-label"},
    ]}
    evidence = {"segments": [{"words": [{"start": 0.0, "end": 1.0, "word": "speech"}]}]}
    assert diarized_speech_coverage(subtitles, diarization, evidence) == (1.0, 1.0)


def test_subtitle_improvement_baseline_is_frozen_and_reproducible():
    """The synthetic fixture preserves the episode baseline and failure classes."""
    fixture = json.loads(
        (FIXTURES / "subtitle_quality_baseline.json").read_text(encoding="utf-8")
    )
    baseline = fixture["episode_baseline"]
    assert baseline == {
        "selected_attempt": 2,
        "profile": "balanced",
        "segment_count": 443,
        "source_event_coverage": 0.9971896955503513,
        "source_time_coverage": 0.9131696520063969,
        "diarized_turn_coverage": 0.8960784313725491,
        "diarized_time_coverage": 0.9129217575534889,
        "short_duration_count": 19,
        "fast_reading_speed_count": 25,
        "long_duration_count": 1,
        "longest_duration": 35.756,
        "maximum_characters_per_second": 73.17,
    }

    report = analyze(
        fixture["candidate"],
        maximum_duration=12.0,
        source_transcript=fixture["source_evidence"],
        diarization_report=fixture["diarization_evidence"],
    )
    observed = set(report["issue_counts"])
    assert set(fixture["expected_issue_types"]) <= observed
    assert report["passed"] is False


def test_legacy_transcript_round_trips_through_canonical_schema():
    """Compatibility conversion preserves current transcript data exactly."""
    legacy = {
        "language": "ja",
        "language_probability": 0.98,
        "task": "transcribe",
        "output_language": "ja",
        "alignment_model": "example/model",
        "segments": [{
            "id": "original-7",
            "semantic_group_id": "thought-3",
            "source_cue_ids": [7, 8],
            "start": 1.25,
            "end": 2.75,
            "text": "synthetic source text",
            "speaker": "speaker_1",
            "words": [{"start": 1.25, "end": 1.7, "word": "synthetic"}],
            "confidence": {"asr": 0.91},
            "notes": "fixture metadata",
        }],
    }

    canonical = adapt_legacy_transcript(legacy)

    validate_canonical_timed_text(canonical)
    assert canonical["schema_version"] == 1
    assert canonical["artifact_type"] == "canonical_timed_text"
    assert canonical["segments"][0]["speaker"] == "speaker_1"
    assert canonical["segments"][0]["source_cue_ids"] == [7, 8]
    assert canonical["segments"][0]["provenance"][-1]["method"] == "legacy-transcript-adapter-v1"
    assert to_legacy_transcript(canonical) == legacy


def test_translated_legacy_artifact_marks_unavailable_source_text():
    """Migration does not invent source dialogue absent from old translations."""
    legacy = {
        "language": "ja",
        "language_probability": 0.9,
        "task": "translate",
        "output_language": "en",
        "segments": [{"start": 1.0, "end": 2.0, "text": "Translated text."}],
    }

    canonical = adapt_legacy_transcript(legacy)

    assert canonical["stage"] == "translated"
    assert canonical["segments"][0]["source_text"] is None
    assert canonical["segments"][0]["translated_text"] == "Translated text."
    assert to_legacy_transcript(canonical) == legacy


def test_canonical_validation_rejects_duplicate_ids_and_invalid_timing():
    """Stable identity and positive timing are blocking schema invariants."""
    canonical = adapt_legacy_transcript({
        "language": "en",
        "task": "transcribe",
        "output_language": "en",
        "segments": [
            {"id": "one", "start": 0.0, "end": 1.0, "text": "One."},
            {"id": "two", "start": 1.0, "end": 2.0, "text": "Two."},
        ],
    })
    canonical["segments"][1]["id"] = "one"
    with pytest.raises(ValueError, match="duplicate segment id"):
        validate_canonical_timed_text(canonical)

    canonical["segments"][1]["id"] = "two"
    canonical["segments"][1]["end"] = canonical["segments"][1]["start"]
    with pytest.raises(ValueError, match="invalid timing"):
        validate_canonical_timed_text(canonical)


def test_canonical_json_schema_is_versioned_and_closed():
    """The portable JSON contract matches the code-level schema identity."""
    schema = json.loads(
        (Path(__file__).parents[1] / "schemas" / "canonical-timed-text.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert schema["properties"]["schema_version"] == {"const": 1}
    assert schema["properties"]["artifact_type"] == {"const": "canonical_timed_text"}
    assert schema["additionalProperties"] is False
    assert schema["properties"]["segments"]["items"]["additionalProperties"] is False


def test_translation_preserves_complete_canonical_lineage():
    """Translation enriches canonical cues instead of rebuilding lossy triples."""
    canonical = adapt_legacy_transcript({
        "language": "ja",
        "language_probability": 0.99,
        "task": "transcribe",
        "output_language": "ja",
        "segments": [{
            "id": "source-1",
            "semantic_group_id": "thought-1",
            "source_cue_ids": [1, 2],
            "start": 1.0,
            "end": 3.0,
            "text": "synthetic source",
            "speaker": "speaker-01",
            "words": [{"start": 1.0, "end": 1.5, "word": "synthetic"}],
            "confidence": {"asr": 0.95},
        }],
    })

    translated = translated_document(
        canonical, ["Synthetic target."], "en", "test-model", "jpn_Jpan", "eng_Latn"
    )

    validate_canonical_timed_text(translated)
    cue = translated["segments"][0]
    assert cue["id"] == "source-1"
    assert cue["semantic_group_id"] == "thought-1"
    assert cue["source_cue_ids"] == [1, 2]
    assert cue["source_text"] == "synthetic source"
    assert cue["translated_text"] == "Synthetic target."
    assert cue["speaker"] == "speaker-01"
    assert cue["words"][0]["word"] == "synthetic"
    assert cue["confidence"] == {"asr": 0.95}
    assert cue["provenance"][-1]["stage"] == "translation"


def test_repair_splits_receive_unique_ids_and_parent_lineage():
    """One-to-many display repair retains ancestry without duplicate cue IDs."""
    canonical = adapt_legacy_transcript({
        "language": "en",
        "language_probability": 1.0,
        "task": "transcribe",
        "output_language": "en",
        "segments": [{
            "id": "parent",
            "semantic_group_id": "thought",
            "source_cue_ids": [4],
            "start": 0.0,
            "end": 4.0,
            "text": "One synthetic sentence. Another synthetic sentence.",
            "speaker": "speaker-01",
        }],
    })

    repaired = repair(canonical, maximum_characters=28)

    validate_canonical_timed_text(repaired)
    assert len(repaired["segments"]) == 2
    assert [cue["id"] for cue in repaired["segments"]] == ["parent.part-01", "parent.part-02"]
    assert all(cue["metadata"]["parent_cue_id"] == "parent" for cue in repaired["segments"])
    assert all(cue["source_cue_ids"] == [4] for cue in repaired["segments"])
    assert all(cue["speaker"] == "speaker-01" for cue in repaired["segments"])
    assert all(any(event["method"] == "split-display-cue" for event in cue["provenance"]) for cue in repaired["segments"])


def test_diarization_preserves_canonical_identity_and_records_assignment():
    """Speaker assignment adds metadata and provenance without losing lineage."""
    canonical = adapt_legacy_transcript({
        "language": "en",
        "language_probability": 1.0,
        "task": "transcribe",
        "output_language": "en",
        "segments": [{
            "id": "cue-1",
            "semantic_group_id": "thought-1",
            "source_cue_ids": [1],
            "start": 0.0,
            "end": 1.0,
            "text": "Synthetic.",
        }],
    })

    assigned, _ = assign_turns(
        canonical["segments"], [{"start": 0.0, "end": 1.0, "speaker": "RAW_0"}]
    )
    canonical["segments"] = assigned

    validate_canonical_timed_text(canonical)
    cue = canonical["segments"][0]
    assert cue["id"] == "cue-1"
    assert cue["speaker"] == "speaker-01"
    assert cue["metadata"]["speaker_assignment"]["source_label"] == "RAW_0"
    assert cue["provenance"][-1]["stage"] == "speaker-diarization"


def test_small_supported_turn_attaches_to_compatible_speaker():
    cues = [{"id": "cue", "start": 1.0, "end": 2.0, "text": "x", "speaker": "speaker-01"}]
    turns = [{"start": 2.1, "end": 2.3, "speaker": "speaker-01"}]
    evidence = [{"start": 2.05, "end": 2.35}]
    reconciled, decisions = reconcile_unmatched_turns(cues, turns, evidence)
    assert reconciled[0]["end"] == 2.3
    assert decisions[0]["status"] == "attached"
    assert reconciled[0]["provenance"][-1]["method"] == "reconcile-supported-unmatched-turn"


def test_unmatched_turn_does_not_invent_or_cross_speakers():
    cues = [{"id": "cue", "start": 1.0, "end": 2.0, "text": "x", "speaker": "speaker-01"}]
    turns = [{"start": 2.1, "end": 2.3, "speaker": "speaker-02"}]
    evidence = [{"start": 2.05, "end": 2.35}]
    reconciled, decisions = reconcile_unmatched_turns(cues, turns, evidence)
    assert reconciled == cues
    assert decisions[0]["reason"] == "no-compatible-neighbor"


def test_canonical_translation_exports_without_discarding_internal_metadata(tmp_path: Path):
    """SRT serialization reads target text while canonical JSON retains lineage."""
    canonical = adapt_legacy_transcript({
        "language": "ja",
        "language_probability": 1.0,
        "task": "transcribe",
        "output_language": "ja",
        "segments": [{
            "id": "source-1", "start": 0.0, "end": 1.0,
            "text": "synthetic source", "speaker": "speaker-01",
        }],
    })
    translated = translated_document(
        canonical, ["Synthetic target."], "en", "test-model", "jpn_Jpan", "eng_Latn"
    )
    output = tmp_path / "translated.srt"

    write_translated_srt(output, translated["segments"])

    assert "Synthetic target." in output.read_text(encoding="utf-8")
    assert translated["segments"][0]["source_text"] == "synthetic source"
    assert translated["segments"][0]["speaker"] == "speaker-01"


def test_clean_transcript_joins_same_speaker_sentence_continuations():
    """Display boundaries do not force incomplete thoughts into translation units."""
    source = {
        "language": "en", "language_probability": 1.0,
        "task": "transcribe", "output_language": "en",
        "segments": [
            {"id": 1, "start": 0.0, "end": 1.0, "text": "This thought", "speaker": "speaker-01"},
            {"id": 2, "start": 1.2, "end": 2.0, "text": "continues here.", "speaker": "speaker-01"},
        ],
    }

    clean = build_clean_transcript(source)

    validate_canonical_timed_text(clean)
    assert clean["stage"] == "clean_transcript"
    assert len(clean["segments"]) == 1
    assert clean["segments"][0]["source_text"] == "This thought continues here."
    assert clean["segments"][0]["source_cue_ids"] == [1, 2]
    assert clean["segments"][0]["metadata"]["raw_source_texts"] == [
        "This thought", "continues here.",
    ]


def test_clean_transcript_never_joins_different_speakers():
    """A sentence continuation cannot cross an incompatible speaker boundary."""
    source = {
        "language": "en", "task": "transcribe", "output_language": "en",
        "segments": [
            {"id": 1, "start": 0.0, "end": 1.0, "text": "Incomplete", "speaker": "speaker-01"},
            {"id": 2, "start": 1.1, "end": 2.0, "text": "Other speaker.", "speaker": "speaker-02"},
        ],
    }

    clean = build_clean_transcript(source)

    assert len(clean["segments"]) == 2
    assert [item["speaker"] for item in clean["segments"]] == ["speaker-01", "speaker-02"]


def test_clean_transcript_splits_multiple_sentences_inside_one_rough_cue():
    """A broad ASR span is divided into genuine semantic translation units."""
    source = {
        "language": "en", "task": "transcribe", "output_language": "en",
        "segments": [{
            "id": 7, "start": 0.0, "end": 4.0,
            "text": "First synthetic sentence. Second synthetic sentence.",
            "speaker": "speaker-01",
        }],
    }

    clean = build_clean_transcript(source)

    assert [item["source_text"] for item in clean["segments"]] == [
        "First synthetic sentence.", "Second synthetic sentence.",
    ]
    assert all(item["source_cue_ids"] == [7] for item in clean["segments"])
    assert clean["segments"][0]["end"] == clean["segments"][1]["start"]


def test_clean_transcript_preserves_aligned_words_and_grouping_provenance():
    """Semantic reconstruction retains acoustic evidence for downstream timing."""
    source = {
        "language": "en", "language_probability": 0.97,
        "task": "transcribe", "output_language": "en",
        "segments": [{
            "id": "raw-1", "start": 0.0, "end": 1.0, "text": "Aligned words.",
            "speaker": "speaker-01", "confidence": {"asr": 0.94},
            "words": [
                {"start": 0.0, "end": 0.4, "word": "Aligned"},
                {"start": 0.5, "end": 1.0, "word": "words."},
            ],
        }],
    }

    clean = build_clean_transcript(source)

    cue = clean["segments"][0]
    assert [word["word"] for word in cue["words"]] == ["Aligned", "words."]
    assert cue["source_cue_ids"] == ["raw-1"]
    assert cue["confidence"]["source_cues"]["raw-1"] == {"asr": 0.94}
    assert cue["provenance"][-1]["stage"] == "semantic-grouping"


def _clean_context_fixture() -> dict:
    """Return four isolated synthetic semantic groups for context tests."""
    source = {
        "language": "ja", "language_probability": 1.0,
        "task": "transcribe", "output_language": "ja",
        "segments": [
            {"id": index, "start": index * 2.0, "end": index * 2.0 + 1.0,
             "text": f"Synthetic sentence {index}.", "speaker": f"speaker-0{index % 2 + 1}"}
            for index in range(1, 5)
        ],
    }
    return build_clean_transcript(source)


def test_context_request_contains_bounded_neighbors_and_direct_language_pair():
    """The current group receives context without using an English pivot."""
    clean = _clean_context_fixture()

    request = translation_request(clean["segments"], 2, "ja", "hi", context_size=2)
    prompt = translation_prompt(request)

    assert request.current_text == "Synthetic sentence 3."
    assert request.previous == ("Synthetic sentence 1.", "Synthetic sentence 2.")
    assert request.following == ("Synthetic sentence 4.",)
    assert "directly from ja to hi" in prompt
    assert "Return only the translation of CURRENT" in prompt


def test_integrity_translation_prompt_requires_explicit_source_numbers():
    request = TranslationRequest(
        "integrity", "ko", "en", "27세에서 31세", (), (), ("27", "31")
    )
    prompt = translation_prompt(request)
    assert "Preserve these explicit numerals exactly" in prompt
    assert "27, 31" in prompt


def test_contextual_translation_preserves_groups_and_records_configuration():
    """Translated semantic groups retain source lineage and context provenance."""
    clean = _clean_context_fixture()
    requests = []

    def fake_translate(request):
        requests.append(request)
        return f"target::{request.current_text}"

    translated = translate_contextual(
        clean, "hi", "fixture-model-v1", fake_translate, context_size=3
    )

    validate_canonical_timed_text(translated)
    assert translated["stage"] == "translated"
    assert translated["source_language"] == "ja"
    assert translated["output_language"] == "hi"
    assert len(requests) == len(clean["segments"])
    for source, target in zip(clean["segments"], translated["segments"]):
        assert target["id"] == source["id"]
        assert target["semantic_group_id"] == source["semantic_group_id"]
        assert target["source_cue_ids"] == source["source_cue_ids"]
        assert target["source_text"] == source["source_text"]
        assert target["speaker"] == source["speaker"]
        assert target["translated_text"].startswith("target::")
        assert target["provenance"][-1]["stage"] == "contextual-translation"
    assert translated["metadata"]["contextual_translation"]["direct_language_pair"] == ["ja", "hi"]


def test_contextual_translation_cache_avoids_repeat_model_calls(tmp_path: Path):
    """Identical versioned requests resume headlessly from deterministic cache."""
    clean = _clean_context_fixture()
    calls = []

    def fake_translate(request):
        calls.append(request.group_id)
        return f"translated {request.group_id}"

    first = translate_contextual(
        clean, "en", "fixture-model-v1", fake_translate,
        cache_directory=tmp_path,
    )
    assert len(calls) == len(clean["segments"])
    calls.clear()
    second = translate_contextual(
        clean, "en", "fixture-model-v1", fake_translate,
        cache_directory=tmp_path,
    )

    assert calls == []
    assert [item["translated_text"] for item in second["segments"]] == [
        item["translated_text"] for item in first["segments"]
    ]
    assert second["metadata"]["contextual_translation"]["cache_hits"] == len(clean["segments"])


def test_contextual_translation_replaces_cached_assistant_chatter(tmp_path: Path):
    clean = _clean_context_fixture()
    translate_contextual(
        clean, "en", "model", lambda request: "Please provide the current dialogue.",
        cache_directory=tmp_path,
    )
    calls = []
    refreshed = translate_contextual(
        clean, "en", "model", lambda request: calls.append(request.group_id) or "Valid subtitle.",
        cache_directory=tmp_path,
    )
    assert len(calls) == len(clean["segments"])
    assert all(item["translated_text"] == "Valid subtitle." for item in refreshed["segments"])


def test_contextual_translation_selectively_refreshes_failed_groups(tmp_path: Path):
    clean = _clean_context_fixture()
    translate_contextual(clean, "en", "model", lambda request: "old", cache_directory=tmp_path)
    calls = []
    refreshed = translate_contextual(
        clean, "en", "model", lambda request: calls.append(request.group_id) or "new",
        cache_directory=tmp_path, refresh_group_ids={"semantic-0002"},
    )
    assert calls == ["semantic-0002"]
    assert [item["translated_text"] for item in refreshed["segments"]] == ["old", "new", "old", "old"]


def test_translation_response_normalizes_only_safe_wrappers():
    assert normalize_translation_response('Translation: "Hello there."') == "Hello there."
    assert normalize_translation_response("```text\nHello there.\n```") == "Hello there."


def test_context_cache_key_changes_with_context_model_or_language():
    """Cached output cannot leak across distinct linguistic configurations."""
    clean = _clean_context_fixture()
    request = translation_request(clean["segments"], 1, "ja", "en")
    changed_language = translation_request(clean["segments"], 1, "ja", "es")
    changed_context = translation_request(clean["segments"], 1, "ja", "en", context_size=0)

    keys = {
        cache_key(request, "model-a"),
        cache_key(request, "model-b"),
        cache_key(changed_language, "model-a"),
        cache_key(changed_context, "model-a"),
    }
    assert len(keys) == 4


def test_contextual_translation_rejects_wrong_stage_and_empty_output():
    """Only clean groups with complete translations can be promoted."""
    clean = _clean_context_fixture()
    wrong_stage = {**clean, "stage": "canonical_source"}
    with pytest.raises(ValueError, match="clean_transcript"):
        translate_contextual(wrong_stage, "en", "model", lambda request: "target")
    with pytest.raises(RuntimeError, match="empty text"):
        translate_contextual(clean, "en", "model", lambda request: "  ")


def test_translation_integrity_detects_numbers_density_and_repetition():
    assert {item["type"] for item in integrity_issues("Order 12 items", "Order 13 items")} == {"number_mismatch"}
    assert integrity_issues("A sufficiently long source sentence", "x")[0]["type"] == "translation_too_short"
    assert integrity_issues("hello", "yes, yes, yes")[0]["type"] in {"translation_too_long", "repeated_translation_clause"}
    assert integrity_issues("行け", "Go over there right now") == []
    assert integrity_issues("number one", "No. 1") == []
    assert integrity_issues("Return in 3 days", "Return in three days") == []
    assert integrity_issues("Ages 27 and 31", "Ages twenty-seven and thirty-one") == []
    assert integrity_issues("Age 27", "Age 28")[0]["type"] == "number_mismatch"
    assert integrity_issues("Henry 3세, 5대 조부", "Henry III, fifth-generation ancestor") == []
    assert integrity_issues("100만 돌파", "surpassed one million") == []
    assert integrity_issues("な、な、な、な。", "No, no, no.") == []
    assert integrity_issues("沈州各处高挂桑番但", "The Chinese government has also been trying to move the country forward.") == []


def test_translation_integrity_retries_and_preserves_lineage():
    clean = _clean_context_fixture()
    translated = translate_contextual(clean, "en", "model", lambda request: "x")

    repaired, report = enforce_translation_integrity(
        translated, lambda source, context: f"Valid translation for {source}",
    )

    assert report["passed"]
    assert all(item["id"] == source["id"] for item, source in zip(repaired["segments"], clean["segments"]))
    assert all(item["provenance"][-1]["stage"] == "translation-integrity" for item in repaired["segments"])


def test_translation_integrity_uses_semantic_resegmentation_after_retry():
    clean = build_clean_transcript({
        "language": "en", "task": "transcribe", "output_language": "en",
        "segments": [{"start": 0, "end": 4, "text": "First clause, second clause.", "speaker": "one"}],
    })
    translated = translate_contextual(clean, "es", "model", lambda request: "x")
    routes = []

    def retry(source, context):
        routes.append(context["route"])
        return "x" if context["route"] == "retry" else f"translated {source}"

    repaired, report = enforce_translation_integrity(translated, retry)

    assert semantic_pieces("First clause, second clause.") == ["First clause", "second clause."]
    assert "semantic-resegmentation" in routes
    assert report["passed"]
    assert repaired["segments"][0]["translated_text"].startswith("translated First")


def test_translation_integrity_retains_rejection_after_bounded_failure():
    clean = _clean_context_fixture()
    translated = translate_contextual(clean, "en", "model", lambda request: "x")
    _, report = enforce_translation_integrity(translated, lambda source, context: "x")
    assert not report["passed"]
    assert report["failed_group_count"] == len(clean["segments"])


def test_display_mapping_uses_source_pause_and_preserves_target_text_once():
    clean = build_clean_transcript({
        "language": "en", "task": "transcribe", "output_language": "en",
        "segments": [{
            "id": 1, "start": 0.0, "end": 2.5,
            "text": "Synthetic source sentence.", "speaker": "speaker-01",
            "words": [
                {"start": 0.0, "end": 1.0, "word": "Synthetic"},
                {"start": 1.5, "end": 2.5, "word": "source."},
            ],
        }],
    })
    translated = translate_contextual(
        clean, "es", "model",
        lambda request: "First translated sentence. Second translated sentence.",
    )

    mapped = map_translated_groups(translated, maximum_characters=30)

    validate_canonical_timed_text(mapped)
    assert len(mapped["segments"]) == 2
    assert mapped["segments"][0]["end"] == 1.25
    assert mapped["segments"][1]["start"] == 1.25
    assert " ".join(cue["translated_text"] for cue in mapped["segments"]) == translated["segments"][0]["translated_text"]
    assert {cue["semantic_group_id"] for cue in mapped["segments"]} == {"semantic-0001"}
    assert [cue["id"] for cue in mapped["segments"]] == [
        "semantic-0001.display-01", "semantic-0001.display-02",
    ]
    assert mapped["segments"][0]["source_text"] is not None
    assert mapped["segments"][1]["source_text"] is None


def test_display_boundary_allocation_falls_back_to_weighted_timing():
    boundaries = allocate_boundaries(0.0, 10.0, ["short", "a much longer part"], [])
    assert boundaries == [2.5]
    assert pause_boundaries([
        {"start": 0.0, "end": 1.0}, {"start": 1.05, "end": 2.0},
    ]) == []


def test_display_mapping_rejects_missing_translation():
    clean = _clean_context_fixture()
    invalid = {**clean, "stage": "translated"}
    with pytest.raises(ValueError, match="has no translation"):
        map_translated_groups(invalid)


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


def test_compute_device_prefers_cuda_and_falls_back_to_cpu(monkeypatch):
    """Automatic compute uses an accessible GPU without requiring one."""
    monkeypatch.setattr(runtime_device.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(runtime_device.torch.cuda, "get_device_capability", lambda: (8, 6))
    monkeypatch.setattr(runtime_device.torch.cuda, "get_arch_list", lambda: ["sm_75", "sm_86"])
    assert runtime_device.resolve_device("auto") == "cuda"
    assert runtime_device.whisper_compute_type("cuda") == "float16"
    monkeypatch.setattr(runtime_device.torch.cuda, "is_available", lambda: False)
    assert runtime_device.resolve_device("auto") == "cpu"
    assert runtime_device.resolve_device("cpu") == "cpu"
    with pytest.raises(RuntimeError):
        runtime_device.resolve_device("cuda")


def test_compute_device_rejects_an_unsupported_torch_architecture(monkeypatch):
    """CUDA availability alone cannot select a wheel lacking the installed GPU."""
    monkeypatch.setattr(runtime_device.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(runtime_device.torch.cuda, "get_device_capability", lambda: (6, 1))
    monkeypatch.setattr(runtime_device.torch.cuda, "get_arch_list", lambda: ["sm_75", "sm_86"])

    assert runtime_device.resolve_device("auto") == "cpu"
    with pytest.raises(RuntimeError, match="compute capability 6.1"):
        runtime_device.resolve_device("cuda")


def test_pascal_uses_supported_whisper_precision(monkeypatch):
    """CTranslate2 uses its documented int8/float32 path on compute 6.1."""
    monkeypatch.setattr(runtime_device.torch.cuda, "get_device_capability", lambda: (6, 1))
    assert runtime_device.whisper_compute_type("cuda") == "int8_float32"


def test_headless_runtime_rejects_unwritable_shared_cache(tmp_path: Path, monkeypatch):
    """An unattended run never hides large model downloads inside run output."""
    monkeypatch.setattr(
        "videotranslator.commands.create_subtitles._is_writable_directory",
        lambda path: False,
    )
    with pytest.raises(RuntimeError, match="Shared cache HF_HOME is not writable"):
        prepare_runtime_environment(tmp_path / "output", {"PYTHON_CACHE_HOME": str(tmp_path / "shared")})


def test_headless_runtime_uses_one_shared_cache_root(tmp_path: Path):
    shared = tmp_path / "shared"
    env, events = prepare_runtime_environment(
        tmp_path / "output", {"PYTHON_CACHE_HOME": str(shared)}
    )
    assert env["HF_HOME"] == str(shared / "huggingface")
    assert env["TORCH_HOME"] == str(shared / "torch")
    assert env["MPLCONFIGDIR"] == str(shared / "matplotlib")
    assert events == []
    assert not (tmp_path / "output" / "model-cache").exists()


def test_headless_runtime_prefers_shared_ffmpeg_for_torchcodec(tmp_path: Path):
    binary = tmp_path / "shared" / "bin"
    binary.mkdir(parents=True)
    (binary / "ffmpeg.exe").write_bytes(b"")
    (binary / "avcodec-62.dll").write_bytes(b"")
    env = {"FFMPEG_SHARED_HOME": str(binary)}
    assert shared_ffmpeg_bin(env) == binary


def test_headless_token_check_accepts_supported_environment_names():
    """Schedulers can inject either standard Hugging Face token variable."""
    assert hugging_face_token_available({"HF_TOKEN": "secret"})
    legacy_env = {"HUGGING_FACE_HUB_TOKEN": "secret"}
    assert hugging_face_token_available(legacy_env)
    assert legacy_env["HF_TOKEN"] == "secret"


def test_cpu_recovery_avoids_unbounded_large_model(monkeypatch):
    """Large Whisper recovery is capped on CPU to prevent hour-long stalls."""
    monkeypatch.setattr("videotranslator.commands.create_subtitles.resolve_device", lambda device: "cpu")

    assert recovery_candidates("large-v3", "auto") == [("medium", "cpu"), ("small", "cpu")]


def test_recovery_retries_a_smaller_model_after_failure(tmp_path: Path, monkeypatch):
    """A failed large-model recovery resolves itself without operator action."""
    output = tmp_path / "recovered.json"
    report = tmp_path / "report.json"
    calls = []

    def fake_run(command, expected, **kwargs):
        calls.append((command[command.index("--model") + 1], command[command.index("--device") + 1]))
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, command)
        for path in expected:
            path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr("videotranslator.commands.create_subtitles.resolve_device", lambda device: "cuda")
    monkeypatch.setattr("videotranslator.commands.create_subtitles.run_command", fake_run)
    events = []
    command = ["python", "recover.py", "--model", "large-v3", "--device", "auto"]

    selected = run_recovery_with_fallbacks(
        command, [output, report], force=False, env={}, requested_model="large-v3",
        requested_device="auto", timeout=30, events=events,
    )

    assert selected == ("medium", "cuda")
    assert calls == [("large-v3", "cuda"), ("medium", "cuda")]
    assert len(events) == 2


def test_dependency_profile_selection_covers_pascal_modern_and_cpu_hosts():
    """Hardware-aware installation selects one explicit PyTorch wheel family."""
    assert parse_compute_capability("NVIDIA GeForce GTX 1050, 6.1\n") == (6, 1)
    assert select_profile((6, 1)) == "cu126"
    assert select_profile((7, 5)) == "cu128"
    assert select_profile(None) == "cpu"


def test_finalized_subtitles_cover_rough_speech_window_without_overlap():
    """Translated word groups share their full source speech window safely."""
    transcript = {"segments": [{
        "start": 1.0, "end": 5.0, "text": "First. Second.",
        "words": [
            {"start": 1.5, "end": 2.0, "word": "First."},
            {"start": 3.0, "end": 3.5, "word": "Second."},
        ],
    }]}
    result = finalize(transcript, 8.0, 84)
    assert [(cue["start"], cue["end"]) for cue in result["segments"]] == [
        (1.5, 2.5), (2.5, 3.5),
    ]


def test_finalized_translation_uses_matching_source_word_envelope():
    """Equal source/target segment counts provide language-independent sync."""
    target = {"segments": [{
        "start": 2.0, "end": 3.0, "text": "Hello.",
        "words": [{"start": 2.2, "end": 2.7, "word": "Hello."}],
    }]}
    source = {"segments": [{
        "start": 1.0, "end": 4.0, "text": "source",
        "words": [{"start": 1.4, "end": 3.6, "word": "source"}],
    }]}
    result = finalize(target, 8.0, 84, source)
    assert (result["segments"][0]["start"], result["segments"][0]["end"]) == (1.4, 3.6)


def test_subtitle_repair_splits_text_and_borrows_only_available_silence():
    """Readability repair respects neighboring dialogue boundaries."""
    assert text_chunks("But,", 84) == ["But."]
    transcript = {"segments": [
        {"start": 1.0, "end": 1.2, "text": "A short phrase."},
        {"start": 2.0, "end": 3.0, "text": "Next."},
    ]}
    result = repair(transcript, minimum_duration=0.5, maximum_characters_per_second=10)
    assert result["segments"][0]["end"] <= result["segments"][1]["start"]
    assert result["segments"][0]["end"] == 2.0


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


def test_subtitle_qa_rejects_missing_independent_diarized_speech():
    """Diarization catches speech absent from the ASR-derived source transcript."""
    target = {"segments": [{"start": 0.0, "end": 1.0, "text": "First."}]}
    diarization = {"turns": [
        {"start": 0.0, "end": 1.0, "speaker": "A"},
        {"start": 2.0, "end": 3.0, "speaker": "B"},
    ]}
    report = analyze(target, 8.0, diarization_report=diarization)
    assert report["diarized_coverage"] == {"turn_coverage": 0.5, "time_coverage": 0.5}
    assert report["issue_counts"] == {"missing_diarized_turns": 1, "missing_diarized_time": 1}


def test_reconciliation_drops_long_unaligned_reference_spans():
    """A long ASR span without word evidence must not become a subtitle cue."""
    transcript = {"language": "ja", "segments": []}
    retained = [
        {"start": 1.0, "end": 3.0, "text": "short"},
        {"start": 10.0, "end": 35.0, "text": "bad timing"},
    ]
    result = build_reconciled_transcript(transcript, [], retained)
    assert [segment["text"] for segment in result["segments"]] == ["short"]


def test_unattended_subtitle_paths_are_isolated_and_deterministic(tmp_path):
    """One video run keeps intermediate attempts away from promoted output."""
    result = subtitle_artifact_paths(Path("episode.mp4"), tmp_path, "en")
    assert result["final"] == tmp_path / "final.srt"
    assert result["rejected"] == tmp_path / "rejected.srt"
    assert result["source"] == tmp_path / "transcription" / "episode.json"
    assert result["attempts"] == tmp_path / "attempts"


def test_unattended_subtitle_attempt_ranking_prefers_speech_coverage():
    """More independent speech coverage wins despite a few extra diagnostics."""
    lower = {"diarized_coverage": {"time_coverage": 0.7, "turn_coverage": 0.8}, "source_coverage": {"source_event_coverage": 1.0}, "issues": []}
    higher = {"diarized_coverage": {"time_coverage": 0.9, "turn_coverage": 0.9}, "source_coverage": {"source_event_coverage": 0.99}, "issues": [{}, {}]}
    assert quality_score(higher) > quality_score(lower)


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

    assert Path(command[1]).parent.name == "commands"
    assert "--language" not in command
    assert command[command.index("--target-language") + 1] == "en"
    assert artifact_paths["transcript_json"].name.endswith(".auto.en.json")


def test_pipeline_defaults_heavy_stages_to_automatic_compute(tmp_path: Path):
    """Configuration-free execution prefers CUDA and remains CPU-safe."""
    config = {
        "project_id": "example",
        "input_video": str(tmp_path / "episode.mp4"),
        "output_root": str(tmp_path / "outputs"),
        "translation": {"target_language": "en", "model": "small"},
    }
    command, _ = stage_command("separate", config, paths(config))
    assert command[command.index("--device") + 1] == "auto"


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


def test_piper_models_dir_uses_shared_environment_cache(tmp_path: Path, monkeypatch):
    """A configured shared cache prevents duplicate voice downloads per run."""
    shared_cache = tmp_path / "shared-piper-voices"
    monkeypatch.setenv("PIPER_MODELS_DIR", str(shared_cache))

    assert piper_models_dir(tmp_path / "run") == shared_cache


def test_piper_models_dir_defaults_to_output_directory(tmp_path: Path, monkeypatch):
    """Existing run-local cache behavior remains when no override is configured."""
    monkeypatch.delenv("PIPER_MODELS_DIR", raising=False)
    output_dir = tmp_path / "run"

    assert piper_models_dir(output_dir) == output_dir / "models"


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


def test_recovered_words_expand_to_independent_speech_envelope():
    recovered = [{"start": 2.0, "end": 2.5, "text": "synthetic", "provenance": "decoder"}]
    evidence = [{"start": 1.5, "end": 3.0, "speaker": "one"}]
    canonical = [{"start": 0.0, "end": 1.0}, {"start": 3.5, "end": 4.0}]
    result = preserve_speech_envelopes(recovered, evidence, canonical)
    assert (result[0]["start"], result[0]["end"]) == (1.5, 3.0)
    assert result[0]["speech_envelope"]["status"] == "expanded"
    assert result[0]["provenance"][-1]["method"] == "independent-speech-envelope"


def test_speech_envelope_never_crosses_canonical_neighbor():
    recovered = [{"start": 2.0, "end": 2.5, "text": "synthetic"}]
    evidence = [{"start": 0.5, "end": 4.0}]
    canonical = [{"start": 0.0, "end": 1.8}, {"start": 2.8, "end": 4.0}]
    result = preserve_speech_envelopes(recovered, evidence, canonical)
    assert (result[0]["start"], result[0]["end"]) == (1.8, 2.8)


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


def test_approved_canonical_data_prepares_complete_tts_handoff():
    canonical = adapt_legacy_transcript({
        "language": "ja", "task": "transcribe", "output_language": "ja",
        "segments": [{"id": "cue-1", "start": 1.0, "end": 3.0, "text": "source", "speaker": "speaker-01"}],
    })
    canonical = translated_document(canonical, ["Target text."], "en", "model", "ja", "en")
    canonical["metadata"]["translation_integrity"] = {"passed": True}
    script = prepare_canonical_tts(canonical, {"speaker-01": "en_US-test-medium"})
    segment = script["segments"][0]
    assert canonical_is_approved(canonical)
    assert segment["text"] == "Target text."
    assert segment["voice"] == "en_US-test-medium"
    assert segment["duration_constraint"]["available_seconds"] == 2.0
    assert segment["semantic_group_id"] == canonical["segments"][0]["semantic_group_id"]
    assert segment["source_cue_ids"] == canonical["segments"][0]["source_cue_ids"]
    assert segment["provenance"][-1]["stage"] == "tts-handoff"
    assert synthesis_text(segment) == "Target text."


def test_canonical_tts_rejects_unapproved_data_or_missing_voice():
    canonical = adapt_legacy_transcript({
        "language": "en", "task": "translate", "output_language": "es",
        "segments": [{"id": "cue", "start": 0.0, "end": 1.0, "text": "Target."}],
    })
    with pytest.raises(ValueError, match="explicitly approved"):
        prepare_canonical_tts(canonical, {"unknown": "voice"})
    canonical["metadata"]["approval"] = {"status": "approved"}
    with pytest.raises(ValueError, match="No voice assigned"):
        prepare_canonical_tts(canonical)
