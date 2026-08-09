"""Automatically produce and approve a cleaned, timed English script."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
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
    task: str = "translate",
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
        task=task,
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
        "task": task,
        "output_language": "en" if task == "translate" else info.language,
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


def quality_metrics(transcript: dict, decisions: dict, notes: list[str]) -> dict:
    """Calculate deterministic confidence and timing metrics for one model pass."""
    segments = transcript["segments"]
    rejected = decisions["rejected_segments"]
    low_confidence = int(sum(bool(note) for note in notes))
    invalid_timing = int(sum(
        segment["end"] <= segment["start"] for segment in segments
    ))
    total_observations = len(segments) + len(rejected)
    return {
        "accepted_segments": int(len(segments)),
        "rejected_segments": int(len(rejected)),
        "low_confidence_segments": low_confidence,
        "invalid_timing_segments": invalid_timing,
        "low_confidence_ratio": low_confidence / len(segments) if segments else 1.0,
        "rejection_ratio": len(rejected) / total_observations if total_observations else 1.0,
        "score": int(low_confidence + (2 * len(rejected)) + (10 * invalid_timing)),
    }


def passes_gate(metrics: dict, maximum_low_confidence_ratio: float, maximum_rejection_ratio: float) -> bool:
    """Return whether automatic quality thresholds permit downstream use."""
    return (
        metrics["accepted_segments"] > 0
        and metrics["invalid_timing_segments"] == 0
        and metrics["low_confidence_ratio"] <= maximum_low_confidence_ratio
        and metrics["rejection_ratio"] <= maximum_rejection_ratio
    )


def run_candidate(
    input_path: Path,
    model_name: str,
    language: str | None,
    maximum_duration: float,
    maximum_chars: int,
    maximum_low_confidence_ratio: float,
    maximum_rejection_ratio: float,
) -> dict:
    """Run separate source and English passes and score them as one candidate."""
    source, source_decisions, source_notes = transcribe_and_decide(
        input_path, model_name, language, maximum_duration, maximum_chars, "transcribe"
    )
    english, english_decisions, english_notes = transcribe_and_decide(
        input_path, model_name, language, maximum_duration, maximum_chars, "translate"
    )
    source_metrics = quality_metrics(source, source_decisions, source_notes)
    english_metrics = quality_metrics(english, english_decisions, english_notes)
    passed = passes_gate(
        source_metrics, maximum_low_confidence_ratio, maximum_rejection_ratio
    ) and passes_gate(
        english_metrics, maximum_low_confidence_ratio, maximum_rejection_ratio
    )
    return {
        "model": model_name,
        "passed": passed,
        "score": source_metrics["score"] + english_metrics["score"],
        "source": source,
        "english": english,
        "english_notes": english_notes,
        "report": {
            "model": model_name,
            "passed": passed,
            "source_metrics": source_metrics,
            "english_metrics": english_metrics,
            "source_decisions": source_decisions,
            "english_decisions": english_decisions,
        },
    }


def make_approval(project_id: str, transcript: dict, notes: list[str], model_name: str) -> dict:
    """Build an automatically approved document from a gated transcript.

    Example::

        approved = make_approval(
            "episode-1",
            {"segments": [{"start": 0, "end": 1, "text": "Hello"}]},
            [""],
            "small",
        )
        assert approved["approval"]["status"] == "approved"
    """
    return {
        "schema_version": 1,
        "project_id": project_id,
        "approval": {
            "status": "approved",
            "approved_by": "automatic-quality-gate",
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "notes": f"Automatically selected and approved using model {model_name}.",
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
    """Run adaptive translation, repair, selection, and approval from the CLI."""
    parser = argparse.ArgumentParser(
        description="Automatically produce and approve a quality-gated English script."
    )
    parser.add_argument("input", type=Path, help="Source audio or video")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--model", default="small")
    parser.add_argument("--fallback-model")
    parser.add_argument("--language", help="Source language code")
    parser.add_argument("--maximum-duration", type=float, default=8.0)
    parser.add_argument("--maximum-characters", type=int, default=84)
    parser.add_argument("--maximum-low-confidence-ratio", type=float, default=0.2)
    parser.add_argument("--maximum-rejection-ratio", type=float, default=0.05)
    parser.add_argument("-o", "--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.input.is_file():
        raise FileNotFoundError(f"Input file not found: {args.input}")

    primary = run_candidate(
        args.input,
        args.model,
        args.language,
        args.maximum_duration,
        args.maximum_characters,
        args.maximum_low_confidence_ratio,
        args.maximum_rejection_ratio,
    )
    candidates = [primary]
    if not primary["passed"] and args.fallback_model:
        print(f"Primary quality gate failed; retrying with {args.fallback_model}")
        candidates.append(
            run_candidate(
                args.input,
                args.fallback_model,
                args.language,
                args.maximum_duration,
                args.maximum_characters,
                args.maximum_low_confidence_ratio,
                args.maximum_rejection_ratio,
            )
        )
    passing = [candidate for candidate in candidates if candidate["passed"]]
    if not passing:
        summary = ", ".join(
            f"{candidate['model']} score={candidate['score']}" for candidate in candidates
        )
        raise RuntimeError(f"No model candidate passed the automatic quality gate: {summary}")
    selected = min(passing, key=lambda candidate: candidate["score"])
    transcript = selected["english"]
    quality_notes = selected["english_notes"]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.input.stem
    transcript_path = args.output_dir / f"{stem}.auto.en.json"
    srt_path = args.output_dir / f"{stem}.auto.en.srt"
    decisions_path = args.output_dir / f"{stem}.decisions.json"
    source_path = args.output_dir / f"{stem}.source.json"
    approval_path = args.output_dir / f"{stem}.approved.json"

    approval = make_approval(args.project_id, transcript, quality_notes, selected["model"])
    decisions = {
        "automatic": True,
        "selected_model": selected["model"],
        "thresholds": {
            "maximum_low_confidence_ratio": args.maximum_low_confidence_ratio,
            "maximum_rejection_ratio": args.maximum_rejection_ratio,
        },
        "candidates": [candidate["report"] for candidate in candidates],
    }
    transcript_path.write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    decisions_path.write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    source_path.write_text(
        json.dumps(selected["source"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    approval_path.write_text(
        json.dumps(approval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_srt(srt_path, transcript["segments"])
    print(
        f"Automatically approved {len(transcript['segments'])} English segments "
        f"using {selected['model']}"
    )
    print(f"Approved script: {approval_path.resolve()}")


if __name__ == "__main__":
    main()
