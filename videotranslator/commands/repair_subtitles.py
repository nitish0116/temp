"""Repair subtitle readability without moving cues across neighboring dialogue."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

try:
    from .canonical_timed_text import append_provenance
    from .transcribe import srt_timestamp
except ImportError:
    from canonical_timed_text import append_provenance
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


def _expand_chunks(chunks: list[str], minimum_count: int) -> list[str]:
    """Increase chunk count at balanced word/character boundaries without loss."""
    expanded = list(chunks)
    while len(expanded) < minimum_count:
        index = max(range(len(expanded)), key=lambda item: len(expanded[item]))
        value = expanded[index]
        words = value.split()
        if len(words) > 1:
            midpoint = len(words) // 2
            parts = [" ".join(words[:midpoint]), " ".join(words[midpoint:])]
        elif len(value) > 1:
            midpoint = len(value) // 2
            parts = [value[:midpoint], value[midpoint:]]
        else:
            break
        expanded[index:index + 1] = [part.strip() for part in parts if part.strip()]
    return expanded


def _bounded_timing_points(cue: dict, count: int, maximum_duration: float) -> list[float]:
    """Choose source pauses near proportional boundaries within hard duration bounds."""
    start, end = float(cue["start"]), float(cue["end"])
    words = sorted(cue.get("words", []), key=lambda word: float(word.get("start", 0)))
    pauses = [
        (float(left["end"]) + float(right["start"])) / 2
        for left, right in zip(words, words[1:])
        if float(right["start"]) - float(left["end"]) >= 0.12
    ]
    points = [start]
    for index in range(1, count):
        remaining = count - index
        ideal = start + (end - start) * index / count
        lower = max(points[-1] + 0.05, end - remaining * maximum_duration)
        upper = min(points[-1] + maximum_duration, end - remaining * 0.05)
        candidates = [point for point in pauses if lower <= point <= upper]
        chosen = min(candidates, key=lambda point: abs(point - ideal)) if candidates else ideal
        points.append(round(max(lower, min(chosen, upper)), 3))
    return [*points, end]


def split_cue(
    cue: dict, maximum_characters: int, maximum_duration: float = 12.0,
) -> list[dict]:
    """Partition one cue's time proportionally across readable text chunks."""
    text_field = "translated_text" if cue.get("translated_text") is not None else "source_text" if cue.get("source_text") is not None else "text"
    chunks = text_chunks(str(cue[text_field]), maximum_characters)
    duration = float(cue["end"]) - float(cue["start"])
    minimum_count = max(1, math.ceil(duration / maximum_duration))
    chunks = _expand_chunks(chunks, minimum_count)
    if len(chunks) == 1:
        return [{
            **cue,
            text_field: chunks[0],
            "provenance": append_provenance(cue, "subtitle-repair", "text-normalization"),
        }]
    start, end = float(cue["start"]), float(cue["end"])
    points = _bounded_timing_points(cue, len(chunks), maximum_duration)
    output = []
    for index, chunk in enumerate(chunks):
        cursor, chunk_end = points[index], points[index + 1]
        parent_id = str(cue.get("id") or f"cue-{round(start, 3)}")
        metadata = {**cue.get("metadata", {}), "parent_cue_id": parent_id}
        output.append({
            **cue,
            "id": f"{parent_id}.part-{index + 1:02d}",
            **({"metadata": metadata} if "metadata" in cue else {"parent_cue_id": parent_id}),
            "start": round(cursor, 3),
            "end": round(chunk_end, 3),
            text_field: chunk,
            "provenance": append_provenance(
                cue, "subtitle-repair", "split-display-cue",
                parent_cue_id=parent_id, part=index + 1, part_count=len(chunks),
            ),
        })
    return output


def repair(
    transcript: dict,
    minimum_duration: float = 0.5,
    maximum_characters: int = 84,
    maximum_characters_per_second: float = 20.0,
    maximum_duration: float = 12.0,
) -> dict:
    """Split long text and borrow only neighboring silence for readability."""
    cues = [part for cue in transcript.get("segments", []) for part in split_cue(cue, maximum_characters, maximum_duration)]
    cues.sort(key=lambda item: (float(item["start"]), float(item["end"])))
    for index, cue in enumerate(cues):
        displayed = cue.get("translated_text") or cue.get("source_text") or cue.get("text", "")
        characters = len(re.sub(r"\s+", "", str(displayed)))
        required = max(minimum_duration, characters / maximum_characters_per_second)
        previous_end = float(cues[index - 1]["end"]) if index else 0.0
        next_start = float(cues[index + 1]["start"]) if index + 1 < len(cues) else math.inf
        start, end = float(cue["start"]), float(cue["end"])
        end = min(next_start, max(end, start + required))
        if end - start < required:
            start = max(previous_end, end - required)
        cue["start"], cue["end"] = round(start, 3), round(end, 3)
    if transcript.get("artifact_type") == "canonical_timed_text":
        return {
            **transcript,
            "metadata": {**transcript.get("metadata", {}), "readability_repaired": True},
            "segments": cues,
        }
    return {**transcript, "segments": cues, "readability_repaired": True}


def write_srt(path: Path, segments: list[dict]) -> None:
    """Write repaired cues as UTF-8 SRT."""
    content = "\n\n".join(
        f"{index}\n{srt_timestamp(item['start'])} --> {srt_timestamp(item['end'])}\n{item.get('translated_text') or item.get('source_text') or item.get('text', '')}"
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
    parser.add_argument("--maximum-duration", type=float, default=12.0)
    args = parser.parse_args()
    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    repaired = repair(
        transcript, args.minimum_duration, args.maximum_characters,
        args.maximum_characters_per_second, args.maximum_duration,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_srt.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(repaired, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_srt(args.output_srt, repaired["segments"])
    print(f"Repaired {len(repaired['segments'])} subtitle cues")


if __name__ == "__main__":
    main()
