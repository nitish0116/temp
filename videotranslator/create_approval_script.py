"""Create an editable, schema-compliant English script review draft."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def create_draft(project_id: str, transcript: dict, qa_report: dict | None) -> dict:
    """Convert a transcript and optional QA report into an editable draft.

    Stable IDs such as ``seg-0001`` allow downstream TTS artifacts to refer to
    dialogue even after a reviewer corrects its English text.

    Example::

        draft = create_draft(
            "episode-1",
            {"segments": [{"start": 1, "end": 2, "text": "Hello"}]},
            None,
        )
    """
    segments = transcript.get("segments")
    if not isinstance(segments, list):
        raise ValueError("Transcript must contain a segments array")

    issues_by_segment: dict[int, list[dict]] = {}
    if qa_report:
        for issue in qa_report.get("issues", []):
            index = issue.get("segment")
            if isinstance(index, int):
                issues_by_segment.setdefault(index, []).append(issue)

    draft_segments = []
    for index, segment in enumerate(segments):
        notes = []
        for issue in issues_by_segment.get(index, []):
            issue_type = issue.get("type", "unknown")
            if issue_type == "long_duration":
                notes.append(f"QA: long duration ({issue['duration']:.2f}s); verify or split")
            elif issue_type == "overlap":
                notes.append("QA: overlaps the previous segment; adjust timing")
            else:
                notes.append(f"QA: {issue_type.replace('_', ' ')}")
        draft_segments.append(
            {
                "id": f"seg-{index + 1:04d}",
                "start": segment["start"],
                "end": segment["end"],
                "text": segment["text"],
                "speaker": None,
                "voice": None,
                "notes": "; ".join(notes),
            }
        )

    return {
        "schema_version": 1,
        "project_id": project_id,
        "approval": {
            "status": "draft",
            "approved_by": None,
            "approved_at": None,
            "notes": (
                "Correct text, names, speakers, and timing. Resolve every QA note "
                "before changing status to approved."
            ),
        },
        "segments": draft_segments,
    }


def main() -> None:
    """Create an approval-draft JSON file from command-line paths."""
    parser = argparse.ArgumentParser(description="Create an English script review draft.")
    parser.add_argument("transcript", type=Path, help="Translated transcript JSON")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--qa-report", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()

    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    qa_report = None
    if args.qa_report:
        qa_report = json.loads(args.qa_report.read_text(encoding="utf-8"))
    draft = create_draft(args.project_id, transcript, qa_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    flagged = sum(bool(segment["notes"]) for segment in draft["segments"])
    print(
        f"Created approval draft: {args.output.resolve()} "
        f"({len(draft['segments'])} segments, {flagged} flagged)"
    )


if __name__ == "__main__":
    main()
