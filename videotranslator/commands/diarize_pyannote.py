"""Assign persistent speakers with pyannote's dedicated diarization pipeline."""

from __future__ import annotations


import argparse
import json
import os
from pathlib import Path
from typing import Any

import librosa
import torch

try:
    from .segment_utterances import join_words, segment_words
except ImportError:  # Direct script execution.
    from segment_utterances import join_words, segment_words
try:
    from .canonical_timed_text import append_provenance
    from .runtime_device import resolve_device
except ImportError:
    from canonical_timed_text import append_provenance
    from runtime_device import resolve_device


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
    expanded = []
    for segment in json.loads(json.dumps(segments)):
        timed_words = segment.get("words", [])
        if not timed_words:
            expanded.append(segment)
            continue
        annotated = []
        for word in timed_words:
            word_start, word_end = float(word["start"]), float(word["end"])
            overlap, selected = max(
                ((overlap_seconds(word_start, word_end, turn), turn) for turn in turns),
                key=lambda item: item[0],
            )
            if overlap <= 0:
                midpoint = (word_start + word_end) / 2
                selected = min(
                    turns,
                    key=lambda turn: min(
                        abs(midpoint - float(turn["start"])), abs(midpoint - float(turn["end"]))
                    ),
                )
            annotated.append({**word, "speaker": stable[selected["speaker"]]})
        for group in segment_words(annotated):
            canonical = "source_text" in segment and "text" not in segment
            expanded.append({
                **segment,
                "start": round(float(group[0]["start"]), 3),
                "end": round(float(group[-1]["end"]), 3),
                **({"source_text": join_words(group)} if canonical else {"text": join_words(group)}),
                "words": group,
                "speaker": group[0]["speaker"],
            })
    updated = expanded
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
        assignment = {
            "method": method,
            "overlap_seconds": round(overlap, 4),
            "source_label": selected["speaker"],
        }
        if "metadata" in segment and "source_text" in segment:
            segment["metadata"] = {**segment["metadata"], "speaker_assignment": assignment}
        else:
            segment["speaker_assignment"] = assignment
        segment["provenance"] = append_provenance(
            segment, "speaker-diarization", method,
            speaker=speaker, source_label=selected["speaker"],
            overlap_seconds=round(overlap, 4),
        )
        speaker_counts[speaker] = speaker_counts.get(speaker, 0) + 1
    return updated, {
        "speaker_count": len(stable),
        "speaker_segment_counts": speaker_counts,
        "fallback_assignment_count": fallback_count,
        "input_segment_count": len(segments),
        "utterance_segment_count": len(updated),
    }


def reconcile_unmatched_turns(
    segments: list[dict], turns: list[dict], speech_evidence: list[dict],
    maximum_gap: float = 0.35, maximum_turn_duration: float = 1.5,
) -> tuple[list[dict], list[dict]]:
    """Attach small supported turns to compatible neighboring speaker cues."""
    updated = json.loads(json.dumps(segments))
    decisions = []
    for turn in turns:
        start, end = float(turn["start"]), float(turn["end"])
        if end - start > maximum_turn_duration:
            decisions.append({**turn, "status": "retained-unmatched", "reason": "turn-too-long"})
            continue
        supported = any(overlap_seconds(start, end, item) > 0 for item in speech_evidence)
        if not supported:
            decisions.append({**turn, "status": "retained-unmatched", "reason": "no-speech-evidence"})
            continue
        candidates = []
        for index, cue in enumerate(updated):
            speaker = cue.get("speaker")
            if not speaker or speaker == "unknown" or speaker != turn.get("speaker"):
                continue
            overlap = overlap_seconds(start, end, cue)
            gap = 0.0 if overlap > 0 else min(abs(start - float(cue["end"])), abs(float(cue["start"]) - end))
            if gap <= maximum_gap:
                candidates.append((-overlap, gap, index, cue))
        if not candidates:
            decisions.append({**turn, "status": "retained-unmatched", "reason": "no-compatible-neighbor"})
            continue
        _overlap, _gap, index, cue = min(candidates, key=lambda item: (item[0], item[1]))
        old = [cue["start"], cue["end"]]
        previous_end = float(updated[index - 1]["end"]) if index else float("-inf")
        next_start = float(updated[index + 1]["start"]) if index + 1 < len(updated) else float("inf")
        proposed_start = min(float(cue["start"]), start)
        proposed_end = max(float(cue["end"]), end)
        cue["start"] = round(proposed_start if proposed_start >= previous_end else float(cue["start"]), 3)
        cue["end"] = round(proposed_end if proposed_end <= next_start else float(cue["end"]), 3)
        cue["provenance"] = append_provenance(
            cue, "speaker-diarization", "reconcile-supported-unmatched-turn",
            old_timing=old, turn=[start, end], speaker=turn["speaker"],
        )
        decisions.append({**turn, "status": "attached", "cue_id": cue.get("id")})
    return updated, decisions


def diarize(
    transcript: dict,
    audio_path: Path,
    token: str,
    model_name: str = DEFAULT_MODEL,
    minimum_speakers: int | None = None,
    maximum_speakers: int | None = None,
    device: str = "auto",
) -> tuple[dict, dict]:
    """Run local pyannote diarization and assign its exclusive turns to cues."""
    try:
        from pyannote.audio import Pipeline
    except ImportError as error:
        raise RuntimeError(
            "pyannote.audio is not installed; install the unified requirements.txt"
        ) from error
    waveform, sample_rate = librosa.load(audio_path, sr=16_000, mono=True)
    selected_device = resolve_device(device)
    pipeline = Pipeline.from_pretrained(model_name, token=token)
    pipeline.to(torch.device(selected_device))
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
        "device": selected_device,
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
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--output-script", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.get("HF_TOKEN")
    if not token:
        try:
            from huggingface_hub import get_token

            token = get_token()
        except ImportError:
            token = None
    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    assigned, report = diarize(
        transcript, args.audio, token, args.model, args.minimum_speakers,
        args.maximum_speakers, args.device
    )
    args.output_script.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_script.write_text(json.dumps(assigned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Detected {report['speaker_count']} speakers across {report['turn_count']} turns")
    print(f"Nearest-turn fallbacks: {report['fallback_assignment_count']}")


if __name__ == "__main__":
    main()
