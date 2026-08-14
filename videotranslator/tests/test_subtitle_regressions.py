"""Offline regression contracts for canonical subtitle quality improvements."""

import json
import re
from pathlib import Path

import pytest

from videotranslator.commands.build_clean_transcript import build_clean_transcript
from videotranslator.commands.canonical_timed_text import validate_canonical_timed_text
from videotranslator.commands.map_translation_cues import map_translated_groups
from videotranslator.commands.qa_transcript import analyze
from videotranslator.commands.translate_contextual import FallbackContextTranslator, translate_contextual, translation_request, valid_translation_response
from videotranslator.commands.export_subtitles import ass_content, export_subtitles, srt_content
from videotranslator.commands.reprocess_subtitles import metric_comparison, reprocess_existing, upstream_recommendations
from videotranslator.commands.headless_preflight import PreflightError, preflight_reprocess
from videotranslator.commands.run_canonical_subtitles import align_recovered_envelopes, compress_dense_translations, run_canonical_attempt, stable_diarization_turns
from videotranslator.commands.create_subtitles import parse_args as parse_subtitle_args
from videotranslator.commands.repair_subtitles import iterative_repair, rebalance_neighbor_timing, repair, repair_short_cues, redistribute_group_timing, subtitle_lines


FIXTURE = Path(__file__).parent / "fixtures" / "subtitle_quality_baseline.json"


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def mapped_fixture(target: str = "First synthetic sentence. Second synthetic sentence.") -> dict:
    clean = build_clean_transcript({
        "language": "en", "language_probability": 1.0,
        "task": "transcribe", "output_language": "en",
        "segments": [{
            "id": "source-1", "start": 0.0, "end": 4.0,
            "text": "One complete synthetic source thought.",
            "speaker": "speaker-01",
            "words": [
                {"start": 0.0, "end": 1.0, "word": "One"},
                {"start": 1.4, "end": 2.0, "word": "complete"},
                {"start": 2.1, "end": 4.0, "word": "thought."},
            ],
        }],
    })
    translated = translate_contextual(clean, "es", "fixture-model", lambda request: target)
    return map_translated_groups(translated, maximum_characters=32)


def test_frozen_fixture_exercises_every_original_blocking_category():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    report = analyze(
        fixture["candidate"], 12.0,
        source_transcript=fixture["source_evidence"],
        diarization_report=fixture["diarization_evidence"],
    )
    assert set(fixture["expected_issue_types"]) <= set(report["issue_counts"])


def test_display_mapping_has_valid_order_without_overlap():
    mapped = mapped_fixture()
    timings = [(cue["start"], cue["end"]) for cue in mapped["segments"]]
    assert all(start < end for start, end in timings)
    assert all(left[1] <= right[0] for left, right in zip(timings, timings[1:]))


def test_display_mapping_preserves_all_target_text_exactly_once():
    target = "First synthetic sentence. Second synthetic sentence."
    mapped = mapped_fixture(target)
    reconstructed = " ".join(cue["translated_text"] for cue in mapped["segments"])
    assert compact(reconstructed) == compact(target)


def test_every_display_cue_retains_canonical_lineage():
    mapped = mapped_fixture()
    validate_canonical_timed_text(mapped)
    for cue in mapped["segments"]:
        assert cue["id"]
        assert cue["semantic_group_id"] == "semantic-0001"
        assert cue["source_cue_ids"] == ["source-1"]
        assert cue["speaker"] == "speaker-01"
        assert cue["words"]
        assert cue["provenance"][-1]["stage"] == "display-cue-mapping"


def test_mapping_is_deterministic_for_identical_input():
    first = mapped_fixture()
    second = mapped_fixture()
    assert first == second


def test_qa_contract_blocks_short_long_fast_and_overlapping_cues():
    transcript = {"segments": [
        {"start": 0.0, "end": 13.0, "text": "long"},
        {"start": 12.9, "end": 13.0, "text": "far too fast for its timing"},
    ]}
    report = analyze(transcript, maximum_duration=12.0)
    assert {"long_duration", "overlap", "short_duration", "fast_reading_speed"} <= set(report["issue_counts"])


