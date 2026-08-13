"""Run canonical semantic translation, reconciliation, optimization, QA, and export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

try:
    from .build_clean_transcript import build_clean_transcript
    from .diarize_pyannote import reconcile_unmatched_turns
    from .export_subtitles import export_subtitles
    from .map_translation_cues import map_translated_groups
    from .qa_transcript import analyze
    from .qa_translation_integrity import enforce_translation_integrity
    from .repair_subtitles import iterative_repair
    from .translate_contextual import TranslationRequest, translate_contextual
except ImportError:
    from build_clean_transcript import build_clean_transcript
    from diarize_pyannote import reconcile_unmatched_turns
    from export_subtitles import export_subtitles
    from map_translation_cues import map_translated_groups
    from qa_transcript import analyze
    from qa_translation_integrity import enforce_translation_integrity
    from repair_subtitles import iterative_repair
    from translate_contextual import TranslationRequest, translate_contextual


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
) -> dict:
    """Execute Steps 4–15 for one recovered-source candidate."""
    turns = stable_diarization_turns(diarization_report)
    source = {**recovered_source, "segments": assign_missing_speakers(recovered_source["segments"], turns)}
    clean = build_clean_transcript(source)
    translated = translate_contextual(
        clean, target_language, model_name, translate_one,
        context_size=context_size, cache_directory=output / "translation-cache",
    )

    def retry(text: str, context: dict) -> str:
        request = TranslationRequest(
            group_id=f"integrity-{context.get('route', 'retry')}-{context.get('attempt', context.get('piece', 1))}",
            source_language=clean["source_language"], target_language=target_language,
            current_text=text, previous=(), following=(),
        )
        return translate_one(request)

    integrity, integrity_report = enforce_translation_integrity(
        translated, retry, maximum_retries=maximum_retries,
    )
    mapped = map_translated_groups(integrity, maximum_characters=64)
    reconciled_segments, reconciliation = reconcile_unmatched_turns(
        mapped["segments"], turns, turns,
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
    status = "passed" if qa["passed"] and integrity_report["passed"] else "rejected"
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "clean": output / "clean-transcript.json",
        "translated": output / "contextual-translation.json",
        "integrity": output / "translation-integrity.json",
        "canonical": output / "canonical-subtitles.json",
        "qa": output / "qa.json",
        "report": output / "canonical-pipeline-report.json",
    }
    for key, value in (("clean", clean), ("translated", translated), ("integrity", integrity_report), ("canonical", repaired), ("qa", qa)):
        artifacts[key].write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    export = export_subtitles(repaired, output / f"{status}.srt", output / f"{status}.ass")
    result = {
        "schema_version": 1, "status": status,
        "translation_model": model_name, "context_size": context_size,
        "translation_integrity": integrity_report,
        "diarization_reconciliation": reconciliation,
        "optimization": optimization, "qa": qa, "export": export,
        "artifacts": {key: str(path.resolve()) for key, path in artifacts.items()},
    }
    artifacts["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
