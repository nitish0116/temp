"""Reject incomplete, inconsistent, or excessively adjusted dubbing artifacts."""

from __future__ import annotations


import argparse
import json
from pathlib import Path
from typing import Any

try:
    from .synthesize_constrained import stable_segment_id
except ImportError:  # Direct script execution.
    from synthesize_constrained import stable_segment_id


def add_finding(findings: list[dict], code: str, detail: str, items: list[str] | None = None) -> None:
    """Append one structured blocking QA finding."""
    finding: dict[str, Any] = {"code": code, "detail": detail}
    if items:
        finding["items"] = items
    findings.append(finding)


def speech_coverage(script: dict, manifest: dict) -> tuple[float, list[str]]:
    """Return generated-audio coverage and missing canonical segment IDs."""
    clips = {clip["segment_id"]: clip for clip in manifest.get("clips", [])}
    expected = [stable_segment_id(segment, index) for index, segment in enumerate(script["segments"])]
    missing = []
    for segment_id in expected:
        clip = clips.get(segment_id)
        path = Path(clip["audio_path"]) if clip and clip.get("audio_path") else None
        if (
            clip is None
            or clip.get("status") == "failed"
            or float(clip.get("generated_duration", 0)) <= 0
            or path is None
            or not path.is_file()
            or path.stat().st_size == 0
        ):
            missing.append(segment_id)
    return ((len(expected) - len(missing)) / len(expected) if expected else 1.0), missing


def speaker_reassignments(script: dict, manifest: dict) -> tuple[dict[str, str], list[str]]:
    """Find speakers or clips whose persistent assigned voice changed."""
    clips = {clip["segment_id"]: clip for clip in manifest.get("clips", [])}
    expected_voices: dict[str, str] = {}
    inconsistent: set[str] = set()
    for index, segment in enumerate(script["segments"]):
        speaker = str(segment.get("speaker", "unknown"))
        voice = str(segment.get("voice", ""))
        previous = expected_voices.setdefault(speaker, voice)
        if previous != voice:
            inconsistent.add(speaker)
        clip = clips.get(stable_segment_id(segment, index))
        if clip and clip.get("voice") != voice:
            inconsistent.add(speaker)
    return expected_voices, sorted(inconsistent)


def maximum_native_tempo(synthesis_report: dict) -> tuple[float, list[str]]:
    """Return the largest native TTS rate factor and affected segment IDs."""
    factors = []
    for result in synthesis_report.get("segments", []):
        attempts = result.get("attempts", [])
        if not attempts or result.get("status") != "fits":
            continue
        scale = float(attempts[-1].get("length_scale", 1.0))
        factors.append((1 / scale if scale > 0 else float("inf"), result["segment_id"]))
    maximum = max((factor for factor, _ in factors), default=1.0)
    return maximum, [segment_id for factor, segment_id in factors if factor == maximum]


def dialogue_overlaps(manifest: dict, tolerance: float = 0.03) -> list[str]:
    """Return clips whose rendered audio overlaps the next dialogue onset."""
    clips = manifest.get("clips", [])
    overlaps = []
    for clip, following in zip(clips, clips[1:]):
        audio_end = float(clip["start"]) + float(clip["generated_duration"])
        if audio_end - float(following["start"]) > tolerance:
            overlaps.append(clip["segment_id"])
    return overlaps


