"""Apply strict automatic quality gates to a generated subtitle transcript."""

from __future__ import annotations


import argparse
import json
import re
from pathlib import Path


CONTINUATION_END = re.compile(r"[,;:\u060c\u061b\u3001\uff0c\uff1b\uff1a\u2014-][\"'\u201d\u2019\u00bb\uff09\u3011]*$")
CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
CJK_CHARACTER = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


def required_line_count(text: str, maximum_line_characters: int) -> int:
    """Return the minimum readable line count without breaking Latin words."""
    explicit = text.splitlines() or [text]
    count = 0
    for line in explicit:
        if len(line) <= maximum_line_characters:
            count += 1
            continue
        if CJK_CHARACTER.search(line) and not re.search(r"[A-Za-z0-9]", line):
            count += (len(line) + maximum_line_characters - 1) // maximum_line_characters
            continue
        current = 0
        for word in line.split():
            if len(word) > maximum_line_characters:
                return 10**9
            proposed = len(word) if current == 0 else current + 1 + len(word)
            if proposed > maximum_line_characters:
                count += 1
                current = len(word)
            else:
                current = proposed
        if current:
            count += 1
    return count


def source_speech_coverage(subtitles: dict, source: dict) -> tuple[float, float]:
    """Measure source cue-event and source-time coverage by subtitle intervals."""
    cues = [(float(item["start"]), float(item["end"])) for item in subtitles.get("segments", [])]
    source_cues = [
        (float(word["start"]), float(word["end"]))
        for item in source.get("segments", [])
        for word in item.get("words", [])
        if word.get("start") is not None and word.get("end") is not None
        and float(word["end"]) > float(word["start"])
    ]
    if not source_cues:
        source_cues = [
            (float(item["start"]), float(item["end"]))
            for item in source.get("segments", [])
            if float(item["end"]) > float(item["start"])
        ]
    if not source_cues:
        return 1.0, 1.0
    covered_events = 0
    covered_seconds = 0.0
    total_seconds = sum(end - start for start, end in source_cues)
    for start, end in source_cues:
        overlaps = sorted(
            (max(start, left), min(end, right))
            for left, right in cues
            if min(end, right) > max(start, left)
        )
        merged: list[list[float]] = []
        for left, right in overlaps:
            if merged and left <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], right)
            else:
                merged.append([left, right])
        overlap = sum(right - left for left, right in merged)
        covered_seconds += overlap
        midpoint = (start + end) / 2
        if overlap >= 0.03 or any(left - 0.1 <= midpoint <= right + 0.1 for left, right in cues):
            covered_events += 1
    return covered_events / len(source_cues), min(1.0, covered_seconds / total_seconds)


def diarized_speech_coverage(subtitles: dict, diarization: dict) -> tuple[float, float]:
    """Measure subtitle coverage of independently detected speaker turns.

    This catches speech omitted by ASR, which cannot be detected by comparing a
    subtitle file only with the transcript from which it was created.
    """
    cues = [(float(item["start"]), float(item["end"])) for item in subtitles.get("segments", [])]
    turns = [
        (float(turn["start"]), float(turn["end"]))
        for turn in diarization.get("turns", [])
        if turn.get("start") is not None and turn.get("end") is not None
        and float(turn["end"]) > float(turn["start"])
        # Sub-100 ms label flickers are below a useful subtitle event and are
        # common at exclusive-speaker boundaries.
        and float(turn["end"]) - float(turn["start"]) >= 0.1
    ]
    if not turns:
        return 1.0, 1.0
    covered_turns = 0
    covered_seconds = 0.0
    total_seconds = sum(end - start for start, end in turns)
    for start, end in turns:
        overlaps = sorted(
            (max(start, left), min(end, right))
            for left, right in cues
            if min(end, right) > max(start, left)
        )
        merged: list[list[float]] = []
        for left, right in overlaps:
            if merged and left <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], right)
            else:
                merged.append([left, right])
        overlap = sum(right - left for left, right in merged)
        covered_seconds += overlap
        if overlap >= min(0.1, (end - start) * 0.5):
            covered_turns += 1
    return covered_turns / len(turns), min(1.0, covered_seconds / total_seconds)


