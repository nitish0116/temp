"""Transcribe audio into plain text, JSON, and SRT subtitles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from faster_whisper import WhisperModel


def srt_timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe audio and create TXT, JSON, and SRT files."
    )
    parser.add_argument("input", type=Path, help="Audio or video file to transcribe")
    parser.add_argument("--model", default="small", help="Whisper model (default: small)")
    parser.add_argument("--language", help="Spoken language code; omit for auto-detection")
    parser.add_argument(
        "--task",
        choices=("transcribe", "translate"),
        default="transcribe",
        help="Keep the original language or translate speech to English",
    )
    parser.add_argument(
        "-o", "--output-dir", type=Path, default=Path("outputs/transcripts")
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.input.is_file():
        raise FileNotFoundError(f"Input file not found: {args.input}")

    print(f"Loading Whisper model: {args.model}")
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    segments_iterator, info = model.transcribe(
        str(args.input),
        language=args.language,
        task=args.task,
        beam_size=5,
        vad_filter=True,
    )

    segments = [
        {"start": segment.start, "end": segment.end, "text": segment.text.strip()}
        for segment in segments_iterator
        if segment.text.strip()
    ]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{args.input.stem}.en" if args.task == "translate" else args.input.stem
    txt_path = args.output_dir / f"{stem}.txt"
    json_path = args.output_dir / f"{stem}.json"
    srt_path = args.output_dir / f"{stem}.srt"

    txt_path.write_text("\n".join(item["text"] for item in segments) + "\n", encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "language": info.language,
                "language_probability": info.language_probability,
                "task": args.task,
                "output_language": "en" if args.task == "translate" else info.language,
                "segments": segments,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    srt_path.write_text(
        "\n\n".join(
            f"{index}\n{srt_timestamp(item['start'])} --> {srt_timestamp(item['end'])}\n{item['text']}"
            for index, item in enumerate(segments, start=1)
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"Detected language: {info.language} "
        f"({info.language_probability:.1%} confidence)"
    )
    print(f"Created: {txt_path}, {json_path}, {srt_path}")


if __name__ == "__main__":
    main()
