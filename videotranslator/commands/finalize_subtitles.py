"""Rebuild translated transcript cues from word timestamps and write final SRT."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .force_align import split_aligned_words
    from .transcribe import srt_timestamp
except ImportError:
    from force_align import split_aligned_words
    from transcribe import srt_timestamp


def speech_window(segment: dict) -> tuple[float, float]:
    """Return the acoustic word envelope, falling back to the rough cue window."""
    words = [word for word in segment.get("words", []) if word.get("start") is not None and word.get("end") is not None]
    if words:
        return float(words[0]["start"]), float(words[-1]["end"])
    return float(segment["start"]), float(segment["end"])


def finalize(
    transcript: dict,
    maximum_duration: float,
    maximum_characters: int,
    reference: dict | None = None,
) -> dict:
    """Return segmented cues expanded across their containing speech windows."""
    cues = []
    segments = transcript.get("segments", [])
    references = reference.get("segments", []) if reference else []
    indexed_reference = references if len(references) == len(segments) else []
    for segment_index, segment in enumerate(segments):
        local = split_aligned_words([segment], maximum_duration, maximum_characters)
        if not local:
            continue
        window_start, window_end = speech_window(
            indexed_reference[segment_index] if indexed_reference else segment
        )
        boundaries = [
            (float(left["end"]) + float(right["start"])) / 2
            for left, right in zip(local, local[1:])
        ]
        for index, cue in enumerate(local):
            cue["start"] = round(window_start if index == 0 else boundaries[index - 1], 3)
            cue["end"] = round(window_end if index == len(local) - 1 else boundaries[index], 3)
            cues.append(cue)
    cues.sort(key=lambda item: (item["start"], item["end"]))
    return {**transcript, "segments": cues}


def write_srt(path: Path, segments: list[dict]) -> None:
    """Write canonical subtitle cues in UTF-8 SRT format."""
    content = "\n\n".join(
        f"{index}\n{srt_timestamp(item['start'])} --> {srt_timestamp(item['end'])}\n{item['text']}"
        for index, item in enumerate(segments, start=1)
    )
    path.write_text(content + "\n", encoding="utf-8")


def main() -> None:
    """Parse paths, segment translated words, and write JSON plus SRT."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-srt", type=Path, required=True)
    parser.add_argument("--maximum-duration", type=float, default=8.0)
    parser.add_argument("--maximum-characters", type=int, default=84)
    args = parser.parse_args()
    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    reference = json.loads(args.reference.read_text(encoding="utf-8")) if args.reference else None
    finalized = finalize(transcript, args.maximum_duration, args.maximum_characters, reference)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_srt.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(finalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_srt(args.output_srt, finalized["segments"])
    print(f"Finalized {len(finalized['segments'])} subtitle cues")


if __name__ == "__main__":
    main()
