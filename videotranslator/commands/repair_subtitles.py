"""Repair subtitle readability without moving cues across neighboring dialogue."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

try:
    from .transcribe import srt_timestamp
except ImportError:
    from transcribe import srt_timestamp


def text_chunks(text: str, maximum_characters: int = 84) -> list[str]:
    """Split text at sentence or word boundaries under a hard cue limit."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    if re.search(r"[,;:]$", cleaned):
        cleaned = cleaned[:-1].rstrip() + "."
    if len(cleaned) <= maximum_characters:
        return [cleaned]
    units = [unit.strip() for unit in re.split(r"(?<=[.!?])\s+", cleaned) if unit.strip()]
    chunks: list[str] = []
    for unit in units:
        words = unit.split()
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if current and len(candidate) > maximum_characters:
                chunks.append(current)
                current = word
            else:
                current = candidate
        if current:
            if chunks and len(chunks[-1]) + 1 + len(current) <= maximum_characters:
                chunks[-1] += " " + current
            else:
                chunks.append(current)
    result = chunks or [cleaned]
    return [re.sub(r"[,;:\u060c\u061b\uff0c\uff1b\uff1a]$", ".", chunk) for chunk in result]


def split_cue(cue: dict, maximum_characters: int) -> list[dict]:
    """Partition one cue's time proportionally across readable text chunks."""
    chunks = text_chunks(str(cue["text"]), maximum_characters)
    if len(chunks) == 1:
        return [{**cue, "text": chunks[0]}]
    start, end = float(cue["start"]), float(cue["end"])
    weights = [max(1, len(re.sub(r"\s+", "", chunk))) for chunk in chunks]
    total = sum(weights)
    cursor = start
    output = []
    for index, (chunk, weight) in enumerate(zip(chunks, weights)):
        chunk_end = end if index == len(chunks) - 1 else cursor + (end - start) * weight / total
        output.append({**cue, "start": round(cursor, 3), "end": round(chunk_end, 3), "text": chunk})
        cursor = chunk_end
    return output


def repair(
    transcript: dict,
    minimum_duration: float = 0.5,
    maximum_characters: int = 84,
    maximum_characters_per_second: float = 20.0,
) -> dict:
    """Split long text and borrow only neighboring silence for readability."""
    cues = [part for cue in transcript.get("segments", []) for part in split_cue(cue, maximum_characters)]
    cues.sort(key=lambda item: (float(item["start"]), float(item["end"])))
    for index, cue in enumerate(cues):
        characters = len(re.sub(r"\s+", "", str(cue["text"])))
        required = max(minimum_duration, characters / maximum_characters_per_second)
        previous_end = float(cues[index - 1]["end"]) if index else 0.0
        next_start = float(cues[index + 1]["start"]) if index + 1 < len(cues) else math.inf
        start, end = float(cue["start"]), float(cue["end"])
        end = min(next_start, max(end, start + required))
        if end - start < required:
            start = max(previous_end, end - required)
        cue["start"], cue["end"] = round(start, 3), round(end, 3)
    return {**transcript, "segments": cues, "readability_repaired": True}


def write_srt(path: Path, segments: list[dict]) -> None:
    """Write repaired cues as UTF-8 SRT."""
    content = "\n\n".join(
        f"{index}\n{srt_timestamp(item['start'])} --> {srt_timestamp(item['end'])}\n{item['text']}"
        for index, item in enumerate(segments, 1)
    )
    path.write_text(content + "\n", encoding="utf-8")


def main() -> None:
    """Repair a transcript JSON and emit repaired JSON plus SRT."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-srt", type=Path, required=True)
    parser.add_argument("--minimum-duration", type=float, default=0.5)
    parser.add_argument("--maximum-characters", type=int, default=84)
    parser.add_argument("--maximum-characters-per-second", type=float, default=20.0)
    args = parser.parse_args()
    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    repaired = repair(transcript, args.minimum_duration, args.maximum_characters, args.maximum_characters_per_second)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_srt.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(repaired, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_srt(args.output_srt, repaired["segments"])
    print(f"Repaired {len(repaired['segments'])} subtitle cues")


if __name__ == "__main__":
    main()
