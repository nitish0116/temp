"""Automatically create a cleaned, timed English approval draft from a video."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

from faster_whisper import WhisperModel

from transcribe import srt_timestamp


def split_words(words: list[dict], maximum_duration: float, maximum_chars: int) -> list[list[dict]]:
    """Split words at pauses, duration limits, or readable subtitle lengths.

    Example::

        words = [
            {"start": 0.0, "end": 0.4, "word": "Hello"},
            {"start": 1.6, "end": 2.0, "word": " again"},
        ]
        assert len(split_words(words, 8.0, 84)) == 2
    """
    chunks: list[list[dict]] = []
    current: list[dict] = []
    for word in words:
        if current:
            duration = word["end"] - current[0]["start"]
            characters = len("".join(item["word"] for item in current + [word]).strip())
            pause = word["start"] - current[-1]["end"]
            if pause >= 1.0 or duration > maximum_duration or characters > maximum_chars:
                chunks.append(current)
                current = []
        current.append(word)
    if current:
        chunks.append(current)
    return chunks


def transcribe_and_decide(
    input_path: Path,
    model_name: str,
    language: str | None,
    maximum_duration: float,
    maximum_chars: int,
) -> tuple[dict, dict, list[str]]:
    """Translate media and make timing, silence, and confidence decisions.

    Returns a transcript, an audit trail of automated decisions, and one review
    note per accepted cue. The model uses word timestamps and VAD so subtitle
    boundaries follow actual speech instead of spanning silence.
    """
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    iterator, info = model.transcribe(
        str(input_path),
        language=language,
        task="translate",
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        word_timestamps=True,
        condition_on_previous_text=False,
        hallucination_silence_threshold=2.0,
    )

    cleaned = []
    rejected = []
    for source_index, segment in enumerate(iterator):
        text = segment.text.strip()
        words = [
            {"start": word.start, "end": word.end, "word": word.word}
            for word in (segment.words or [])
            if (
                word.start is not None
                and word.end is not None
                and word.end > word.start
                and word.word.strip()
            )
        ]
        likely_silence = segment.no_speech_prob >= 0.8 and segment.avg_logprob < -1.0
        if not text or not words or likely_silence:
            rejected.append(
                {
                    "source_segment": source_index,
                    "start": segment.start,
                    "end": segment.end,
                    "text": text,
                    "reason": "likely_silence" if likely_silence else "missing_words",
                }
            )
            continue

        for chunk in split_words(words, maximum_duration, maximum_chars):
            chunk_text = "".join(item["word"] for item in chunk).strip()
            notes = []
            if segment.avg_logprob < -0.8:
                notes.append(f"Low recognition confidence ({segment.avg_logprob:.2f})")
            cleaned.append(
                {
                    "start": round(chunk[0]["start"], 3),
                    "end": round(chunk[-1]["end"], 3),
                    "text": chunk_text,
                    "notes": "; ".join(notes),
                }
            )

    transcript = {
        "language": info.language,
        "language_probability": info.language_probability,
        "task": "translate",
        "output_language": "en",
        "segments": [
            {"start": item["start"], "end": item["end"], "text": item["text"]}
            for item in cleaned
        ],
    }
    decisions = {
        "model": model_name,
        "source_language": info.language,
        "rules": {
            "maximum_duration": maximum_duration,
            "maximum_characters": maximum_chars,
            "silence_probability_threshold": 0.8,
            "low_confidence_threshold": -0.8,
        },
        "accepted_segments": len(cleaned),
        "rejected_segments": rejected,
    }
    return transcript, decisions, [item["notes"] for item in cleaned]


def make_approval(project_id: str, transcript: dict, notes: list[str]) -> dict:
    """Build a draft approval document from cleaned transcript segments.

    Example::

        draft = make_approval(
            "episode-1",
            {"segments": [{"start": 0, "end": 1, "text": "Hello"}]},
            [""],
        )
        assert draft["approval"]["status"] == "draft"
    """
    return {
        "schema_version": 1,
        "project_id": project_id,
        "approval": {
            "status": "draft",
            "approved_by": None,
            "approved_at": None,
            "notes": "Automatically timed with word-level speech boundaries.",
        },
        "segments": [
            {
                "id": f"seg-{index:04d}",
                "start": segment["start"],
                "end": segment["end"],
                "text": segment["text"],
                "speaker": None,
                "voice": None,
                "notes": notes[index - 1],
            }
            for index, segment in enumerate(transcript["segments"], start=1)
        ],
    }


def write_srt(path: Path, segments: list[dict]) -> None:
    """Serialize timed segment dictionaries as a UTF-8 SRT file."""
    content = "\n\n".join(
        f"{index}\n{srt_timestamp(item['start'])} --> {srt_timestamp(item['end'])}\n{item['text']}"
        for index, item in enumerate(segments, start=1)
    )
    path.write_text(content + "\n", encoding="utf-8")


def main() -> None:
    """Run automatic translation, repair, and draft generation from the CLI."""
    parser = argparse.ArgumentParser(
        description="Automatically repair timing and create an English approval draft."
    )
    parser.add_argument("input", type=Path, help="Source audio or video")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--model", default="small")
    parser.add_argument("--language", help="Source language code")
    parser.add_argument("--maximum-duration", type=float, default=8.0)
    parser.add_argument("--maximum-characters", type=int, default=84)
    parser.add_argument("-o", "--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.input.is_file():
        raise FileNotFoundError(f"Input file not found: {args.input}")

    transcript, decisions, quality_notes = transcribe_and_decide(
        args.input,
        args.model,
        args.language,
        args.maximum_duration,
        args.maximum_characters,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.input.stem
    transcript_path = args.output_dir / f"{stem}.auto.en.json"
    srt_path = args.output_dir / f"{stem}.auto.en.srt"
    decisions_path = args.output_dir / f"{stem}.decisions.json"
    approval_path = args.output_dir / f"{stem}.approval-draft.json"

    approval = make_approval(args.project_id, transcript, quality_notes)
    transcript_path.write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    decisions_path.write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    approval_path.write_text(
        json.dumps(approval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_srt(srt_path, transcript["segments"])
    print(
        f"Created {len(transcript['segments'])} automatically timed segments; "
        f"rejected {len(decisions['rejected_segments'])} likely invalid segments"
    )
    print(f"Approval draft: {approval_path.resolve()}")


if __name__ == "__main__":
    main()
