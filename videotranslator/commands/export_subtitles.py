"""Export validated canonical timed text to SRT and ASS subtitle formats."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

try:
    from .canonical_timed_text import validate_canonical_timed_text
    from .transcribe import srt_timestamp
except ImportError:
    from canonical_timed_text import validate_canonical_timed_text
    from transcribe import srt_timestamp


def display_text(cue: dict) -> str:
    return str(cue.get("translated_text") or cue.get("source_text") or "").strip()


def validate_export_source(document: dict) -> None:
    """Block export of invalid, empty, unordered, or overlapping canonical cues."""
    validate_canonical_timed_text(document)
    previous_end = 0.0
    for cue in document["segments"]:
        if not display_text(cue):
            raise ValueError(f"Cue {cue['id']} has no display text")
        if float(cue["start"]) < previous_end:
            raise ValueError(f"Cue {cue['id']} overlaps the preceding cue")
        previous_end = float(cue["end"])


def ass_timestamp(seconds: float) -> str:
    centiseconds = round(float(seconds) * 100)
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    whole_seconds, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{fraction:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def srt_content(document: dict) -> str:
    validate_export_source(document)
    return "\n\n".join(
        f"{index}\n{srt_timestamp(cue['start'])} --> {srt_timestamp(cue['end'])}\n{display_text(cue)}"
        for index, cue in enumerate(document["segments"], start=1)
    ) + "\n"


def ass_content(document: dict) -> str:
    validate_export_source(document)
    speakers = sorted({cue["speaker"] for cue in document["segments"]})
    styles = [
        "[Script Info]", "ScriptType: v4.00+", "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
    ]
    for speaker in speakers:
        style = re.sub(r"[^A-Za-z0-9_-]", "_", speaker) or "unknown"
        styles.append(f"Style: {style},Arial,42,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,40,40,30,1")
    styles.extend(["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"])
    for cue in document["segments"]:
        style = re.sub(r"[^A-Za-z0-9_-]", "_", cue["speaker"]) or "unknown"
        styles.append(
            f"Dialogue: 0,{ass_timestamp(cue['start'])},{ass_timestamp(cue['end'])},{style},{cue['speaker']},0,0,0,,{ass_escape(display_text(cue))}"
        )
    return "\n".join(styles) + "\n"


def export_subtitles(document: dict, srt_path: Path | None, ass_path: Path | None) -> dict:
    """Write requested exports and return an auditable manifest."""
    if srt_path is None and ass_path is None:
        raise ValueError("At least one subtitle export path is required")
    validate_export_source(document)
    outputs = {}
    if srt_path is not None:
        srt_path.parent.mkdir(parents=True, exist_ok=True)
        srt_path.write_text(srt_content(document), encoding="utf-8")
        outputs["srt"] = str(srt_path.resolve())
    if ass_path is not None:
        ass_path.parent.mkdir(parents=True, exist_ok=True)
        ass_path.write_text(ass_content(document), encoding="utf-8")
        outputs["ass"] = str(ass_path.resolve())
    return {
        "schema_version": 1, "source_schema_version": document["schema_version"],
        "cue_count": len(document["segments"]), "outputs": outputs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--srt", type=Path)
    parser.add_argument("--ass", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    document = json.loads(args.source.read_text(encoding="utf-8"))
    report = export_subtitles(document, args.srt, args.ass)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {report['cue_count']} validated subtitle cues")


if __name__ == "__main__":
    main()
