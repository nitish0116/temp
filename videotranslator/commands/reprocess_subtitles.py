"""Incrementally reprocess existing subtitle artifacts and compare quality metrics."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from .auto_prepare_script import translated_document
    from .canonical_timed_text import adapt_legacy_transcript
    from .export_subtitles import export_subtitles
    from .map_translation_cues import map_translated_groups
    from .qa_transcript import analyze
    from .repair_subtitles import iterative_repair
except ImportError:
    from auto_prepare_script import translated_document
    from canonical_timed_text import adapt_legacy_transcript
    from export_subtitles import export_subtitles
    from map_translation_cues import map_translated_groups
    from qa_transcript import analyze
    from repair_subtitles import iterative_repair


def metric_snapshot(report: dict) -> dict:
    """Return stable metrics used for before/after comparisons."""
    source = report.get("source_coverage") or {}
    diarized = report.get("diarized_coverage") or {}
    counts = report.get("issue_counts") or {}
    return {
        "segment_count": report.get("segment_count", 0),
        "source_event_coverage": source.get("source_event_coverage"),
        "source_time_coverage": source.get("source_time_coverage"),
        "diarized_turn_coverage": diarized.get("turn_coverage"),
        "diarized_time_coverage": diarized.get("time_coverage"),
        "short_duration_count": counts.get("short_duration", 0),
        "fast_reading_speed_count": counts.get("fast_reading_speed", 0),
        "long_duration_count": counts.get("long_duration", 0),
        "maximum_characters_per_second": report.get("maximum_observed_characters_per_second", 0),
    }


def metric_comparison(before: dict | None, after: dict) -> dict:
    """Compare numeric metrics without assigning direction-dependent semantics."""
    after_snapshot = metric_snapshot(after)
    if not before:
        return {"before": None, "after": after_snapshot, "delta": {}}
    baseline = before.get("episode_baseline", before)
    delta = {
        key: round(value - baseline[key], 6)
        for key, value in after_snapshot.items()
        if isinstance(value, (int, float)) and isinstance(baseline.get(key), (int, float))
    }
    return {"before": baseline, "after": after_snapshot, "delta": delta}


def upstream_recommendations(qa: dict) -> list[dict]:
    """Map unresolved evidence to the cheapest justified upstream rerun."""
    counts = qa.get("issue_counts", {})
    recommendations = []
    if counts.get("fast_reading_speed") or counts.get("short_duration"):
        recommendations.append({
            "stage": "contextual-translation-and-display-mapping",
            "reason": "target cues still fail reading-speed or minimum-duration QA",
        })
    if counts.get("missing_diarized_turns") or counts.get("missing_diarized_time"):
        recommendations.append({
            "stage": "diarization-reconciliation",
            "reason": "independent speaker turns remain uncovered",
        })
    if counts.get("missing_source_events") or counts.get("missing_source_time"):
        recommendations.append({
            "stage": "speech-recovery",
            "reason": "canonical cues do not cover enough source speech evidence",
        })
    return recommendations


def reprocess_existing(
    source: dict,
    translated: dict,
    output: Path,
    *,
    diarization: dict | None = None,
    baseline: dict | None = None,
    maximum_passes: int = 4,
) -> dict:
    """Reuse expensive artifacts and run only canonical mapping, repair, QA, export."""
    source_segments = source.get("segments", [])
    target_segments = translated.get("segments", [])
    if len(source_segments) != len(target_segments):
        raise ValueError("Source and translated artifacts must contain the same segment count")
    canonical_source = adapt_legacy_transcript(source)
    target_texts = [
        item.get("translated_text") or item.get("text") or ""
        for item in target_segments
    ]
    canonical_target = translated_document(
        canonical_source, target_texts,
        translated.get("output_language") or translated.get("language") or "en",
        translated.get("translation_model", "reused-translation-artifact"),
        source.get("language", "und"),
        translated.get("output_language") or translated.get("language") or "en",
    )
    mapped = map_translated_groups(canonical_target, maximum_characters=64)
    repaired, optimization = iterative_repair(mapped, maximum_passes=maximum_passes)
    qa = analyze(
        repaired, 12.0, source_transcript=source,
        diarization_report=diarization,
    )
    status = "passed" if qa["passed"] else "rejected"
    output.mkdir(parents=True, exist_ok=True)
    canonical_path = output / "canonical-subtitles.json"
    qa_path = output / "qa.json"
    report_path = output / "incremental-report.json"
    canonical_path.write_text(json.dumps(repaired, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    export = export_subtitles(
        repaired, output / f"{status}.srt", output / f"{status}.ass"
    )
    result = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "reused_stages": ["audio", "transcription", "alignment", "diarization", "speech-recovery", "translation"],
        "executed_stages": ["canonical-migration", "display-mapping", "iterative-repair", "qa", "export"],
        "comparison": metric_comparison(baseline, qa),
        "upstream_recommendations": upstream_recommendations(qa),
        "optimization": optimization,
        "qa": qa,
        "export": export,
    }
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("translated", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--diarization", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--maximum-passes", type=int, default=4)
    args = parser.parse_args()
    load = lambda path: json.loads(path.read_text(encoding="utf-8"))
    result = reprocess_existing(
        load(args.source), load(args.translated), args.output,
        diarization=load(args.diarization) if args.diarization else None,
        baseline=load(args.baseline) if args.baseline else None,
        maximum_passes=args.maximum_passes,
    )
    print(json.dumps({"status": result["status"], "comparison": result["comparison"], "upstream_recommendations": result["upstream_recommendations"]}, indent=2))
    if result["status"] != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
