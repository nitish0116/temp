"""Force-align a transcript to isolated vocals with a language-specific CTC model."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import librosa
import torch
from torchaudio.functional import forced_align, merge_tokens
from transformers import AutoModelForCTC, AutoProcessor

from segment_utterances import join_words, segment_words


ALIGNMENT_MODELS = {
    "en": "facebook/wav2vec2-base-960h",
    "fr": "facebook/wav2vec2-large-xlsr-53-french",
    "de": "facebook/wav2vec2-large-xlsr-53-german",
    "es": "facebook/wav2vec2-large-xlsr-53-spanish",
    "hi": "jonatasgrosman/wav2vec2-large-xlsr-53-hindi",
    "ja": "jonatasgrosman/wav2vec2-large-xlsr-53-japanese",
    "zh": "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn",
    "ar": "jonatasgrosman/wav2vec2-large-xlsr-53-arabic",
    "ko": "kresnik/wav2vec2-large-xlsr-korean",
}

LANGUAGE_ALIASES = {
    "ara": "ar", "arabic": "ar", "chi": "zh", "chinese": "zh", "cmn": "zh",
    "deu": "de", "english": "en", "fra": "fr", "french": "fr", "german": "de",
    "hin": "hi", "hindi": "hi", "japanese": "ja", "jpn": "ja", "kor": "ko",
    "korean": "ko", "spa": "es", "spanish": "es", "zho": "zh",
}


def normalize_language(language: str) -> str:
    """Return a two-letter routing code from a name, locale, or ISO-639 code."""
    value = language.strip().lower().replace("_", "-")
    base = value.split("-", 1)[0]
    return LANGUAGE_ALIASES.get(value, LANGUAGE_ALIASES.get(base, base))


def select_alignment_route(
    transcript: dict,
    model_name: str | None = None,
    minimum_language_probability: float = 0.5,
) -> dict:
    """Choose a CTC model or the safe Whisper-word fallback for a transcript."""
    language = normalize_language(str(transcript.get("language", "")))
    probability = transcript.get("language_probability")
    if model_name:
        return {"language": language, "mode": "ctc", "model": model_name, "reason": "explicit-model"}
    if probability is not None and float(probability) < minimum_language_probability:
        return {
            "language": language, "mode": "whisper", "model": None,
            "reason": "low-language-confidence",
        }
    selected = ALIGNMENT_MODELS.get(language)
    if selected:
        return {"language": language, "mode": "ctc", "model": selected, "reason": "language-route"}
    return {"language": language, "mode": "whisper", "model": None, "reason": "unsupported-language"}


def whisper_timestamp_alignment(segment: dict, reason: str) -> dict:
    """Preserve valid Whisper word times when CTC alignment cannot be trusted."""
    words = [
        {
            "word": str(word.get("word", "")).strip(),
            "start": round(float(word["start"]), 3),
            "end": round(float(word["end"]), 3),
            "score": round(float(word.get("probability", word.get("score", 0.0))), 4),
        }
        for word in segment.get("words", [])
        if str(word.get("word", "")).strip()
        and word.get("start") is not None
        and word.get("end") is not None
        and float(word["end"]) > float(word["start"])
    ]
    if not words:
        return {**segment, "alignment_status": "failed", "alignment_error": f"{reason}; no Whisper word timestamps"}
    return {
        **segment,
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "words": words,
        "alignment_status": "whisper-timestamps",
        "alignment_error": reason,
    }


def interval_overlap(start: float, end: float, intervals: list[tuple[float, float]]) -> float:
    """Return seconds of union overlap between one interval and sorted intervals."""
    return sum(max(0.0, min(end, right) - max(start, left)) for left, right in intervals)


def word_intervals(segments: list[dict]) -> list[tuple[float, float]]:
    """Merge aligned word spans into a non-overlapping interval list."""
    spans = sorted(
        (float(word["start"]), float(word["end"]))
        for segment in segments
        for word in segment.get("words", [])
        if word.get("start") is not None and word.get("end") is not None
    )
    merged: list[list[float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def reconciliation_candidates(reference: list[dict], aligned: list[dict], minimum_overlap: float = 0.01) -> list[dict]:
    """Return reference cues insufficiently covered by forced-aligned words."""
    intervals = word_intervals(aligned)
    missing = []
    for segment in reference:
        duration = float(segment["end"]) - float(segment["start"])
        ratio = interval_overlap(float(segment["start"]), float(segment["end"]), intervals) / duration if duration > 0 else 0.0
        if ratio < minimum_overlap:
            missing.append({**segment, "aligned_word_overlap_ratio": round(ratio, 4)})
    return missing


def split_aligned_words(segments: list[dict], maximum_duration: float = 8.0, maximum_characters: int = 84) -> list[dict]:
    """Build punctuation-, pause-, and speaker-aware cues from aligned words."""
    words = [
        {**word, **({"speaker": segment["speaker"]} if segment.get("speaker") else {})}
        for segment in segments
        for word in segment.get("words", [])
    ]
    groups = segment_words(words, maximum_duration, maximum_characters)
    return [
        {
            "start": round(float(group[0]["start"]), 3),
            "end": round(float(group[-1]["end"]), 3),
            "text": join_words(group),
            "words": group,
            **({"speaker": group[0]["speaker"]} if group[0].get("speaker") else {}),
            "provenance": "large-v3-forced-alignment",
        }
        for group in groups
    ]


def build_reconciled_transcript(transcript: dict, aligned: list[dict], retained: list[dict]) -> dict:
    """Combine aligned word cues with only acoustically uncovered reference cues."""
    canonical = split_aligned_words(aligned)
    for segment in retained:
        canonical.append(
            {
                "start": segment["start"],
                "end": segment["end"],
                "text": segment["text"],
                "words": [],
                "provenance": "retained-reference-no-word-overlap",
            }
        )
    canonical.sort(key=lambda segment: (segment["start"], segment["end"]))
    return {
        "language": transcript["language"],
        "language_probability": transcript.get("language_probability"),
        "task": "transcribe",
        "output_language": transcript["language"],
        "alignment_model": transcript.get("alignment_model"),
        "segments": canonical,
    }


def align_one(
    waveform: Any,
    sample_rate: int,
    segment: dict,
    processor: Any,
    model: Any,
    padding: float,
) -> dict:
    """CTC-align one rough Whisper segment and return acoustic word boundaries."""
    rough_start = max(0.0, float(segment["start"]) - padding)
    rough_end = min(len(waveform) / sample_rate, float(segment["end"]) + padding)
    audio = waveform[round(rough_start * sample_rate) : round(rough_end * sample_rate)]
    target = processor.tokenizer(segment["text"], add_special_tokens=False).input_ids
    unknown = processor.tokenizer.unk_token_id
    target = [token for token in target if token != unknown]
    if not target:
        return {**segment, "alignment_status": "failed", "alignment_error": "no supported CTC tokens"}
    inputs = processor(audio, sampling_rate=sample_rate, return_tensors="pt")
    with torch.inference_mode():
        emissions = model(inputs.input_values).logits.log_softmax(dim=-1)
    targets = torch.tensor([target], dtype=torch.int32)
    try:
        path, scores = forced_align(emissions, targets, blank=processor.tokenizer.pad_token_id)
    except RuntimeError as error:
        return {**segment, "alignment_status": "failed", "alignment_error": str(error)}
    spans = merge_tokens(path[0], scores[0], blank=processor.tokenizer.pad_token_id)
    if len(spans) != len(target):
        return {**segment, "alignment_status": "failed", "alignment_error": "CTC token count mismatch"}
    frame_seconds = (rough_end - rough_start) / emissions.shape[1]
    words = []
    cursor = 0
    for text_word in segment["text"].split():
        word_tokens = [
            token for token in processor.tokenizer(text_word, add_special_tokens=False).input_ids
            if token != unknown and token != processor.tokenizer.word_delimiter_token_id
        ]
        count = len(word_tokens)
        if not count or cursor + count > len(spans):
            continue
        selected = spans[cursor : cursor + count]
        words.append(
            {
                "word": text_word,
                "start": round(rough_start + selected[0].start * frame_seconds, 3),
                "end": round(rough_start + selected[-1].end * frame_seconds, 3),
                "score": round(math.exp(sum(float(span.score) for span in selected) / len(selected)), 4),
            }
        )
        cursor += count
        # The full-text tokenizer may include a word delimiter between words.
        if cursor < len(spans) and spans[cursor].token == processor.tokenizer.word_delimiter_token_id:
            cursor += 1
    if not words:
        return {**segment, "alignment_status": "failed", "alignment_error": "no aligned words"}
    return {
        **segment,
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "words": words,
        "alignment_status": "aligned",
        "alignment_error": None,
    }


def align_transcript(
    transcript: dict,
    reference: dict,
    audio_path: Path,
    model_name: str | None,
    padding: float,
    minimum_language_probability: float = 0.5,
) -> tuple[dict, dict, dict]:
    """Align all candidate cues and identify reference-only speech for reconciliation."""
    route = select_alignment_route(transcript, model_name, minimum_language_probability)
    language = route["language"]
    selected_model = route["model"]
    if route["mode"] == "ctc":
        try:
            waveform, sample_rate = librosa.load(audio_path, sr=16_000, mono=True)
            processor = AutoProcessor.from_pretrained(selected_model)
            model = AutoModelForCTC.from_pretrained(selected_model)
            model.eval()
            ctc_results = [
                align_one(waveform, sample_rate, segment, processor, model, padding)
                for segment in transcript["segments"]
            ]
            aligned = [
                result if result.get("alignment_status") == "aligned"
                else whisper_timestamp_alignment(source, result.get("alignment_error", "CTC alignment failed"))
                for source, result in zip(transcript["segments"], ctc_results)
            ]
        except (OSError, ValueError) as error:
            route = {**route, "mode": "whisper", "reason": "ctc-model-unavailable", "error": str(error)}
            aligned = [whisper_timestamp_alignment(segment, route["reason"]) for segment in transcript["segments"]]
    else:
        aligned = [whisper_timestamp_alignment(segment, route["reason"]) for segment in transcript["segments"]]
    passed = [segment for segment in aligned if segment.get("alignment_status") in {"aligned", "whisper-timestamps"}]
    retained = reconciliation_candidates(reference.get("segments", []), passed)
    alignment_name = selected_model if route["mode"] == "ctc" else "whisper-word-timestamps"
    result = {**transcript, "alignment_model": alignment_name, "alignment_route": route, "segments": aligned}
    reconciled = build_reconciled_transcript(result, passed, retained)
    scores = [word["score"] for segment in passed for word in segment.get("words", [])]
    report = {
        "schema_version": 1,
        "language": language,
        "alignment_mode": route["mode"],
        "alignment_model": alignment_name,
        "attempted_alignment_model": selected_model,
        "alignment_route_reason": route["reason"],
        "candidate_segments": len(aligned),
        "aligned_segments": len(passed),
        "failed_segments": len(aligned) - len(passed),
        "ctc_aligned_segments": sum(segment.get("alignment_status") == "aligned" for segment in aligned),
        "whisper_fallback_segments": sum(segment.get("alignment_status") == "whisper-timestamps" for segment in aligned),
        "aligned_words": sum(len(segment.get("words", [])) for segment in passed),
        "mean_word_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "reference_segments_requiring_reconciliation": retained,
        "reference_reconciliation_count": len(retained),
        "reconciled_segment_count": len(reconciled["segments"]),
    }
    return result, reconciled, report


def main() -> None:
    """Parse inputs, force-align the candidate, and write alignment artifacts."""
    parser = argparse.ArgumentParser(description="Force-align recovered speech to isolated vocals.")
    parser.add_argument("transcript", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--minimum-language-probability", type=float, default=0.5)
    parser.add_argument("--padding", type=float, default=0.75)
    parser.add_argument("--output-transcript", type=Path, required=True)
    parser.add_argument("--output-reconciled", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    args = parser.parse_args()
    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    aligned, reconciled, report = align_transcript(
        transcript, reference, args.audio, args.model, args.padding, args.minimum_language_probability
    )
    args.output_transcript.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_reconciled.parent.mkdir(parents=True, exist_ok=True)
    args.output_transcript.write_text(json.dumps(aligned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_reconciled.write_text(json.dumps(reconciled, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Aligned {report['aligned_segments']}/{report['candidate_segments']} segments ({report['aligned_words']} words)")
    print(f"Reference cues requiring reconciliation: {report['reference_reconciliation_count']}")


if __name__ == "__main__":
    main()
