"""Recover dialogue from diarized vocal regions absent from the canonical transcript."""

from __future__ import annotations


import argparse
import json
import re
from pathlib import Path
from typing import Iterable

import librosa
import numpy as np
from faster_whisper import WhisperModel


Interval = tuple[float, float]


def merge_intervals(intervals: Iterable[Interval], maximum_gap: float = 0.03) -> list[Interval]:
    """Return sorted union intervals, joining boundaries separated by a small gap."""
    merged: list[list[float]] = []
    for start, end in sorted((float(start), float(end)) for start, end in intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + maximum_gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def subtract_intervals(source: Iterable[Interval], covered: Iterable[Interval]) -> list[Interval]:
    """Subtract covered cue spans from independent speech intervals."""
    covered_union = merge_intervals(covered)
    remaining = []
    for start, end in merge_intervals(source):
        pieces = [(start, end)]
        for left, right in covered_union:
            if right <= start:
                continue
            if left >= end:
                break
            next_pieces = []
            for piece_start, piece_end in pieces:
                if right <= piece_start or left >= piece_end:
                    next_pieces.append((piece_start, piece_end))
                    continue
                if piece_start < left:
                    next_pieces.append((piece_start, left))
                if right < piece_end:
                    next_pieces.append((right, piece_end))
            pieces = next_pieces
        remaining.extend(pieces)
    return remaining


def recovery_regions(
    turns: list[dict],
    cues: list[dict],
    minimum_duration: float = 0.25,
    merge_gap: float = 0.0,
) -> list[Interval]:
    """Find material diarized speech outside cue spans and merge nearby fragments."""
    speech = [(turn["start"], turn["end"]) for turn in turns]
    covered = [(cue["start"], cue["end"]) for cue in cues]
    missing = [
        interval
        for interval in subtract_intervals(speech, covered)
        if interval[1] - interval[0] >= minimum_duration
    ]
    return merge_intervals(missing, maximum_gap=merge_gap)


def normalized_text(text: str) -> str:
    """Normalize Unicode dialogue for nearby duplicate suppression."""
    return re.sub(r"\W+", "", text, flags=re.UNICODE).casefold()


def is_confident(segment: object, minimum_log_probability: float, maximum_no_speech: float) -> bool:
    """Accept a targeted Whisper result only when decoder evidence is speech-like."""
    return bool(
        getattr(segment, "text", "").strip()
        and float(getattr(segment, "avg_logprob", -99.0)) >= minimum_log_probability
        and float(getattr(segment, "no_speech_prob", 1.0)) <= maximum_no_speech
        and float(getattr(segment, "compression_ratio", 99.0)) <= 2.4
    )


def words_inside_region(segment: object, region_start: float, region_end: float, audio_offset: float) -> list[dict]:
    """Keep targeted Whisper words whose absolute centers fall in the missing span."""
    words = []
    for word in getattr(segment, "words", None) or []:
        start = audio_offset + float(word.start)
        end = audio_offset + float(word.end)
        if region_start <= (start + end) / 2 <= region_end:
            words.append({"start": round(start, 3), "end": round(end, 3), "word": word.word})
    return words


def transcribe_regions(
    waveform: object,
    sample_rate: int,
    regions: list[Interval],
    model: WhisperModel,
    language: str | None,
    padding: float,
    minimum_log_probability: float,
    maximum_no_speech: float,
) -> tuple[list[dict], list[dict]]:
    """Decode batched uncovered regions without VAD and map words to source time."""
    duration = len(waveform) / sample_rate
    separator = np.zeros(round(sample_rate * 0.5), dtype=np.float32)
    chunks = []
    mappings = []
    batch_cursor = 0.0
    attempts = [
        {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "decoded": [],
            "accepted": [],
        }
        for start, end in regions
    ]
    for index, (region_start, region_end) in enumerate(regions):
        audio_start = max(0.0, region_start - padding)
        audio_end = min(duration, region_end + padding)
        audio = waveform[round(audio_start * sample_rate) : round(audio_end * sample_rate)]
        chunk_duration = len(audio) / sample_rate
        mappings.append(
            {
                "index": index,
                "batch_start": batch_cursor,
                "batch_end": batch_cursor + chunk_duration,
                "audio_start": audio_start,
                "region_start": region_start,
                "region_end": region_end,
            }
        )
        chunks.extend((audio, separator))
        batch_cursor += chunk_duration + len(separator) / sample_rate
    if not chunks:
        return [], attempts
    batch = np.concatenate(chunks)
    iterator, _info = model.transcribe(
        batch,
        language=language,
        task="transcribe",
        beam_size=5,
        vad_filter=False,
        word_timestamps=True,
        condition_on_previous_text=False,
        no_speech_threshold=maximum_no_speech,
    )
    recovered = []
    for segment in iterator:
        confident = is_confident(segment, minimum_log_probability, maximum_no_speech)
        words_by_region: dict[int, list[dict]] = {}
        for word in getattr(segment, "words", None) or []:
            center = (float(word.start) + float(word.end)) / 2
            mapping = next(
                (item for item in mappings if item["batch_start"] <= center <= item["batch_end"]),
                None,
            )
            if mapping is None:
                continue
            absolute_start = mapping["audio_start"] + float(word.start) - mapping["batch_start"]
            absolute_end = mapping["audio_start"] + float(word.end) - mapping["batch_start"]
            absolute_center = (absolute_start + absolute_end) / 2
            if mapping["region_start"] <= absolute_center <= mapping["region_end"]:
                words_by_region.setdefault(mapping["index"], []).append(
                    {
                        "start": round(absolute_start, 3),
                        "end": round(absolute_end, 3),
                        "word": word.word,
                    }
                )
        for index, words in words_by_region.items():
            attempts[index]["decoded"].append(segment.text.strip())
            if not confident:
                continue
            text = " ".join(word["word"].strip() for word in words).strip()
            if not text:
                continue
            region_start, region_end = regions[index]
            cue_start = max(words[0]["start"], round(region_start, 3))
            cue_end = min(words[-1]["end"], round(region_end, 3))
            if cue_end <= cue_start:
                continue
            recovered.append(
                {
                    "start": cue_start,
                    "end": cue_end,
                    "text": text,
                    "words": words,
                    "provenance": "targeted-large-v3-diarization-recovery",
                    "recovery_region": {
                        "start": round(region_start, 3),
                        "end": round(region_end, 3),
                        "average_log_probability": round(float(segment.avg_logprob), 4),
                        "no_speech_probability": round(float(segment.no_speech_prob), 4),
                    },
                }
            )
            attempts[index]["accepted"].append(text)
    return recovered, attempts


def merge_recovered(canonical: dict, recovered: list[dict], duplicate_window: float = 1.0) -> tuple[dict, list[dict]]:
    """Insert nonduplicate recovered cues into a new chronological transcript."""
    existing = json.loads(json.dumps(canonical["segments"]))
    promoted = []
    for candidate in recovered:
        normalized = normalized_text(candidate["text"])
        duplicate = next(
            (
                segment
                for segment in existing
                if normalized
                and (
                    normalized == normalized_text(segment["text"])
                    or normalized in normalized_text(segment["text"])
                    or normalized_text(segment["text"]) in normalized
                )
                and abs(float(candidate["start"]) - float(segment["start"])) <= duplicate_window
            ),
            None,
        )
        if duplicate is not None:
            previous = {
                "start": duplicate["start"],
                "end": duplicate["end"],
                "text": duplicate["text"],
                "provenance": duplicate.get("provenance"),
            }
            duplicate["start"] = min(float(duplicate["start"]), float(candidate["start"]))
            duplicate["end"] = max(float(duplicate["end"]), float(candidate["end"]))
            if len(normalized) > len(normalized_text(duplicate["text"])):
                duplicate["text"] = candidate["text"]
                duplicate["words"] = candidate.get("words", duplicate.get("words", []))
            duplicate.setdefault("merged_recovery_evidence", []).extend(
                [previous, {"start": candidate["start"], "end": candidate["end"], "text": candidate["text"], "provenance": candidate.get("provenance")}]
            )
        else:
            promoted.append(candidate)
    segments = [*existing, *promoted]
    segments.sort(key=lambda segment: (float(segment["start"]), float(segment["end"])))
    return {**canonical, "segments": segments}, promoted


def recover_uncovered_words(
    strong_transcript: dict,
    canonical: dict,
    minimum_overlap: float = 0.03,
    maximum_word_gap: float = 0.8,
) -> list[dict]:
    """Group every strong-ASR word still absent from the canonical cue timeline."""
    cue_intervals = [
        (float(segment["start"]), float(segment["end"]))
        for segment in canonical["segments"]
    ]
    missing_words = []
    for segment in strong_transcript.get("segments", []):
        for word in segment.get("words", []):
            start, end = float(word["start"]), float(word["end"])
            overlap = sum(
                max(0.0, min(end, right) - max(start, left))
                for left, right in cue_intervals
            )
            center = (start + end) / 2
            covered_by_boundary = any(
                left - 0.1 <= center <= right + 0.1 for left, right in cue_intervals
            )
            if overlap < minimum_overlap and not covered_by_boundary:
                missing_words.append(
                    {"start": start, "end": max(end, start + 0.12), "word": word["word"]}
                )
    missing_words.sort(key=lambda word: word["start"])
    groups: list[list[dict]] = []
    for word in missing_words:
        gap_has_cue = bool(groups) and any(
            left < word["start"] and right > groups[-1][-1]["end"]
            for left, right in cue_intervals
        )
        if (
            groups
            and word["start"] - groups[-1][-1]["end"] <= maximum_word_gap
            and not gap_has_cue
        ):
            groups[-1].append(word)
        else:
            groups.append([word])
    recovered = []
    for group in groups:
        if not any(word["word"].strip() for word in group):
            continue
        start, end = group[0]["start"], group[-1]["end"]
        for left, right in cue_intervals:
            if left <= start < right:
                start = right
            if left < end <= right:
                end = left
            if start < left and right < end:
                center = (start + end) / 2
                if center >= right:
                    start = right
                else:
                    end = left
        if end <= start:
            continue
        recovered.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "text": " ".join(word["word"].strip() for word in group).strip(),
            "words": group,
            "provenance": "retained-large-v3-word-coverage",
        })
    return recovered


