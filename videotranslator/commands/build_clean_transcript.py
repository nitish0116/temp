"""Build coherent source-language semantic groups from aligned timed text."""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path

try:
    from .canonical_timed_text import (
        adapt_legacy_transcript,
        append_provenance,
        provenance_entries,
        validate_canonical_timed_text,
    )
    from .segment_utterances import CJK_CHARACTER, TERMINAL_PUNCTUATION, join_words, segment_words
except ImportError:
    from canonical_timed_text import adapt_legacy_transcript, append_provenance, provenance_entries, validate_canonical_timed_text
    from segment_utterances import CJK_CHARACTER, TERMINAL_PUNCTUATION, join_words, segment_words


SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?\u3002\uff01\uff1f\u061f\u0964\u0965\u2026])\s+")


def _join_text(parts: list[str]) -> str:
    """Join source fragments while retaining natural CJK spacing."""
    cleaned = [re.sub(r"\s+", " ", part).strip() for part in parts if part.strip()]
    combined = "".join(cleaned)
    if combined and CJK_CHARACTER.search(combined) and not re.search(r"[A-Za-z0-9]", combined):
        return combined
    return " ".join(cleaned)


def _text_units(segment: dict) -> list[dict]:
    """Split a wordless legacy cue into sentence units with proportional timing."""
    text = str(segment.get("source_text") or "").strip()
    parts = [part.strip() for part in SENTENCE_BOUNDARY.split(text) if part.strip()] or [text]
    start, end = float(segment["start"]), float(segment["end"])
    weights = [max(1, len(re.sub(r"\s+", "", part))) for part in parts]
    total = sum(weights)
    cursor = start
    units = []
    for index, (part, weight) in enumerate(zip(parts, weights)):
        part_end = end if index == len(parts) - 1 else cursor + (end - start) * weight / total
        units.append({
            "start": cursor,
            "end": part_end,
            "text": part,
            "words": [],
            "speaker": segment["speaker"],
            "source_cue_ids": list(segment["source_cue_ids"]),
            "source_segment": segment,
        })
        cursor = part_end
    return units


def _word_units(segments: list[dict], maximum_duration: float) -> list[dict]:
    """Group timestamped words while retaining their originating cue IDs."""
    words = []
    source_by_id = {}
    for segment in segments:
        for source_id in segment["source_cue_ids"]:
            source_by_id[str(source_id)] = segment
        for word in segment["words"]:
            words.append({
                **deepcopy(word),
                "speaker": word.get("speaker") or segment["speaker"],
                "_source_cue_ids": list(segment["source_cue_ids"]),
            })
    units = []
    for group in segment_words(
        words, maximum_duration=maximum_duration, maximum_characters=1000,
        pause_threshold=1.0, punctuation_pause=0.12,
    ):
        source_ids = []
        for word in group:
            for source_id in word.pop("_source_cue_ids"):
                if source_id not in source_ids:
                    source_ids.append(source_id)
        units.append({
            "start": float(group[0]["start"]),
            "end": float(group[-1]["end"]),
            "text": join_words(group),
            "words": group,
            "speaker": group[0].get("speaker") or "unknown",
            "source_cue_ids": source_ids,
            "source_segments": [source_by_id[str(source_id)] for source_id in source_ids],
        })
    return units


def _merge_continuations(units: list[dict], maximum_gap: float, maximum_duration: float) -> list[list[dict]]:
    """Join incomplete neighboring units but never cross a speaker boundary."""
    groups: list[list[dict]] = []
    for unit in sorted(units, key=lambda item: (item["start"], item["end"])):
        if groups:
            previous = groups[-1][-1]
            previous_text = previous["text"].strip()
            same_speaker = previous["speaker"] == unit["speaker"]
            gap = unit["start"] - previous["end"]
            duration = unit["end"] - groups[-1][0]["start"]
            if gap < 0 and duration <= maximum_duration:
                # Recovery can produce a wordless cue nested inside a longer
                # word-aligned unit. It must share the semantic envelope rather
                # than becoming an impossible overlapping display group.
                groups[-1].append(unit)
                continue
            if (
                same_speaker
                and 0 <= gap <= maximum_gap
                and duration <= maximum_duration
                and not TERMINAL_PUNCTUATION.search(previous_text)
            ):
                groups[-1].append(unit)
                continue
        groups.append([unit])
    return groups


