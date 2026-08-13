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


def _display_field(cue: dict) -> str:
    return "translated_text" if cue.get("translated_text") is not None else "source_text" if cue.get("source_text") is not None else "text"


def _merge_compatible(
    left: dict, right: dict, maximum_gap: float, maximum_duration: float,
    maximum_characters: int, maximum_characters_per_second: float,
) -> bool:
    gap = float(right["start"]) - float(left["end"])
    field = _display_field(left)
    if _display_field(right) != field:
        return False
    text = f"{left[field].strip()} {right[field].strip()}".strip()
    duration = float(right["end"]) - float(left["start"])
    same_speaker = left.get("speaker", "unknown") == right.get("speaker", "unknown")
    left_group, right_group = left.get("semantic_group_id"), right.get("semantic_group_id")
    same_group = not left_group or not right_group or left_group == right_group
    return (
        same_speaker and same_group and 0 <= gap <= maximum_gap
        and 0 < duration <= maximum_duration and len(text) <= maximum_characters
        and len(re.sub(r"\s+", "", text)) / duration <= maximum_characters_per_second
    )


def _merged_cue(left: dict, right: dict) -> dict:
    """Merge compatible neighbors while retaining both source mappings."""
    field = _display_field(left)
    source_ids = []
    for source_id in [*left.get("source_cue_ids", []), *right.get("source_cue_ids", [])]:
        if source_id not in source_ids:
            source_ids.append(source_id)
    left_id, right_id = str(left.get("id", "left")), str(right.get("id", "right"))
    metadata = {
        **left.get("metadata", {}),
        "merged_cue_ids": [left_id, right_id],
    }
    return {
        **left,
        "id": f"{left_id}+{right_id}",
        "source_cue_ids": source_ids or left.get("source_cue_ids", []),
        "start": min(float(left["start"]), float(right["start"])),
        "end": max(float(left["end"]), float(right["end"])),
        field: f"{left[field].strip()} {right[field].strip()}".strip(),
        "words": [*left.get("words", []), *right.get("words", [])],
        **({"metadata": metadata} if "metadata" in left else {"merged_cue_ids": [left_id, right_id]}),
        "provenance": append_provenance(
            left, "subtitle-repair", "merge-short-compatible-cues",
            merged_cue_ids=[left_id, right_id],
        ),
    }


def repair_short_cues(
    cues: list[dict], minimum_duration: float, maximum_duration: float,
    maximum_characters: int, maximum_characters_per_second: float,
    maximum_merge_gap: float = 0.45,
) -> list[dict]:
    """Extend into free space, then merge only objectively compatible short cues."""
    repaired = [dict(cue) for cue in cues]
    index = 0
    while index < len(repaired):
        cue = repaired[index]
        start, end = float(cue["start"]), float(cue["end"])
        if end - start >= minimum_duration:
            index += 1
            continue
        previous_end = float(repaired[index - 1]["end"]) if index else 0.0
        next_start = float(repaired[index + 1]["start"]) if index + 1 < len(repaired) else end + minimum_duration
        needed = minimum_duration - (end - start)
        add_after = min(needed, max(0.0, next_start - end))
        end += add_after
        needed -= add_after
        add_before = min(needed, max(0.0, start - previous_end))
        start -= add_before
        cue["start"], cue["end"] = round(start, 3), round(end, 3)
        if end - start + 1e-9 >= minimum_duration:
            cue["provenance"] = append_provenance(
                cue, "subtitle-repair", "extend-short-cue-into-silence",
                added_before=round(add_before, 3), added_after=round(add_after, 3),
            )
            index += 1
            continue
        if index + 1 < len(repaired) and _merge_compatible(
            cue, repaired[index + 1], maximum_merge_gap, maximum_duration,
            maximum_characters, maximum_characters_per_second,
        ):
            repaired[index:index + 2] = [_merged_cue(cue, repaired[index + 1])]
            continue
        if index and _merge_compatible(
            repaired[index - 1], cue, maximum_merge_gap, maximum_duration,
            maximum_characters, maximum_characters_per_second,
        ):
            repaired[index - 1:index + 1] = [_merged_cue(repaired[index - 1], cue)]
            index = max(0, index - 1)
            continue
        cue["provenance"] = append_provenance(
            cue, "subtitle-repair", "short-cue-unresolved",
            duration=round(end - start, 3),
        )
        index += 1
    return repaired


