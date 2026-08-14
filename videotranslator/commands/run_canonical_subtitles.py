"""Run canonical semantic translation, reconciliation, optimization, QA, and export."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Callable

try:
    from .build_clean_transcript import build_clean_transcript
    from .canonical_timed_text import append_provenance
    from .diarize_pyannote import reconcile_unmatched_turns
    from .export_subtitles import export_subtitles
    from .map_translation_cues import map_translated_groups
    from .qa_transcript import analyze
    from .qa_translation_integrity import enforce_translation_integrity, integrity_issues
    from .qa_translation_agreement import enforce_translation_agreement
    from .repair_subtitles import iterative_repair
    from .translate_contextual import TranslationRequest, translate_cached_request, translate_contextual
except ImportError:
    from build_clean_transcript import build_clean_transcript
    from canonical_timed_text import append_provenance
    from diarize_pyannote import reconcile_unmatched_turns
    from export_subtitles import export_subtitles
    from map_translation_cues import map_translated_groups
    from qa_transcript import analyze
    from qa_translation_integrity import enforce_translation_integrity, integrity_issues
    from qa_translation_agreement import enforce_translation_agreement
    from repair_subtitles import iterative_repair
    from translate_contextual import TranslationRequest, translate_cached_request, translate_contextual


def stable_diarization_turns(report: dict) -> list[dict]:
    """Convert raw pyannote labels to the stable IDs used by assigned cues."""
    labels = []
    for turn in sorted(report.get("turns", []), key=lambda item: (item["start"], item["end"])):
        if turn["speaker"] not in labels:
            labels.append(turn["speaker"])
    stable = {label: f"speaker-{index:02d}" for index, label in enumerate(labels, start=1)}
    return [{**turn, "speaker": stable[turn["speaker"]], "source_label": turn["speaker"]} for turn in report.get("turns", [])]


def assign_missing_speakers(segments: list[dict], turns: list[dict]) -> list[dict]:
    """Assign the maximum-overlap stable speaker without changing cue boundaries."""
    output = json.loads(json.dumps(segments))
    for cue in output:
        if cue.get("speaker") and cue["speaker"] != "unknown":
            continue
        start, end = float(cue["start"]), float(cue["end"])
        scored = [
            (max(0.0, min(end, float(turn["end"])) - max(start, float(turn["start"]))), turn)
            for turn in turns
        ]
        if scored:
            overlap, selected = max(scored, key=lambda item: item[0])
            if overlap > 0:
                cue["speaker"] = selected["speaker"]
                cue.setdefault("metadata", {})["speaker_assignment"] = {
                    "method": "maximum-overlap-canonical-preparation",
                    "overlap_seconds": round(overlap, 4),
                    "source_label": selected.get("source_label"),
                }
    return output


def align_recovered_envelopes(recovered: dict, strong: dict, maximum_gap: float = 0.5) -> dict:
    """Expand recovered cues to nearby strong-ASR words without adding duplicate text."""
    output = json.loads(json.dumps(recovered))
    segments = output.get("segments", [])
    core_starts = [float(cue["start"]) for cue in segments]
    core_ends = [float(cue["end"]) for cue in segments]
    words = [
        word for segment in strong.get("segments", []) for word in segment.get("words", [])
        if word.get("start") is not None and word.get("end") is not None
    ]
    for word in words:
        start, end = float(word["start"]), float(word["end"])
        midpoint = (start + end) / 2
        candidates = []
        for index, cue in enumerate(segments):
            left, right = float(cue["start"]), float(cue["end"])
            distance = 0.0 if left <= midpoint <= right else min(abs(midpoint - left), abs(midpoint - right))
            if distance <= maximum_gap:
                candidates.append((distance, index))
        if candidates:
            _distance, index = min(candidates)
            cue = segments[index]
            lower = core_ends[index - 1] if index else float("-inf")
            upper = core_starts[index + 1] if index + 1 < len(segments) else float("inf")
            old_start, old_end = float(cue["start"]), float(cue["end"])
            proposed_start = max(lower, min(old_start, start))
            proposed_end = min(upper, max(old_end, end))
            cue["start"] = round(proposed_start if proposed_start < old_end else old_start, 3)
            cue["end"] = round(proposed_end if proposed_end > old_start else old_end, 3)
    return output


def compress_dense_translations(
    document: dict, translate_one: Callable[[TranslationRequest], str],
    maximum_characters_per_second: float = 20.0, minimum_duration: float = 0.5,
) -> tuple[dict, list[dict]]:
    """Shorten only over-budget groups while retaining semantic integrity."""
    output = json.loads(json.dumps(document))
    events = []
    for segment in output["segments"]:
        current = str(segment.get("translated_text") or "").strip()
        duration = float(segment["end"]) - float(segment["start"])
        budget = max(
            4, math.floor(minimum_duration * maximum_characters_per_second),
            math.floor(duration * maximum_characters_per_second),
        )
        if len(current) <= budget:
            continue
        source = str(segment.get("source_text") or "")
        request = TranslationRequest(
            group_id=f"compression-{segment['semantic_group_id']}",
            source_language=output["source_language"], target_language=output["output_language"],
            current_text=source, previous=(), following=(),
            required_numbers=tuple(re.findall(r"\d+(?:[.,]\d+)*", source)),
            maximum_characters=budget,
        )
        candidate = translate_one(request).strip()
        accepted = bool(candidate and len(candidate) <= budget and not integrity_issues(source, candidate))
        if accepted:
            segment["translated_text"] = candidate
            segment["provenance"] = append_provenance(
                segment, "readability-compression", "duration-aware-retranslation",
                previous_characters=len(current), new_characters=len(candidate), budget=budget,
            )
        events.append({
            "semantic_group_id": segment["semantic_group_id"], "budget": budget,
            "previous_characters": len(current), "candidate_characters": len(candidate),
            "accepted": accepted,
        })
    return output, events


def run_canonical_attempt(
    recovered_source: dict,
    strong_source: dict,
    diarization_report: dict,
    target_language: str,
    model_name: str,
    translate_one: Callable[[TranslationRequest], str],
    output: Path,
    *,
    context_size: int = 3,
    maximum_retries: int = 1,
    minimum_diarized_turn_coverage: float = 0.90,
    minimum_diarized_time_coverage: float = 0.90,
    independent_translate: Callable[[TranslationRequest], str] | None = None,
    semantic_similarity: Callable[[str, str], float] | None = None,
    independent_model: str | None = None,
    stronger_translate: Callable[[TranslationRequest], str] | None = None,
    stronger_model: str | None = None,
) -> dict:
    """Execute Steps 4–15 for one recovered-source candidate."""
    turns = stable_diarization_turns(diarization_report)
    recovered_source = align_recovered_envelopes(recovered_source, strong_source)
    source = {**recovered_source, "segments": assign_missing_speakers(recovered_source["segments"], turns)}
    clean = build_clean_transcript(source)
    prior_integrity_path = output / "translation-integrity.json"
    refresh_group_ids: set[str] = set()
    if prior_integrity_path.is_file():
        prior_integrity = json.loads(prior_integrity_path.read_text(encoding="utf-8"))
        refresh_group_ids = {
            str(item["semantic_group_id"])
            for item in prior_integrity.get("results", []) if not item.get("passed", False)
        }
    translated = translate_contextual(
        clean, target_language, model_name, translate_one,
        context_size=context_size, cache_directory=output / "translation-cache",
        refresh_group_ids=refresh_group_ids,
    )
    auxiliary_cache = output / "translation-cache"

    def translate_auxiliary(request: TranslationRequest) -> str:
        """Cache integrity and readability requests using their full contracts."""
        return translate_cached_request(
            request, translate_one, model_name, auxiliary_cache,
        )

    def retry(text: str, context: dict) -> str:
        """Adapt integrity-retry context into the contextual translator protocol.

        Example:: a number-mismatch retry adds the source numerals to
        ``required_numbers`` before invoking ``translate_one``.
        """
        request = TranslationRequest(
            group_id=f"integrity-{context.get('route', 'retry')}-{context.get('attempt', context.get('piece', 1))}",
            source_language=clean["source_language"], target_language=target_language,
            current_text=text, previous=(), following=(),
            required_numbers=tuple(re.findall(r"\d+(?:[.,]\d+)*", text)),
        )
        return translate_auxiliary(request)

    integrity, integrity_report = enforce_translation_integrity(
        translated, retry, maximum_retries=maximum_retries,
    )
    integrity, compression = compress_dense_translations(integrity, translate_auxiliary)
    integrity, integrity_report = enforce_translation_integrity(
        integrity, retry, maximum_retries=0,
    )
    agreement_report = {
        "schema_version": 1, "passed": True, "evaluated": False,
        "group_count": len(integrity["segments"]), "failed_group_count": 0,
    }
    if independent_translate is not None and semantic_similarity is not None:
        integrity, agreement_report = enforce_translation_agreement(
            integrity, independent_translate, semantic_similarity,
            independent_model=independent_model or "independent-translator",
            retry_translate=stronger_translate,
            retry_model=stronger_model,
            cache_directory=output / "agreement-cache",
        )
        agreement_report["evaluated"] = True
    mapped = map_translated_groups(integrity, maximum_characters=64)
    speech_evidence = [
        word for segment in strong_source.get("segments", []) for word in segment.get("words", [])
    ] or strong_source.get("segments", [])
    reconciled_segments, reconciliation = reconcile_unmatched_turns(
        mapped["segments"], turns, speech_evidence, maximum_gap=0.5,
        maximum_turn_duration=12.0,
    )
    mapped["segments"] = reconciled_segments
    repaired, optimization = iterative_repair(
        mapped, maximum_passes=4, maximum_characters=84,
        maximum_line_characters=42, maximum_characters_per_second=20.0,
    )
    qa = analyze(
        repaired, 12.0, source_transcript=strong_source,
        diarization_report=diarization_report,
        minimum_diarized_turn_coverage=minimum_diarized_turn_coverage,
        minimum_diarized_time_coverage=minimum_diarized_time_coverage,
    )
    status = "passed" if (
        qa["passed"] and integrity_report["passed"] and agreement_report["passed"]
    ) else "rejected"
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "clean": output / "clean-transcript.json",
        "translated": output / "contextual-translation.json",
        "integrity": output / "translation-integrity.json",
        "agreement": output / "translation-agreement.json",
        "canonical": output / "canonical-subtitles.json",
        "qa": output / "qa.json",
        "report": output / "canonical-pipeline-report.json",
    }
    for key, value in (("clean", clean), ("translated", translated), ("integrity", integrity_report), ("agreement", agreement_report), ("canonical", repaired), ("qa", qa)):
        artifacts[key].write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    export = export_subtitles(repaired, output / f"{status}.srt", output / f"{status}.ass")
    result = {
        "schema_version": 1, "status": status,
        "translation_model": model_name, "context_size": context_size,
        "translation_integrity": integrity_report,
        "translation_agreement": agreement_report,
        "diarization_reconciliation": reconciliation,
        "optimization": optimization, "qa": qa, "export": export,
        "readability_compression": compression,
        "artifacts": {key: str(path.resolve()) for key, path in artifacts.items()},
    }
    artifacts["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
