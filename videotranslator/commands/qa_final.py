"""Automatically validate and, when safe, normalize a final dubbed video."""

from __future__ import annotations


import argparse
import json
import math
import os
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import soundfile


def probe_media(path: Path) -> dict[str, Any]:
    """Return FFprobe stream and duration metadata for a media file."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=index,codec_type,codec_name,duration", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def audio_levels(path: Path) -> tuple[float, float]:
    """Measure mean and peak volume in dB with FFmpeg's volume detector."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "NUL" if os.name == "nt" else "/dev/null"],
        capture_output=True,
        text=True,
    )
    output = result.stderr + result.stdout
    mean_match = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", output)
    peak_match = re.search(r"max_volume:\s*(-?[0-9.]+) dB", output)
    if not mean_match or not peak_match:
        raise RuntimeError("FFmpeg did not report audio levels")
    return float(mean_match.group(1)), float(peak_match.group(1))


def stem_leakage(vocals: Path, accompaniment: Path, blocksize: int = 65536) -> float:
    """Estimate vocal residue using normalized cross-correlation of Demucs stems."""
    dot = vocal_energy = background_energy = 0.0
    with soundfile.SoundFile(vocals) as vocal_file, soundfile.SoundFile(accompaniment) as background_file:
        while True:
            vocal_block = vocal_file.read(blocksize, dtype="float32", always_2d=True)
            background_block = background_file.read(blocksize, dtype="float32", always_2d=True)
            length = min(len(vocal_block), len(background_block))
            if not length:
                break
            vocal_block = vocal_block[:length]
            background_block = background_block[:length]
            dot += float((vocal_block * background_block).sum())
            vocal_energy += float((vocal_block * vocal_block).sum())
            background_energy += float((background_block * background_block).sum())
    denominator = math.sqrt(vocal_energy * background_energy)
    return abs(dot / denominator) if denominator else 0.0


def normalize_mix(video: Path, gain_db: float) -> None:
    """Apply a bounded whole-program gain correction while preserving other streams."""
    temporary = video.with_name(f"{video.stem}.normalizing{video.suffix}")
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-map", "0", "-c:v", "copy", "-c:s", "copy", "-af", f"volume={gain_db:.2f}dB", "-c:a", "aac", "-b:a", "192k", str(temporary)],
        check=True,
    )
    temporary.replace(video)