def subtitle_lines(text: str, maximum_line_characters: int = 42, maximum_lines: int = 2) -> str:
    """Balance display text across at most two language-aware lines when possible."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= maximum_line_characters:
        return cleaned
    words = cleaned.split()
    if len(words) == 1:
        chunks = [cleaned[index:index + maximum_line_characters] for index in range(0, len(cleaned), maximum_line_characters)]
    else:
        chunks, current = [], ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if current and len(candidate) > maximum_line_characters:
                chunks.append(current)
                current = word
            else:
                current = candidate
        if current:
            chunks.append(current)
    return "\n".join(chunks) if len(chunks) <= maximum_lines else cleaned


def redistribute_group_timing(cues: list[dict], maximum_characters_per_second: float) -> list[dict]:
    """Allocate a semantic group's full envelope by target text density."""
    output = [dict(cue) for cue in cues]
    groups: dict[str, list[int]] = {}
    for index, cue in enumerate(output):
        groups.setdefault(str(cue.get("semantic_group_id") or cue.get("id") or index), []).append(index)
    for group_id, indexes in groups.items():
        if len(indexes) < 2:
            continue
        start, end = float(output[indexes[0]]["start"]), float(output[indexes[-1]]["end"])
        weights = [max(1, len(re.sub(r"\s+", "", str(output[index].get(_display_field(output[index]), ""))))) for index in indexes]
        total = sum(weights)
        if (end - start) * maximum_characters_per_second + 1e-9 < total:
            continue
        cursor, cumulative = start, 0
        for position, (index, weight) in enumerate(zip(indexes, weights)):
            cumulative += weight
            cue_end = end if position == len(indexes) - 1 else start + (end - start) * cumulative / total
            output[index]["start"], output[index]["end"] = round(cursor, 3), round(cue_end, 3)
            output[index]["provenance"] = append_provenance(
                output[index], "subtitle-repair", "redistribute-semantic-group-timing",
                semantic_group_id=group_id,
            )
            cursor = cue_end
    return output


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
    maximum_line_characters: int = 42,
) -> dict:
    """Split long text and borrow only neighboring silence for readability."""
    cues = [part for cue in transcript.get("segments", []) for part in split_cue(cue, maximum_characters, maximum_duration)]
    cues.sort(key=lambda item: (float(item["start"]), float(item["end"])))
    cues = repair_short_cues(
        cues, minimum_duration, maximum_duration, maximum_characters,
        maximum_characters_per_second,
    )
    cues = redistribute_group_timing(cues, maximum_characters_per_second)
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
        field = _display_field(cue)
        cue[field] = subtitle_lines(str(cue[field]), maximum_line_characters)
    if transcript.get("artifact_type") == "canonical_timed_text":
        return {
            **transcript,
            "metadata": {**transcript.get("metadata", {}), "readability_repaired": True},
            "segments": cues,
        }
    return {**transcript, "segments": cues, "readability_repaired": True}


def _cue_state(document: dict) -> tuple:
    """Return semantic output state while ignoring append-only audit provenance."""
    return tuple(
        (
            cue.get("id"), round(float(cue["start"]), 3), round(float(cue["end"]), 3),
            cue.get("translated_text"), cue.get("source_text"), cue.get("text"),
        )
        for cue in document.get("segments", [])
    )


def iterative_repair(
    transcript: dict,
    *,
    maximum_passes: int = 4,
    minimum_duration: float = 0.5,
    maximum_duration: float = 12.0,
    maximum_characters: int = 84,
    maximum_line_characters: int = 42,
    maximum_characters_per_second: float = 20.0,
) -> tuple[dict, dict]:
    """Accept only improving repair passes and stop at convergence or a hard limit."""
    if maximum_passes < 1:
        raise ValueError("maximum_passes must be positive")
    try:
        from .qa_transcript import analyze
    except ImportError:
        from qa_transcript import analyze

    current = transcript
    initial = analyze(
        current, maximum_duration, minimum_duration=minimum_duration,
        maximum_characters=maximum_characters,
        maximum_line_characters=maximum_line_characters,
        maximum_characters_per_second=maximum_characters_per_second,
    )
    current_score = len(initial["issues"])
    passes = []
    reason = "maximum-passes"
    for number in range(1, maximum_passes + 1):
        candidate = repair(
            current, minimum_duration, maximum_characters,
            maximum_characters_per_second, maximum_duration,
            maximum_line_characters,
        )
        report = analyze(
            candidate, maximum_duration, minimum_duration=minimum_duration,
            maximum_characters=maximum_characters,
            maximum_line_characters=maximum_line_characters,
            maximum_characters_per_second=maximum_characters_per_second,
        )
        score = len(report["issues"])
        stable = _cue_state(candidate) == _cue_state(current)
        improved = score < current_score
        passes.append({
            "number": number, "score_before": current_score, "score_after": score,
            "improved": improved, "stable": stable,
            "issue_counts": report["issue_counts"],
        })
        if improved:
            current, current_score = candidate, score
        if stable:
            reason = "stable-state"
            break
        if not improved:
            reason = "no-objective-improvement"
            break
        if score == 0:
            reason = "quality-target-reached"
            break
    audit = {
        "schema_version": 1, "maximum_passes": maximum_passes,
        "initial_score": len(initial["issues"]), "final_score": current_score,
        "termination_reason": reason, "passes": passes,
    }
    if current.get("artifact_type") == "canonical_timed_text":
        current = {**current, "metadata": {**current.get("metadata", {}), "iterative_repair": audit}}
    else:
        current = {**current, "iterative_repair": audit}
    return current, audit


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
    parser.add_argument("--maximum-line-characters", type=int, default=42)
    args = parser.parse_args()
    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    repaired = repair(
        transcript, args.minimum_duration, args.maximum_characters,
        args.maximum_characters_per_second, args.maximum_duration,
        args.maximum_line_characters,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_srt.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(repaired, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_srt(args.output_srt, repaired["segments"])
    print(f"Repaired {len(repaired['segments'])} subtitle cues")


if __name__ == "__main__":
    main()
