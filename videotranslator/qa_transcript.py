"""Validate translated transcript timing and produce a machine-readable QA report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def analyze(transcript: dict, maximum_duration: float) -> dict:
    segments = transcript.get("segments")
    if not isinstance(segments, list):
        raise ValueError("Transcript must contain a segments array")

    issues = []
    previous_end = 0.0
    for index, segment in enumerate(segments):
        start = float(segment["start"])
        end = float(segment["end"])
        text = str(segment["text"]).strip()
        duration = end - start
        if start < previous_end:
            issues.append(
                {"type": "overlap", "segment": index, "start": start, "previous_end": previous_end}
            )
        if duration <= 0:
            issues.append({"type": "invalid_duration", "segment": index, "duration": duration})
        elif duration > maximum_duration:
            issues.append({"type": "long_duration", "segment": index, "duration": duration})
        if not text:
            issues.append({"type": "empty_text", "segment": index})
        previous_end = max(previous_end, end)

    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue["type"]] = counts.get(issue["type"], 0) + 1
    return {
        "passed": not issues,
        "segment_count": len(segments),
        "maximum_segment_duration": maximum_duration,
        "issue_counts": counts,
        "issues": issues,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check transcript segment timing.")
    parser.add_argument("transcript", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--maximum-duration", type=float, default=12.0)
    args = parser.parse_args()

    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    report = analyze(transcript, args.maximum_duration)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"QA {'passed' if report['passed'] else 'needs review'}: "
        f"{report['segment_count']} segments, {len(report['issues'])} issues"
    )


if __name__ == "__main__":
    main()