def evidence_coverage(script: dict, strong_transcript: dict, diarization_report: dict) -> tuple[float, float]:
    """Measure canonical timing against independent ASR words and speech turns."""
    cues = [
        (float(segment["start"]), float(segment["end"]))
        for segment in script.get("segments", [])
    ]
    words = [
        word
        for segment in strong_transcript.get("segments", [])
        for word in segment.get("words", [])
    ]
    covered_words = 0
    for word in words:
        start, end = float(word["start"]), float(word["end"])
        overlap = sum(max(0.0, min(end, right) - max(start, left)) for left, right in cues)
        center = (start + end) / 2
        if overlap >= 0.03 or any(left - 0.1 <= center <= right + 0.1 for left, right in cues):
            covered_words += 1
    word_coverage = covered_words / len(words) if words else 1.0
    turns = [
        (float(turn["start"]), float(turn["end"]))
        for turn in diarization_report.get("turns", [])
        if float(turn["end"]) > float(turn["start"])
    ]
    turn_seconds = sum(end - start for start, end in turns)
    intersection = sum(
        max(0.0, min(end, right) - max(start, left))
        for start, end in turns
        for left, right in cues
    )
    diarized_coverage = min(1.0, intersection / turn_seconds) if turn_seconds else 1.0
    return word_coverage, diarized_coverage


def evaluate_pipeline(
    script: dict,
    translation_report: dict,
    manifest: dict,
    synthesis_report: dict,
    active_report: dict,
    minimum_coverage: float = 1.0,
    maximum_tempo: float = 1.2,
    maximum_onset_offset: float = 0.25,
    minimum_active_confidence: float = 0.5,
    strong_transcript: dict | None = None,
    diarization_report: dict | None = None,
    minimum_asr_word_coverage: float = 0.98,
    minimum_diarized_time_coverage: float = 0.7,
) -> dict:
    """Combine stage artifacts into one strict automatic pass/fail decision."""
    findings: list[dict] = []
    coverage, missing = speech_coverage(script, manifest)
    if coverage < minimum_coverage:
        add_finding(
            findings,
            "missing_speech_coverage",
            f"Speech coverage {coverage:.2%} is below {minimum_coverage:.2%}",
            missing,
        )
    expected_after_deduplication = int(translation_report.get("input_segment_count", len(script["segments"]))) - int(
        translation_report.get("deduplicated_segment_count", 0)
    )
    if expected_after_deduplication != len(script["segments"]):
        add_finding(
            findings,
            "translation_coverage_mismatch",
            "Translated cue count does not match input minus audited duplicates",
        )
    speaker_voices, reassigned = speaker_reassignments(script, manifest)
    if reassigned:
        add_finding(
            findings,
            "speaker_reassignment",
            f"{len(reassigned)} persistent speakers changed assigned voice",
            reassigned,
        )
    tempo, fastest = maximum_native_tempo(synthesis_report)
    if synthesis_report.get("post_processing_tempo_used") is not False:
        add_finding(findings, "post_processing_tempo", "Synthesis did not explicitly disable post-processing tempo")
    if tempo > maximum_tempo:
        add_finding(
            findings,
            "excessive_tempo",
            f"Maximum native TTS rate {tempo:.3f}x exceeds {maximum_tempo:.3f}x",
            fastest,
        )
    offsets = [
        (decision["segment_id"], abs(float(decision.get("onset_offset", 0))))
        for decision in active_report.get("decisions", [])
    ]
    excessive_offsets = [segment_id for segment_id, offset in offsets if offset > maximum_onset_offset + 1e-6]
    if excessive_offsets:
        add_finding(
            findings,
            "excessive_onset_offset",
            f"{len(excessive_offsets)} visual corrections exceed {maximum_onset_offset:.3f}s",
            excessive_offsets,
        )
    overlaps = dialogue_overlaps(manifest)
    if overlaps:
        add_finding(
            findings,
            "dialogue_overlap",
            f"{len(overlaps)} generated clips overlap the following dialogue",
            overlaps,
        )
    multi_face = int(active_report.get("multi_face_segment_count", 0))
    aligned = int(active_report.get("aligned_multi_face_segment_count", 0))
    active_confidence = aligned / multi_face if multi_face else 1.0
    if active_confidence < minimum_active_confidence:
        add_finding(
            findings,
            "active_speaker_confidence",
            f"Multi-face alignment coverage {active_confidence:.2%} is below {minimum_active_confidence:.2%}",
        )
    asr_word_coverage = diarized_time_coverage = None
    if strong_transcript is not None and diarization_report is not None:
        asr_word_coverage, diarized_time_coverage = evidence_coverage(
            script, strong_transcript, diarization_report
        )
        if asr_word_coverage < minimum_asr_word_coverage:
            add_finding(
                findings,
                "source_asr_coverage",
                f"Strong-ASR word coverage {asr_word_coverage:.2%} is below {minimum_asr_word_coverage:.2%}",
            )
        if diarized_time_coverage < minimum_diarized_time_coverage:
            add_finding(
                findings,
                "source_diarization_coverage",
                f"Diarized speech-time coverage {diarized_time_coverage:.2%} is below {minimum_diarized_time_coverage:.2%}",
            )
    checks = {
        "canonical_segment_count": len(script["segments"]),
        "speech_coverage": round(coverage, 6),
        "missing_segment_count": len(missing),
        "speaker_count": len(speaker_voices),
        "reassigned_speaker_count": len(reassigned),
        "maximum_native_tempo": round(tempo, 4),
        "post_processing_tempo_used": synthesis_report.get("post_processing_tempo_used"),
        "maximum_absolute_onset_offset": max((offset for _, offset in offsets), default=0.0),
        "dialogue_overlap_count": len(overlaps),
        "multi_face_segment_count": multi_face,
        "aligned_multi_face_segment_count": aligned,
        "active_speaker_confidence": round(active_confidence, 6),
        "source_asr_word_coverage": round(asr_word_coverage, 6) if asr_word_coverage is not None else None,
        "source_diarized_time_coverage": round(diarized_time_coverage, 6) if diarized_time_coverage is not None else None,
    }
    return {
        "schema_version": 1,
        "automatic": True,
        "status": "passed" if not findings else "failed",
        "thresholds": {
            "minimum_speech_coverage": minimum_coverage,
            "maximum_native_tempo": maximum_tempo,
            "maximum_onset_offset": maximum_onset_offset,
            "minimum_active_speaker_confidence": minimum_active_confidence,
            "minimum_source_asr_word_coverage": minimum_asr_word_coverage,
            "minimum_source_diarized_time_coverage": minimum_diarized_time_coverage,
        },
        "checks": checks,
        "findings": findings,
    }