def main() -> None:
    """Recover uncovered speech and write candidate, promoted transcript, and report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("canonical", type=Path)
    parser.add_argument("diarization_report", type=Path)
    parser.add_argument("vocals", type=Path)
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--strong-transcript", type=Path)
    parser.add_argument("--language")
    parser.add_argument("--minimum-duration", type=float, default=0.25)
    parser.add_argument(
        "--merge-gap",
        type=float,
        default=0.0,
        help="Join only contiguous uncovered spans by default; positive values may bridge cues",
    )
    parser.add_argument("--padding", type=float, default=0.35)
    parser.add_argument("--minimum-log-probability", type=float, default=-1.0)
    parser.add_argument("--maximum-no-speech", type=float, default=0.65)
    parser.add_argument("--output-transcript", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    args = parser.parse_args()
    canonical = json.loads(args.canonical.read_text(encoding="utf-8"))
    diarization = json.loads(args.diarization_report.read_text(encoding="utf-8"))
    regions = recovery_regions(
        diarization["turns"],
        canonical["segments"],
        args.minimum_duration,
        args.merge_gap,
    )
    waveform, sample_rate = librosa.load(args.vocals, sr=16_000, mono=True)
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    recovered, attempts = transcribe_regions(
        waveform,
        sample_rate,
        regions,
        model,
        args.language or canonical.get("language"),
        args.padding,
        args.minimum_log_probability,
        args.maximum_no_speech,
    )
    transcript, promoted = merge_recovered(canonical, recovered)
    retained_words = []
    if args.strong_transcript:
        strong = json.loads(args.strong_transcript.read_text(encoding="utf-8"))
        retained_words = recover_uncovered_words(strong, transcript)
        transcript, retained_words = merge_recovered(transcript, retained_words)
    report = {
        "schema_version": 1,
        "automatic": True,
        "model": args.model,
        "region_count": len(regions),
        "uncovered_seconds": round(sum(end - start for start, end in regions), 3),
        "candidate_count": len(recovered),
        "promoted_count": len(promoted),
        "retained_word_cue_count": len(retained_words),
        "canonical_segment_count": len(canonical["segments"]),
        "output_segment_count": len(transcript["segments"]),
        "attempts": attempts,
    }
    args.output_transcript.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_transcript.write_text(json.dumps(transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Recovered {report['promoted_count']} cues from {report['region_count']} "
        f"uncovered speech regions ({report['uncovered_seconds']:.1f}s)"
    )


if __name__ == "__main__":
    main()