def test_long_cue_is_split_with_hard_duration_and_text_conservation():
    transcript = {"segments": [{
        "id": "long", "start": 0.0, "end": 35.756,
        "text": "First synthetic phrase. Second synthetic phrase. Third synthetic phrase.",
        "words": [
            {"start": 0.0, "end": 8.0, "word": "First"},
            {"start": 10.0, "end": 20.0, "word": "Second"},
            {"start": 23.0, "end": 35.756, "word": "Third"},
        ],
    }]}
    repaired = repair(transcript, maximum_duration=12.0)
    cues = repaired["segments"]
    assert len(cues) >= 3
    assert all(cue["end"] - cue["start"] <= 12.0 for cue in cues)
    assert compact(" ".join(cue["text"] for cue in cues)) == compact(transcript["segments"][0]["text"])
    assert len({cue["id"] for cue in cues}) == len(cues)


def test_long_cue_split_prefers_a_safe_acoustic_pause():
    transcript = {"segments": [{
        "start": 0.0, "end": 20.0, "text": "First half. Second half.",
        "words": [
            {"start": 0.0, "end": 8.0, "word": "First"},
            {"start": 12.0, "end": 20.0, "word": "Second"},
        ],
    }]}
    cues = repair(transcript, maximum_duration=12.0)["segments"]
    assert cues[0]["end"] == 10.0
    assert cues[1]["start"] == 10.0


def test_short_cue_extends_only_into_available_silence():
    cues = repair_short_cues(
        [
            {"start": 0.0, "end": 1.0, "text": "First."},
            {"start": 1.2, "end": 1.3, "text": "Hi."},
            {"start": 1.7, "end": 2.5, "text": "Third."},
        ],
        0.5, 12.0, 84, 20.0,
    )
    assert cues[1]["start"] >= cues[0]["end"]
    assert cues[1]["end"] <= cues[2]["start"]
    assert round(cues[1]["end"] - cues[1]["start"], 3) == 0.5
    assert cues[1]["provenance"][-1]["method"] == "extend-short-cue-into-silence"


def test_short_cue_merges_only_with_same_speaker_and_semantic_group():
    base = [
        {"id": "a", "semantic_group_id": "g", "source_cue_ids": [1], "speaker": "one", "start": 0.0, "end": 0.2, "text": "Hi"},
        {"id": "b", "semantic_group_id": "g", "source_cue_ids": [2], "speaker": "one", "start": 0.2, "end": 1.2, "text": "there."},
    ]
    merged = repair_short_cues(base, 0.5, 12.0, 84, 20.0)
    assert len(merged) == 1
    assert merged[0]["text"] == "Hi there."
    assert merged[0]["source_cue_ids"] == [1, 2]
    assert merged[0]["provenance"][-1]["method"] == "merge-short-compatible-cues"

    incompatible = [dict(base[0]), {**base[1], "speaker": "two"}]
    retained = repair_short_cues(incompatible, 0.5, 12.0, 84, 20.0)
    assert len(retained) == 2


def test_semantic_group_timing_is_redistributed_by_target_density():
    cues = [
        {"id": "a", "semantic_group_id": "g", "start": 0.0, "end": 1.0, "text": "1234567890"},
        {"id": "b", "semantic_group_id": "g", "start": 1.0, "end": 4.0, "text": "123456789012345678901234567890"},
    ]
    result = redistribute_group_timing(cues, 20.0)
    assert result[0]["end"] == 1.0
    assert result[1]["start"] == 1.0
    assert all(len(re.sub(r"\s+", "", cue["text"])) / (cue["end"] - cue["start"]) <= 20 for cue in result)


