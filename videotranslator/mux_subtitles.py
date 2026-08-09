"""Add an SRT file to a video as a selectable subtitle track."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def mux_subtitles(video: Path, subtitles: Path, output: Path) -> None:
    if not video.is_file():
        raise FileNotFoundError(f"Input video not found: {video}")
    if not subtitles.is_file():
        raise FileNotFoundError(f"Subtitle file not found: {subtitles}")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("FFmpeg is not installed or is not available on PATH")
    if video.resolve() == output.resolve():
        raise ValueError("Output path must be different from the input video")

    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video),
        "-i",
        str(subtitles),
        "-map",
        "0",
        "-map",
        "1:0",
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-c:s",
        "mov_text",
        "-metadata:s:s:0",
        "language=eng",
        "-metadata:s:s:0",
        "title=English",
        str(output),
    ]
    subprocess.run(command, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add SRT subtitles to an MP4 without re-encoding video or audio."
    )
    parser.add_argument("video", type=Path, help="Source video")
    parser.add_argument("subtitles", type=Path, help="SRT subtitle file")
    parser.add_argument("-o", "--output", type=Path, help="Output MP4 path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output or Path("outputs/subtitled") / (
        f"{args.video.stem}.english-subs.mp4"
    )
    mux_subtitles(args.video, args.subtitles, output)
    print(f"Created subtitled video: {output.resolve()}")


if __name__ == "__main__":
    main()