def evaluate(
    video: Path,
    dub_manifest_path: Path,
    assigned_script_path: Path,
    assembly_report_path: Path,
    vocals: Path,
    accompaniment: Path,
    maximum_tempo: float = 3.0,
    maximum_leakage: float = 0.35,
    auto_normalize: bool = True,
) -> dict[str, Any]:
    """Run deterministic final checks and return a machine-readable decision."""
    dub = json.loads(dub_manifest_path.read_text(encoding="utf-8"))
    assigned = json.loads(assigned_script_path.read_text(encoding="utf-8"))
    assembly = json.loads(assembly_report_path.read_text(encoding="utf-8"))
    clips = dub.get("clips", [])
    issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    missing = [clip["segment_id"] for clip in clips if clip.get("status") == "failed" or not Path(clip["audio_path"]).is_file()]
    if missing:
        issues.append({"code": "missing_clips", "detail": f"{len(missing)} clips failed or are missing"})
    overruns = []
    extreme = []
    for index, clip in enumerate(clips):
        next_start = float(clips[index + 1]["start"]) if index + 1 < len(clips) else float(clip["end"])
        window = max(float(clip["end"]), next_start) - float(clip["start"])
        required_tempo = float(clip["generated_duration"]) / window if window > 0 else math.inf
        if required_tempo > 1:
            overruns.append(clip["segment_id"])
        if required_tempo > maximum_tempo:
            extreme.append(clip["segment_id"])
    if extreme:
        issues.append({"code": "extreme_tempo", "detail": f"{len(extreme)} clips require tempo above {maximum_tempo:g}x"})
    elif overruns:
        warnings.append({"code": "tempo_fitting", "detail": f"{len(overruns)} clips were accelerated to fit their cues"})

    speaker_voices: dict[str, set[str]] = {}
    for segment in assigned.get("segments", []):
        speaker_voices.setdefault(segment.get("speaker", "unknown"), set()).add(segment.get("voice", ""))
    inconsistent = [speaker for speaker, voices in speaker_voices.items() if len(voices) != 1]
    style_counts = Counter(segment.get("voice_style", "unknown") for segment in assigned.get("segments", []))
    if inconsistent:
        issues.append({"code": "voice_inconsistency", "detail": f"{len(inconsistent)} speakers use multiple voices"})

    probe = probe_media(video)
    stream_types = [stream["codec_type"] for stream in probe.get("streams", [])]
    for required in ("video", "audio", "subtitle"):
        if required not in stream_types:
            issues.append({"code": f"missing_{required}_stream", "detail": f"Final file has no {required} stream"})
    video_duration = float(probe["format"]["duration"])
    source_probe = probe_media(Path(assembly["input_video"]))
    source_video_durations = [float(s["duration"]) for s in source_probe["streams"] if s["codec_type"] == "video" and s.get("duration")]
    source_duration = source_video_durations[0] if source_video_durations else float(source_probe["format"]["duration"])
    duration_delta = abs(video_duration - source_duration)
    if duration_delta > 0.25:
        issues.append({"code": "duration_mismatch", "detail": f"Final duration differs by {duration_delta:.3f}s"})

    mean_db, peak_db = audio_levels(video)
    correction_db = 0.0
    if auto_normalize and (peak_db > -0.1 or mean_db < -32 or mean_db > -14):
        correction_db = min(-1.0 - peak_db, -24.0 - mean_db)
        correction_db = max(-12.0, min(12.0, correction_db))
        normalize_mix(video, correction_db)
        mean_db, peak_db = audio_levels(video)
    if peak_db > -0.1:
        issues.append({"code": "audio_clipping", "detail": f"Peak level is {peak_db:.1f} dB"})
    if not -32 <= mean_db <= -14:
        issues.append({"code": "loudness_out_of_range", "detail": f"Mean level is {mean_db:.1f} dB"})

    leakage = stem_leakage(vocals, accompaniment)
    if leakage > maximum_leakage:
        warnings.append({"code": "possible_vocal_residue", "detail": f"Stem correlation {leakage:.3f} exceeds {maximum_leakage:.3f}"})

    return {
        "schema_version": 1,
        "project_id": dub["project_id"],
        "status": "passed" if not issues else "failed",
        "automatic": True,
        "checks": {
            "clip_count": len(clips),
            "missing_clip_count": len(missing),
            "accelerated_clip_count": len(overruns),
            "extreme_tempo_clip_count": len(extreme),
            "speaker_count": len(speaker_voices),
            "voice_style_counts": dict(style_counts),
            "voice_consistent": not inconsistent,
            "stream_types": stream_types,
            "duration_seconds": video_duration,
            "duration_delta_seconds": duration_delta,
            "mean_volume_db": mean_db,
            "peak_volume_db": peak_db,
            "applied_gain_correction_db": correction_db,
            "stem_leakage_correlation": leakage,
        },
        "issues": issues,
        "warnings": warnings,
    }


def main() -> None:
    """Parse paths, run QA, persist its report, and fail the stage on rejection."""
    parser = argparse.ArgumentParser(description="Automatically quality-check a final dub.")
    parser.add_argument("video", type=Path)
    parser.add_argument("dub_manifest", type=Path)
    parser.add_argument("assigned_script", type=Path)
    parser.add_argument("assembly_report", type=Path)
    parser.add_argument("vocals", type=Path)
    parser.add_argument("accompaniment", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--maximum-tempo", type=float, default=3.0)
    parser.add_argument("--maximum-leakage", type=float, default=0.35)
    parser.add_argument("--no-auto-normalize", action="store_true")
    args = parser.parse_args()
    report = evaluate(args.video, args.dub_manifest, args.assigned_script, args.assembly_report, args.vocals, args.accompaniment, args.maximum_tempo, args.maximum_leakage, not args.no_auto_normalize)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Final QA: {report['status']}")
    print(f"Report: {args.output.resolve()}")
    if report["status"] != "passed":
        raise RuntimeError(f"Final dub failed {len(report['issues'])} quality checks")


if __name__ == "__main__":
    main()