def test_overfull_semantic_group_still_distributes_excess_speed_evenly():
    cues = [
        {"id": "a", "semantic_group_id": "g", "start": 0.0, "end": 0.1, "text": "a" * 40},
        {"id": "b", "semantic_group_id": "g", "start": 0.1, "end": 2.0, "text": "b" * 40},
    ]
    result = redistribute_group_timing(cues, 20.0)
    speeds = [len(cue["text"]) / (cue["end"] - cue["start"]) for cue in result]
    assert max(speeds) - min(speeds) < 0.1


def test_short_cue_borrows_slack_across_contiguous_neighbor():
    cues = [
        {"id": "donor", "start": 0.0, "end": 1.5, "text": "Hi"},
        {"id": "short", "start": 1.5, "end": 1.7, "text": "What?"},
    ]
    result = rebalance_neighbor_timing(cues, 0.5, 20.0)
    assert result[1]["end"] - result[1]["start"] >= 0.5
    assert result[0]["end"] == result[1]["start"]
    assert result[0]["end"] - result[0]["start"] >= 0.5


def test_subtitle_layout_balances_words_and_preserves_text():
    text = "one two three four five six seven"
    laid_out = subtitle_lines(text, maximum_line_characters=20)
    assert laid_out.count("\n") == 1
    assert laid_out.replace("\n", " ") == text
    assert all(len(line) <= 20 for line in laid_out.splitlines())


def test_iterative_repair_is_bounded_and_records_objective_progress():
    transcript = {"segments": [{"start": 0.0, "end": 20.0, "text": "First synthetic sentence. Second synthetic sentence."}]}
    repaired, audit = iterative_repair(transcript, maximum_passes=3)
    assert len(audit["passes"]) <= 3
    assert audit["final_score"] <= audit["initial_score"]
    assert audit["termination_reason"] in {"stable-state", "quality-target-reached", "no-objective-improvement", "maximum-passes"}
    assert repaired["iterative_repair"] == audit


def test_iterative_repair_repeated_run_has_stable_semantic_output():
    transcript = {"segments": [{"start": 0.0, "end": 0.2, "text": "Hi."}]}
    first, _ = iterative_repair(transcript)
    second, audit = iterative_repair(first)
    first_state = [(cue["start"], cue["end"], cue["text"]) for cue in first["segments"]]
    second_state = [(cue["start"], cue["end"], cue["text"]) for cue in second["segments"]]
    assert first_state == second_state
    assert audit["termination_reason"] == "stable-state"


def test_validated_srt_and_ass_exports_match_canonical_cues(tmp_path: Path):
    mapped = mapped_fixture()
    srt, ass = tmp_path / "output.srt", tmp_path / "output.ass"
    report = export_subtitles(mapped, srt, ass)
    assert report["cue_count"] == len(mapped["segments"])
    assert srt.read_text(encoding="utf-8").count(" --> ") == len(mapped["segments"])
    ass_text = ass.read_text(encoding="utf-8")
    assert ass_text.count("Dialogue: ") == len(mapped["segments"])
    assert "Style: speaker-01" in ass_text
    assert all(cue["translated_text"] in srt.read_text(encoding="utf-8") for cue in mapped["segments"])


def test_ass_export_preserves_line_breaks_and_speaker_style():
    mapped = mapped_fixture()
    mapped["segments"][0]["translated_text"] = "Line one\nLine two"
    content = ass_content(mapped)
    assert "Line one\\NLine two" in content
    assert ",speaker-01,speaker-01," in content


def test_export_blocks_overlapping_or_empty_canonical_cues():
    mapped = mapped_fixture()
    mapped["segments"][1]["start"] = mapped["segments"][0]["end"] - 0.1
    try:
        srt_content(mapped)
        assert False, "overlap should fail"
    except ValueError as error:
        assert "overlaps" in str(error)
    mapped = mapped_fixture()
    mapped["segments"][0]["translated_text"] = ""
    mapped["segments"][0]["source_text"] = None
    try:
        srt_content(mapped)
        assert False, "empty cue should fail"
    except ValueError as error:
        assert "no display text" in str(error)