def malformed_text_reasons(text: str) -> list[str]:
    """Return deterministic reasons why cue text is incomplete or corrupted."""
    reasons = []
    if "\ufffd" in text or CONTROL_CHARACTER.search(text):
        reasons.append("invalid_character")
    if re.search(r"[ \t]{2,}", text) or text != text.strip():
        reasons.append("bad_whitespace")
    if CONTINUATION_END.search(text):
        reasons.append("incomplete_ending")
    for left, right, name in (("(", ")", "parentheses"), ("[", "]", "brackets"), ("{", "}", "braces")):
        if text.count(left) != text.count(right):
            reasons.append(f"unbalanced_{name}")
    return reasons


def analyze(
    transcript: dict,
    maximum_duration: float,
    *,
    minimum_duration: float = 0.5,
    maximum_characters: int = 84,
    maximum_line_characters: int = 42,
    maximum_lines: int = 2,
    maximum_characters_per_second: float = 20.0,
    source_transcript: dict | None = None,
    minimum_source_event_coverage: float = 0.98,
    minimum_source_time_coverage: float = 0.95,
    diarization_report: dict | None = None,
    minimum_diarized_turn_coverage: float = 0.90,
    minimum_diarized_time_coverage: float = 0.90,
) -> dict:
    """Return blocking timing, readability, text, and source-coverage issues."""
    segments = transcript.get("segments")
    if not isinstance(segments, list):
        raise ValueError("Transcript must contain a segments array")

    issues = []
    previous_end = 0.0
    reading_speeds = []
    for index, segment in enumerate(segments):
        start = float(segment["start"])
        end = float(segment["end"])
        raw_text = str(
            segment.get("translated_text")
            or segment.get("source_text")
            or segment.get("text", "")
        )
        text = raw_text.strip()
        duration = end - start
        if start < previous_end:
            issues.append({"type": "overlap", "segment": index, "start": start, "previous_end": previous_end})
        if duration <= 0:
            issues.append({"type": "invalid_duration", "segment": index, "duration": duration})
        else:
            if duration > maximum_duration:
                issues.append({"type": "long_duration", "segment": index, "duration": duration})
            if duration < minimum_duration:
                issues.append({"type": "short_duration", "segment": index, "duration": duration})
            characters = len(re.sub(r"\s+", "", text))
            speed = characters / duration
            reading_speeds.append(speed)
            if speed > maximum_characters_per_second + 1e-6:
                issues.append({"type": "fast_reading_speed", "segment": index, "characters_per_second": round(speed, 2)})
        if not text:
            issues.append({"type": "empty_text", "segment": index})
        else:
            if len(text) > maximum_characters:
                issues.append({"type": "long_text", "segment": index, "characters": len(text)})
            lines = required_line_count(text, maximum_line_characters)
            if lines > maximum_lines:
                issues.append({"type": "excessive_lines", "segment": index, "required_lines": lines})
            for reason in malformed_text_reasons(raw_text):
                issues.append({"type": "malformed_text", "segment": index, "reason": reason})
        previous_end = max(previous_end, end)

    coverage = None
    if source_transcript is not None:
        event_coverage, time_coverage = source_speech_coverage(transcript, source_transcript)
        coverage = {"source_event_coverage": event_coverage, "source_time_coverage": time_coverage}
        if event_coverage < minimum_source_event_coverage:
            issues.append({"type": "missing_source_events", "coverage": round(event_coverage, 4), "minimum": minimum_source_event_coverage})
        if time_coverage < minimum_source_time_coverage:
            issues.append({"type": "missing_source_time", "coverage": round(time_coverage, 4), "minimum": minimum_source_time_coverage})

    diarized_coverage = None
    if diarization_report is not None:
        turn_coverage, time_coverage = diarized_speech_coverage(transcript, diarization_report)
        diarized_coverage = {"turn_coverage": turn_coverage, "time_coverage": time_coverage}
        if turn_coverage < minimum_diarized_turn_coverage:
            issues.append({"type": "missing_diarized_turns", "coverage": round(turn_coverage, 4), "minimum": minimum_diarized_turn_coverage})
        if time_coverage < minimum_diarized_time_coverage:
            issues.append({"type": "missing_diarized_time", "coverage": round(time_coverage, 4), "minimum": minimum_diarized_time_coverage})

    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue["type"]] = counts.get(issue["type"], 0) + 1
    return {
        "schema_version": 2,
        "passed": not issues,
        "segment_count": len(segments),
        "thresholds": {
            "minimum_duration": minimum_duration,
            "maximum_duration": maximum_duration,
            "maximum_characters": maximum_characters,
            "maximum_line_characters": maximum_line_characters,
            "maximum_lines": maximum_lines,
            "maximum_characters_per_second": maximum_characters_per_second,
            "minimum_source_event_coverage": minimum_source_event_coverage,
            "minimum_source_time_coverage": minimum_source_time_coverage,
            "minimum_diarized_turn_coverage": minimum_diarized_turn_coverage,
            "minimum_diarized_time_coverage": minimum_diarized_time_coverage,
        },
        "maximum_segment_duration": maximum_duration,
        "maximum_observed_characters_per_second": round(max(reading_speeds, default=0.0), 2),
        "source_coverage": coverage,
        "diarized_coverage": diarized_coverage,
        "issue_counts": counts,
        "issues": issues,
    }


