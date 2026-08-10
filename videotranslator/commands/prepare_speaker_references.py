"""Extract clean vocal-stem reference clips for persistent XTTS speakers.

Example:
    python prepare_speaker_references.py vocals.wav diarization-report.json script.json -o references
"""

from __future__ import annotations


import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf


def source_to_persistent_speakers(script: dict) -> dict[str, str]:
    """Return the diarization-label to persistent-speaker mapping in the script."""
    mapping: dict[str, str] = {}
    for segment in script.get("segments", []):
        label = segment.get("speaker_assignment", {}).get("source_label")
        speaker = segment.get("speaker")
        if label and speaker:
            mapping.setdefault(str(label), str(speaker))
    return mapping


def turn_score(samples: np.ndarray) -> float:
    """Score a reference candidate by useful energy while penalizing clipping."""
    if not len(samples):
        return 0.0
    mono = samples.mean(axis=1) if samples.ndim > 1 else samples
    rms = float(np.sqrt(np.mean(np.square(mono))))
    clipping = float(np.mean(np.abs(mono) >= 0.98))
    return rms * max(0.0, 1.0 - clipping * 20.0)


def prepare_references(
    audio_path: Path,
    diarization: dict,
    script: dict,
    output_dir: Path,
    minimum_turn: float = 1.0,
    target_seconds: float = 12.0,
    maximum_clips: int = 6,
) -> dict:
    """Select and write high-energy, non-overlapping references for each speaker."""
    audio, sample_rate = sf.read(audio_path, always_2d=False)
    mapping = source_to_persistent_speakers(script)
    candidates: dict[str, list[dict]] = defaultdict(list)
    for turn in diarization.get("turns", []):
        duration = float(turn["end"]) - float(turn["start"])
        speaker = mapping.get(str(turn["speaker"]))
        if not speaker or duration < minimum_turn:
            continue
        start = max(0, round(float(turn["start"]) * sample_rate))
        end = min(len(audio), round(float(turn["end"]) * sample_rate))
        clip = audio[start:end]
        candidates[speaker].append({**turn, "samples": clip, "score": turn_score(clip)})

    output_dir.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": 1, "audio": str(audio_path.resolve()), "speakers": {}}
    for speaker, turns in sorted(candidates.items()):
        selected, seconds = [], 0.0
        for turn in sorted(turns, key=lambda item: item["score"], reverse=True):
            if len(selected) >= maximum_clips or seconds >= target_seconds:
                break
            path = output_dir / f"{speaker}-{len(selected) + 1:02d}.wav"
            sf.write(path, turn["samples"], sample_rate)
            duration = float(turn["end"]) - float(turn["start"])
            selected.append({
                "path": str(path.resolve()), "start": turn["start"], "end": turn["end"],
                "duration": round(duration, 4), "score": round(turn["score"], 6),
            })
            seconds += duration
        if selected:
            report["speakers"][speaker] = {"reference_seconds": round(seconds, 4), "clips": selected}
    (output_dir / "reference-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


def main() -> None:
    """Parse CLI arguments and create automatic per-speaker references."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("diarization", type=Path)
    parser.add_argument("script", type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path)
    args = parser.parse_args()
    report = prepare_references(
        args.audio,
        json.loads(args.diarization.read_text(encoding="utf-8")),
        json.loads(args.script.read_text(encoding="utf-8")),
        args.output,
    )
    print(json.dumps({key: value["reference_seconds"] for key, value in report["speakers"].items()}, indent=2))


if __name__ == "__main__":
    main()