def test_independent_qa_accepts_canonical_translated_text_field():
    mapped = mapped_fixture("Short target. Another target.")
    report = analyze(mapped, maximum_duration=12.0)
    assert "empty_text" not in report["issue_counts"]


def test_incremental_reprocessor_reuses_expensive_artifacts_and_promotes_pass(tmp_path: Path):
    source = {"language": "en", "language_probability": 1.0, "task": "transcribe", "output_language": "en", "segments": [
        {"start": 0.0, "end": 2.0, "text": "Synthetic source."},
    ]}
    target = {"language": "en", "task": "translate", "output_language": "es", "segments": [
        {"start": 0.0, "end": 2.0, "text": "Destino sintetico."},
    ]}
    result = reprocess_existing(source, target, tmp_path)
    assert result["status"] == "passed"
    assert (tmp_path / "passed.srt").is_file()
    assert not (tmp_path / "rejected.srt").exists()
    assert "transcription" in result["reused_stages"]
    assert result["executed_stages"] == ["canonical-migration", "display-mapping", "iterative-repair", "qa", "export"]


def test_incremental_report_explains_cheapest_upstream_reruns():
    qa = {"issue_counts": {"fast_reading_speed": 2, "missing_diarized_turns": 1}}
    assert [item["stage"] for item in upstream_recommendations(qa)] == [
        "contextual-translation-and-display-mapping", "diarization-reconciliation",
    ]
    comparison = metric_comparison(
        {"segment_count": 10, "fast_reading_speed_count": 4},
        {"segment_count": 11, "issue_counts": {"fast_reading_speed": 2}},
    )
    assert comparison["delta"]["segment_count"] == 1
    assert comparison["delta"]["fast_reading_speed_count"] == -2


def test_headless_preflight_validates_artifacts_output_and_resume(tmp_path: Path):
    source = tmp_path / "source.json"
    target = tmp_path / "target.json"
    output = tmp_path / "output"
    source.write_text(json.dumps({"segments": [{"text": "source"}]}), encoding="utf-8")
    target.write_text(json.dumps({"segments": [{"text": "target"}]}), encoding="utf-8")
    output.mkdir()
    (output / "qa.json").write_text("{}", encoding="utf-8")
    report = preflight_reprocess(source, target, output, minimum_free_bytes=1)
    assert report["passed"]
    assert report["checks"][-1]["found"] == ["qa.json"]
    assert "documents" in report


def test_headless_preflight_rejects_missing_or_mismatched_artifacts(tmp_path: Path):
    source = tmp_path / "source.json"
    target = tmp_path / "target.json"
    source.write_text(json.dumps({"segments": [{}, {}]}), encoding="utf-8")
    target.write_text(json.dumps({"segments": [{}]}), encoding="utf-8")
    with pytest.raises(PreflightError, match="segment mismatch"):
        preflight_reprocess(source, target, tmp_path / "output", minimum_free_bytes=1)
    with pytest.raises(PreflightError, match="not found"):
        preflight_reprocess(tmp_path / "missing.json", target, tmp_path / "output", minimum_free_bytes=1)


def test_main_subtitle_command_defaults_to_contextual_translation():
    args = parse_subtitle_args(["video.mp4"])
    assert args.translation_model == "Qwen/Qwen2.5-0.5B-Instruct"
    assert args.translation_backend == "causal"
    assert args.translation_context_size == 3
    assert args.translation_fallback_model == "facebook/nllb-200-distilled-600M"
    assert args.legacy_cue_translation is False


def test_canonical_attempt_runs_semantic_translation_through_validated_export(tmp_path: Path):
    source = {
        "language": "en", "language_probability": 1.0,
        "task": "transcribe", "output_language": "en",
        "segments": [{
            "id": 1, "start": 0.0, "end": 2.0,
            "text": "Synthetic source.",
            "words": [{"start": 0.0, "end": 1.8, "word": "Synthetic source."}],
        }],
    }
    diarization = {
        "turns": [{"start": 0.0, "end": 2.0, "speaker": "RAW_A"}],
    }
    result = run_canonical_attempt(
        source, source, diarization, "es", "fixture-model",
        lambda request: "Destino sintetico.", tmp_path,
    )
    assert result["status"] == "passed"
    assert result["translation_integrity"]["passed"]
    assert (tmp_path / "clean-transcript.json").is_file()
    assert (tmp_path / "contextual-translation.json").is_file()
    assert (tmp_path / "passed.srt").is_file()
    assert (tmp_path / "passed.ass").is_file()
    assert result["qa"]["passed"]


