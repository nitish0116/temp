"""Assign persistent speakers with pyannote's dedicated diarization pipeline."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import librosa
import torch


DEFAULT_MODEL = "pyannote/speaker-diarization-community-1"


def overlap_seconds(start: float, end: float, turn: dict) -> float:
    """Return temporal overlap between a transcript cue and diarization turn."""
    return max(0.0, min(end, float(turn["end"])) - max(start, float(turn["start"])))


def assign_turns(segments: list[dict], turns: list[dict]) -> tuple[list[dict], dict]:
    """Assign the maximum-overlap persistent speaker to each transcript cue.

    Exclusive pyannote turns ensure only one speaker wins at any instant. A cue
    with no overlap receives the temporally nearest speaker and is marked as a
    fallback so QA can reject excessive uncertain assignments later.
    """
    if not turns:
        raise ValueError("Diarization returned no speaker turns")
    ordered_labels = []
    for turn in sorted(turns, key=lambda item: (item["start"], item["end"])):
        if turn["speaker"] not in ordered_labels:
            ordered_labels.append(turn["speaker"])
    stable = {label: f"speaker-{index:02d}" for index, label in enumerate(ordered_labels, start=1)}
    updated = json.loads(json.dumps(segments))
    fallback_count = 0
    speaker_counts: dict[str, int] = {}
    for segment in updated:
        start, end = float(segment["start"]), float(segment["end"])
        scored = [(overlap_seconds(start, end, turn), turn) for turn in turns]
        overlap, selected = max(scored, key=lambda item: item[0])
        method = "maximum-overlap"
        if overlap <= 0:
            midpoint = (start + end) / 2
            selected = min(
                turns,
                key=lambda turn: min(abs(midpoint - float(turn["start"])), abs(midpoint - float(turn["end"]))),
            )
            method = "nearest-turn-fallback"
            fallback_count += 1
        speaker = stable[selected["speaker"]]
        segment["speaker"] = speaker
        segment["speaker_assignment"] = {
            "method": method,
            "overlap_seconds": round(overlap, 4),
            "source_label": selected["speaker"],
        }
        speaker_counts[speaker] = speaker_counts.get(speaker, 0) + 1
    return updated, {
        "speaker_count": len(stable),
        "speaker_segment_counts": speaker_counts,
        "fallback_assignment_count": fallback_count,
    }


def diarize(
    transcript: dict,
    audio_path: Path,
    token: str,
    model_name: str = DEFAULT_MODEL,
    minimum_speakers: int | None = None,
    maximum_speakers: int | None = None,
) -> tuple[dict, dict]:
    """Run local pyannote diarization and assign its exclusive turns to cues."""
    try:
        from pyannote.audio import Pipeline
    except ImportError as error:
        raise RuntimeError(
            "pyannote.audio is not installed; install requirements/diarization.txt"
        ) from error
    waveform, sample_rate = librosa.load(audio_path, sr=16_000, mono=True)
    pipeline = Pipeline.from_pretrained(model_name, token=token)
    parameters: dict[str, int] = {}
    if minimum_speakers is not None:
        parameters["min_speakers"] = minimum_speakers
    if maximum_speakers is not None:
        parameters["max_speakers"] = maximum_speakers
    output = pipeline(
        {"waveform": torch.from_numpy(waveform).unsqueeze(0), "sample_rate": sample_rate},
        **parameters,
    )
    annotation = getattr(output, "exclusive_speaker_diarization", None)
    if annotation is None:
        annotation = output.speaker_diarization
    turns = [
        {"start": round(turn.start, 4), "end": round(turn.end, 4), "speaker": speaker}
        for turn, _track, speaker in annotation.itertracks(yield_label=True)
    ]
    segments, assignment = assign_turns(transcript["segments"], turns)
    updated = {**transcript, "segments": segments}
    report = {
        "schema_version": 1,
        "method": "pyannote-community-1-exclusive",
        "model": model_name,
        "audio": str(audio_path.resolve()),
        "turn_count": len(turns),
        "turns": turns,
        **assignment,
    }
    return updated, report


def main() -> None:
    """Load credentials, run diarization, and write separate comparison artifacts."""
    parser = argparse.ArgumentParser(description="Run dedicated pyannote speaker diarization.")
    parser.add_argument("transcript", type=Path)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--minimum-speakers", type=int)
    parser.add_argument("--maximum-speakers", type=int)
    parser.add_argument("--output-script", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError(
            "HF_TOKEN is not configured. Accept the community-1 model terms and set a read token."
        )
    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    assigned, report = diarize(
        transcript, args.audio, token, args.model, args.minimum_speakers, args.maximum_speakers
    )
    args.output_script.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_script.write_text(json.dumps(assigned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Detected {report['speaker_count']} speakers across {report['turn_count']} turns")
    print(f"Nearest-turn fallbacks: {report['fallback_assignment_count']}")


if __name__ == "__main__":
    main()