def _restore_source_envelopes(clean_segments: list[dict], source_segments: list[dict]) -> None:
    """Restore source-cue edges and divide internal pauses without creating overlaps."""
    core_starts = [float(segment["start"]) for segment in clean_segments]
    core_ends = [float(segment["end"]) for segment in clean_segments]
    for source in source_segments:
        source_ids = {str(value) for value in source.get("source_cue_ids", [])}
        indexes = [
            index for index, segment in enumerate(clean_segments)
            if source_ids.intersection(str(value) for value in segment["source_cue_ids"])
        ]
        if not indexes:
            continue
        first, last = indexes[0], indexes[-1]
        lower_bound = core_ends[first - 1] if first else float("-inf")
        upper_bound = core_starts[last + 1] if last + 1 < len(clean_segments) else float("inf")
        clean_segments[first]["start"] = max(
            lower_bound, min(clean_segments[first]["start"], float(source["start"]))
        )
        clean_segments[last]["end"] = min(
            upper_bound, max(clean_segments[last]["end"], float(source["end"]))
        )
        for left, right in zip(indexes, indexes[1:]):
            if right != left + 1:
                continue
            boundary = (clean_segments[left]["end"] + clean_segments[right]["start"]) / 2
            clean_segments[left]["end"] = boundary
            clean_segments[right]["start"] = boundary
    for segment in clean_segments:
        segment["start"] = round(segment["start"], 3)
        segment["end"] = round(segment["end"], 3)


def build_clean_transcript(
    document: dict,
    maximum_gap: float = 0.9,
    maximum_duration: float = 30.0,
) -> dict:
    """Return canonical, coherent semantic groups with complete source lineage."""
    canonical = adapt_legacy_transcript(document)
    word_segments = [segment for segment in canonical["segments"] if segment["words"]]
    word_ids = {id(segment) for segment in word_segments}
    units = _word_units(word_segments, maximum_duration)
    for segment in canonical["segments"]:
        if id(segment) not in word_ids:
            units.extend(_text_units(segment))
    groups = _merge_continuations(units, maximum_gap, maximum_duration)
    clean_segments = []
    for index, group in enumerate(groups, start=1):
        source_ids = []
        source_segments = []
        words = []
        for unit in group:
            for source_id in unit["source_cue_ids"]:
                if source_id not in source_ids:
                    source_ids.append(source_id)
            for source in unit.get("source_segments", [unit.get("source_segment")]):
                if source is not None and all(source is not existing for existing in source_segments):
                    source_segments.append(source)
            words.extend(unit["words"])
        provenance = []
        for source in source_segments:
            provenance.extend(provenance_entries(source.get("provenance")))
        base = {"provenance": provenance}
        group_id = f"semantic-{index:04d}"
        clean_segments.append({
            "id": group_id,
            "semantic_group_id": group_id,
            "source_cue_ids": source_ids,
            "start": round(group[0]["start"], 3),
            "end": round(group[-1]["end"], 3),
            "source_text": _join_text([unit["text"] for unit in group]),
            "translated_text": None,
            "speaker": group[0]["speaker"],
            "words": words,
            "confidence": {
                "source_cues": {
                    str(source_id): deepcopy(source.get("confidence", {}))
                    for source_id, source in zip(source_ids, source_segments)
                }
            },
            "provenance": append_provenance(
                base, "semantic-grouping", "pause-punctuation-speaker-rules",
                source_cue_ids=source_ids,
            ),
            "metadata": {
                "raw_source_texts": [source.get("source_text") for source in source_segments],
            },
        })
    _restore_source_envelopes(clean_segments, canonical["segments"])
    result = {
        **canonical,
        "stage": "clean_transcript",
        "output_language": canonical["source_language"],
        "metadata": {
            **canonical.get("metadata", {}),
            "semantic_grouping": {
                "maximum_gap": maximum_gap,
                "maximum_duration": maximum_duration,
                "input_segment_count": len(canonical["segments"]),
                "output_group_count": len(clean_segments),
            },
        },
        "segments": clean_segments,
    }
    validate_canonical_timed_text(result)
    return result


def main() -> None:
    """Build and validate a clean semantic transcript JSON artifact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--maximum-gap", type=float, default=0.9)
    parser.add_argument("--maximum-duration", type=float, default=30.0)
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))
    result = build_clean_transcript(source, args.maximum_gap, args.maximum_duration)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built {len(result['segments'])} clean semantic groups")


if __name__ == "__main__":
    main()
