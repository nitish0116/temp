"""Prepare a seeded accepted-group reliability audit with hashed audio clips."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from .bounded_review import attach_audio_clips, build_reliability_audit, sha256_file


def main(argv: list[str] | None = None) -> None:
    """Build and persist one episode stratum of the reliability audit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    parser.add_argument("adjudication_report", type=Path)
    parser.add_argument("source_media", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--sample-size", type=int, required=True)
    parser.add_argument("--selection-seed", required=True)
    parser.add_argument("--adjudication-model", default="qwen3:14b")
    parser.add_argument("--minimum-accuracy", type=float, default=0.95)
    parser.add_argument("--confidence", type=float, default=0.95)
    args = parser.parse_args(argv)

    document = json.loads(args.document.read_text(encoding="utf-8"))
    report = json.loads(args.adjudication_report.read_text(encoding="utf-8"))
    media_hash = sha256_file(args.source_media)
    audit = build_reliability_audit(
        document, report, sample_id=args.sample_id,
        source_media=args.source_media.as_posix(), media_sha256=media_hash,
        adjudication_model=args.adjudication_model,
        sample_size=args.sample_size, selection_seed=args.selection_seed,
        minimum_accuracy=args.minimum_accuracy, confidence=args.confidence,
    )
    clips = args.output_directory / "clips"

    def extract(start: float, end: float, destination: Path) -> None:
        """Extract one padded mono review clip with FFmpeg."""
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", str(start), "-to", str(end), "-i", str(args.source_media),
            "-vn", "-ac", "1", "-ar", "16000", str(destination),
        ], check=True)

    attach_audio_clips(
        audit, clips, (args.output_directory / "clips").as_posix(), extract,
    )
    args.output_directory.mkdir(parents=True, exist_ok=True)
    output = args.output_directory / "audit.json"
    output.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({
        "audit": str(output), "selected": audit["review_item_count"],
        "required_total": audit["statistical_target"]["required_total_sample_size"],
    }, indent=2))


if __name__ == "__main__":
    main()