def test_semantic_groups_preserve_complete_source_timing_envelope():
    source = {
        "language": "ja", "task": "transcribe", "output_language": "ja",
        "segments": [{
            "id": 1, "start": 0.0, "end": 4.0, "text": "one two",
            "words": [
                {"start": 0.5, "end": 1.0, "word": "one"},
                {"start": 3.0, "end": 3.5, "word": "two"},
            ],
        }],
    }
    clean = build_clean_transcript(source, maximum_gap=0.1)
    assert clean["segments"][0]["start"] == 0.0
    assert clean["segments"][-1]["end"] == 4.0
    assert clean["segments"][0]["end"] == clean["segments"][1]["start"]


def test_overlapping_source_envelopes_cannot_collapse_semantic_groups():
    source = {
        "language": "ja", "task": "transcribe", "output_language": "ja",
        "segments": [
            {"id": 1, "start": 0.0, "end": 2.5, "text": "one", "words": [{"start": 0.5, "end": 1.0, "word": "one"}]},
            {"id": 2, "start": 0.8, "end": 3.0, "text": "two", "words": [{"start": 1.5, "end": 2.0, "word": "two"}]},
        ],
    }
    clean = build_clean_transcript(source, maximum_gap=0.1)
    assert all(item["end"] > item["start"] for item in clean["segments"])
    assert all(left["end"] <= right["start"] for left, right in zip(clean["segments"], clean["segments"][1:]))


def test_raw_diarization_labels_map_to_stable_first_appearance_ids():
    turns = stable_diarization_turns({"turns": [
        {"start": 2.0, "end": 3.0, "speaker": "B"},
        {"start": 0.0, "end": 1.0, "speaker": "A"},
        {"start": 4.0, "end": 5.0, "speaker": "A"},
    ]})
    by_source = {item["source_label"]: item["speaker"] for item in turns}
    assert by_source == {"A": "speaker-01", "B": "speaker-02"}


def test_dense_translation_is_replaced_only_by_integrity_safe_budgeted_candidate():
    clean = build_clean_transcript({
        "language": "ko", "task": "transcribe", "output_language": "ko",
        "segments": [{"start": 0.0, "end": 1.0, "text": "항목 3", "speaker": "one"}],
    })
    translated = translate_contextual(clean, "en", "model", lambda request: "A very long translation containing item 3")
    requests = []

    def shorten(request):
        requests.append(request)
        return "Item 3"

    compressed, events = compress_dense_translations(translated, shorten, 20.0)
    assert compressed["segments"][0]["translated_text"] == "Item 3"
    assert requests[0].maximum_characters == 20
    assert requests[0].required_numbers == ("3",)
    assert events[0]["accepted"]


def test_canonical_attempt_reports_integrity_after_readability_compression(tmp_path: Path):
    source = {
        "language": "zh", "task": "transcribe", "output_language": "zh",
        "segments": [{
            "start": 0.0, "end": 1.0, "text": "短句文本内容很多了",
            "words": [{"start": 0.0, "end": 1.0, "word": "短句文本内容很多了"}],
        }],
    }
    diarization = {"turns": [{"start": 0.0, "end": 1.0, "speaker": "RAW"}]}

    def backend(request):
        return "Short line." if request.maximum_characters else "This response is deliberately far too long for the compact source dialogue text."

    result = run_canonical_attempt(source, source, diarization, "en", "model", backend, tmp_path)
    assert result["translation_integrity"]["passed"]


