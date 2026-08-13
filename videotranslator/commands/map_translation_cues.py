"""Map translated semantic groups into readable target-language display cues."""

from __future__ import annotations

import re
from copy import deepcopy

try:
    from .canonical_timed_text import append_provenance, validate_canonical_timed_text
    from .repair_subtitles import text_chunks
except ImportError:
    from canonical_timed_text import append_provenance, validate_canonical_timed_text
    from repair_subtitles import text_chunks


def pause_boundaries(words: list[dict], minimum_pause: float = 0.12) -> list[float]:
    """Return acoustic midpoints between words separated by measurable silence."""
    ordered = sorted(words, key=lambda word: (float(word["start"]), float(word["end"])))
    return [
        (float(left["end"]) + float(right["start"])) / 2
        for left, right in zip(ordered, ordered[1:])
        if float(right["start"]) - float(left["end"]) >= minimum_pause
    ]


def allocate_boundaries(
    start: float, end: float, chunks: list[str], acoustic: list[float],
) -> list[float]:
    """Place internal boundaries near weighted text targets and source pauses."""
    if len(chunks) <= 1:
        return []
    weights = [max(1, len(re.sub(r"\s+", "", chunk))) for chunk in chunks]
    total = sum(weights)
    cumulative = 0
    boundaries = []
    available = [point for point in acoustic if start < point < end]
    for weight in weights[:-1]:
        cumulative += weight
        ideal = start + (end - start) * cumulative / total
        lower = boundaries[-1] + 0.05 if boundaries else start + 0.05
        candidates = [point for point in available if lower < point < end - 0.05]
        chosen = min(candidates, key=lambda point: abs(point - ideal)) if candidates else ideal
        boundaries.append(round(max(lower, min(chosen, end - 0.05)), 3))
        available = [point for point in available if point > boundaries[-1]]
    return boundaries


def map_translated_groups(
    document: dict,
    maximum_characters: int = 84,
    minimum_pause: float = 0.12,
) -> dict:
    """Create target display cues whose text maps exactly once to semantic groups."""
    validate_canonical_timed_text(document)
    if document["stage"] != "translated":
        raise ValueError("Display mapping requires a translated artifact")
    cues = []
    for group in document["segments"]:
        translated = group.get("translated_text") or ""
        if not translated.strip():
            raise ValueError(f"Semantic group {group['semantic_group_id']} has no translation")
        chunks = text_chunks(translated, maximum_characters)
        start, end = float(group["start"]), float(group["end"])
        boundaries = allocate_boundaries(
            start, end, chunks, pause_boundaries(group["words"], minimum_pause)
        )
        points = [start, *boundaries, end]
        for index, chunk in enumerate(chunks, start=1):
            cue_id = f"{group['semantic_group_id']}.display-{index:02d}"
            cues.append({
                "id": cue_id,
                "semantic_group_id": group["semantic_group_id"],
                "source_cue_ids": deepcopy(group["source_cue_ids"]),
                "start": round(points[index - 1], 3),
                "end": round(points[index], 3),
                "source_text": group["source_text"] if index == 1 else None,
                "translated_text": chunk,
                "speaker": group["speaker"],
                "words": deepcopy(group["words"]),
                "confidence": deepcopy(group["confidence"]),
                "provenance": append_provenance(
                    group, "display-cue-mapping", "target-text-and-source-pauses",
                    parent_group_id=group["semantic_group_id"], part=index,
                    part_count=len(chunks),
                ),
                "metadata": {
                    **deepcopy(group["metadata"]),
                    "parent_group_id": group["semantic_group_id"],
                    "display_part": index,
                    "display_part_count": len(chunks),
                },
            })
    result = {
        **document,
        "metadata": {
            **document.get("metadata", {}),
            "display_mapping": {
                "maximum_characters": maximum_characters,
                "minimum_pause": minimum_pause,
                "semantic_group_count": len(document["segments"]),
                "display_cue_count": len(cues),
            },
        },
        "segments": cues,
    }
    validate_canonical_timed_text(result)
    return result