def main() -> None:
    """Analyze subtitle JSON, write its report, and fail on blocking findings."""
    parser = argparse.ArgumentParser(description="Apply strict automatic subtitle QA.")
    parser.add_argument("transcript", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--source-transcript", type=Path)
    parser.add_argument("--diarization-report", type=Path)
    parser.add_argument("--minimum-duration", type=float, default=0.5)
    parser.add_argument("--maximum-duration", type=float, default=12.0)
    parser.add_argument("--maximum-characters", type=int, default=84)
    parser.add_argument("--maximum-line-characters", type=int, default=42)
    parser.add_argument("--maximum-lines", type=int, default=2)
    parser.add_argument("--maximum-characters-per-second", type=float, default=20.0)
    parser.add_argument("--minimum-source-event-coverage", type=float, default=0.98)
    parser.add_argument("--minimum-source-time-coverage", type=float, default=0.95)
    parser.add_argument("--minimum-diarized-turn-coverage", type=float, default=0.90)
    parser.add_argument("--minimum-diarized-time-coverage", type=float, default=0.90)
    args = parser.parse_args()

    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    source = json.loads(args.source_transcript.read_text(encoding="utf-8")) if args.source_transcript else None
    diarization = json.loads(args.diarization_report.read_text(encoding="utf-8")) if args.diarization_report else None
    report = analyze(
        transcript,
        args.maximum_duration,
        minimum_duration=args.minimum_duration,
        maximum_characters=args.maximum_characters,
        maximum_line_characters=args.maximum_line_characters,
        maximum_lines=args.maximum_lines,
        maximum_characters_per_second=args.maximum_characters_per_second,
        source_transcript=source,
        minimum_source_event_coverage=args.minimum_source_event_coverage,
        minimum_source_time_coverage=args.minimum_source_time_coverage,
        diarization_report=diarization,
        minimum_diarized_turn_coverage=args.minimum_diarized_turn_coverage,
        minimum_diarized_time_coverage=args.minimum_diarized_time_coverage,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"QA {'passed' if report['passed'] else 'failed'}: {report['segment_count']} segments, {len(report['issues'])} issues")
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