def main() -> None:
    """Load pipeline artifacts, persist QA, and exit nonzero on rejection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script", type=Path)
    parser.add_argument("translation_report", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("synthesis_report", type=Path)
    parser.add_argument("active_speaker_report", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--minimum-coverage", type=float, default=1.0)
    parser.add_argument("--maximum-tempo", type=float, default=1.2)
    parser.add_argument("--maximum-onset-offset", type=float, default=0.25)
    parser.add_argument("--minimum-active-confidence", type=float, default=0.5)
    parser.add_argument("--strong-transcript", type=Path)
    parser.add_argument("--diarization-report", type=Path)
    parser.add_argument("--minimum-asr-word-coverage", type=float, default=0.98)
    parser.add_argument("--minimum-diarized-time-coverage", type=float, default=0.7)
    args = parser.parse_args()
    load = lambda path: json.loads(path.read_text(encoding="utf-8"))
    report = evaluate_pipeline(
        load(args.script),
        load(args.translation_report),
        load(args.manifest),
        load(args.synthesis_report),
        load(args.active_speaker_report),
        args.minimum_coverage,
        args.maximum_tempo,
        args.maximum_onset_offset,
        args.minimum_active_confidence,
        load(args.strong_transcript) if args.strong_transcript else None,
        load(args.diarization_report) if args.diarization_report else None,
        args.minimum_asr_word_coverage,
        args.minimum_diarized_time_coverage,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Dubbing pipeline QA: {report['status']}")
    print(json.dumps(report["checks"], indent=2))
    if report["status"] != "passed":
        raise RuntimeError(f"Dubbing pipeline failed {len(report['findings'])} QA checks")


if __name__ == "__main__":
    main()