def test_recovered_envelopes_align_to_nearby_strong_words():
    recovered = {"segments": [{"start": 1.2, "end": 1.8, "text": "dialogue"}]}
    strong = {"segments": [{"words": [{"start": 1.0, "end": 2.0, "word": "dialogue"}]}]}
    aligned = align_recovered_envelopes(recovered, strong)
    assert aligned["segments"][0]["start"] == 1.0
    assert aligned["segments"][0]["end"] == 2.0


def test_strong_word_alignment_never_crosses_adjacent_cue_cores():
    recovered = {"segments": [
        {"start": 1.0, "end": 1.4, "text": "one"},
        {"start": 1.6, "end": 2.0, "text": "two"},
    ]}
    strong = {"segments": [{"words": [
        {"start": 1.2, "end": 1.8, "word": "boundary"},
    ]}]}
    aligned = align_recovered_envelopes(recovered, strong)
    assert aligned["segments"][0]["end"] <= recovered["segments"][1]["start"]
    assert aligned["segments"][0]["end"] > aligned["segments"][0]["start"]


def test_overlapping_adjacent_core_cannot_invert_aligned_source_cue():
    recovered = {"segments": [
        {"start": 0.0, "end": 2.0, "text": "one"},
        {"start": 1.0, "end": 1.5, "text": "two"},
    ]}
    strong = {"segments": [{"words": [{"start": 0.5, "end": 1.2, "word": "word"}]}]}
    aligned = align_recovered_envelopes(recovered, strong)
    assert all(item["end"] > item["start"] for item in aligned["segments"])


def test_nested_wordless_recovery_cue_merges_into_aligned_group():
    source = {
        "language": "zh", "task": "transcribe", "output_language": "zh",
        "segments": [
            {"start": 0.0, "end": 2.0, "text": "aligned", "words": [{"start": 0.0, "end": 2.0, "word": "aligned"}]},
            {"start": 0.8, "end": 1.2, "text": "nested", "words": []},
        ],
    }
    clean = build_clean_transcript(source)
    assert len(clean["segments"]) == 1
    assert clean["segments"][0]["start"] == 0.0
    assert clean["segments"][0]["end"] == 2.0


def test_empty_contextual_output_uses_direct_translation_fallback():
    backend = FallbackContextTranslator(lambda request: "", lambda request: "fallback target")
    clean = build_clean_transcript({
        "language": "en", "task": "transcribe", "output_language": "en",
        "segments": [{"start": 0.0, "end": 1.0, "text": "Synthetic.", "speaker": "one"}],
    })
    translated = translate_contextual(clean, "en", "primary", backend)
    assert all(item["translated_text"] == "fallback target" for item in translated["segments"])
    assert len(backend.events) == len(clean["segments"])
    assert backend.events[0]["reason"] == "empty-primary-output"


def test_verbose_or_context_leaking_translation_uses_fallback():
    clean = build_clean_transcript({
        "language": "ja", "task": "transcribe", "output_language": "ja",
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "source one", "speaker": "one"},
            {"start": 2.0, "end": 3.0, "text": "source two", "speaker": "one"},
        ],
    })
    request = translation_request(clean["segments"], 0, "ja", "en")
    assert not valid_translation_response("Here's the translation:\nTarget", request)
    assert not valid_translation_response("Target source two", request)
    backend = FallbackContextTranslator(
        lambda item: "Here's the translation:\nTarget", lambda item: "Clean target"
    )
    assert backend(request) == "Clean target"
    assert backend.events[0]["reason"] == "invalid-primary-output-contract"


def test_refusal_and_untranslated_target_script_use_direct_fallback():
    clean = build_clean_transcript({
        "language": "ko", "task": "transcribe", "output_language": "ko",
        "segments": [{"start": 0.0, "end": 1.0, "text": "안녕하세요", "speaker": "one"}],
    })
    request = translation_request(clean["segments"], 0, "ko", "en")
    assert not valid_translation_response("I can't provide that translation.", request)
    assert not valid_translation_response("어디에나 소소하다고 합니다.", request)
