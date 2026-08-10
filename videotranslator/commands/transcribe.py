"""Transcribe audio into plain text, JSON, and SRT subtitles."""

from __future__ import annotations


import argparse
import json
from pathlib import Path

try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    # Public Python installs generally work with certifi; truststore is needed on
    # machines whose proxy or corporate CA is registered only with the OS.
    pass

from faster_whisper import WhisperModel


def srt_timestamp(seconds: float) -> str:
    """Convert seconds to the ``HH:MM:SS,mmm`` format required by SRT.

    Example::

        >>> srt_timestamp(65.25)
        '00:01:05,250'
    """
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def parse_args() -> argparse.Namespace:
    """Parse transcription model, language, task, and output options."""
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
    parser.add_argument("--vad-threshold", type=float, default=0.5)
    parser.add_argument("--minimum-speech-ms", type=int, default=250)
    parser.add_argument("--minimum-silence-ms", type=int, default=500)
    parser.add_argument("--speech-padding-ms", type=int, default=200)
    parser.add_argument("--no-speech-threshold", type=float, default=0.6)
    return parser.parse_args()


def main() -> None:
    """Transcribe or translate one media file and write TXT, JSON, and SRT."""
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
        vad_parameters={
            "threshold": args.vad_threshold,
            "min_speech_duration_ms": args.minimum_speech_ms,
            "min_silence_duration_ms": args.minimum_silence_ms,
            "speech_pad_ms": args.speech_padding_ms,
        },
        no_speech_threshold=args.no_speech_threshold,
        word_timestamps=True,
        condition_on_previous_text=False,
    )

    segments = [
        {
            "start": segment.start,
            "end": segment.end,
            "text": segment.text.strip(),
            "words": [
                {"start": word.start, "end": word.end, "word": word.word}
                for word in (segment.words or [])
                if word.start is not None and word.end is not None
            ],
        }
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
