"""Extract transcription-ready audio from a video using FFmpeg."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def extract_audio(input_path: Path, output_path: Path) -> None:
    if not input_path.is_file():
        raise FileNotFoundError(f"Input video not found: {input_path}")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("FFmpeg is not installed or is not available on PATH")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    subprocess.run(command, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract mono 16 kHz WAV audio from a video."
    )
    parser.add_argument("input", type=Path, help="Path to the source video")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output WAV path (default: outputs/audio/<video name>.wav)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output or Path("outputs/audio") / f"{args.input.stem}.wav"
    extract_audio(args.input, output)
    print(f"Audio extracted to: {output.resolve()}")


if __name__ == "__main__":
    main()
