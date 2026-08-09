"""Burn subtitles at the top of a video for visual timing review."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def escape_filter_path(path: Path) -> str:
    """Escape an absolute Windows path for FFmpeg's subtitles filter."""
    value = path.resolve().as_posix()
    return value.replace("'", r"\'").replace(":", r"\:")


def burn_subtitles(video: Path, subtitles: Path, output: Path) -> None:
    if not video.is_file():
        raise FileNotFoundError(f"Input video not found: {video}")
    if not subtitles.is_file():
        raise FileNotFoundError(f"Subtitle file not found: {subtitles}")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("FFmpeg is not installed or is not available on PATH")
    if video.resolve() == output.resolve():
        raise ValueError("Output path must be different from the input video")

    output.parent.mkdir(parents=True, exist_ok=True)
    subtitle_filter = (
        f"subtitles=filename='{escape_filter_path(subtitles)}':"
        "force_style='Alignment=6,MarginV=24,FontSize=22,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        "BorderStyle=1,Outline=2,Shadow=1'"
    )
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video),
        "-vf",
        subtitle_filter,
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output),
    ]
    subprocess.run(command, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Burn an SRT at the top of a video for timing review."
    )
    parser.add_argument("video", type=Path, help="Source video")
    parser.add_argument("subtitles", type=Path, help="SRT subtitle file")
    parser.add_argument("-o", "--output", type=Path, help="Output MP4 path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output or Path("outputs/review") / f"{args.video.stem}.top-subs.mp4"
    burn_subtitles(args.video, args.subtitles, output)
    print(f"Created top-subtitle review video: {output.resolve()}")


if __name__ == "__main__":
    main()
