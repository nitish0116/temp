"""Translate canonical cues under voice-calibrated speaking-duration constraints."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

from auto_prepare_script import clean_translation_repetition, nllb_code
from generate_dub import media_duration
from match_speaker_voices import PROBE_TEXT


def available_windows(segments: list[dict], maximum_extension: float = 0.75) -> list[float]:
    """Return cue durations plus bounded trailing silence before the next cue."""
    windows = []
    for index, segment in enumerate(segments):
        duration = float(segment["end"]) - float(segment["start"])
        next_start = (
            float(segments[index + 1]["start"])
            if index + 1 < len(segments)
            else float(segment["end"])
        )
        trailing_silence = max(0.0, next_start - float(segment["end"]))
        windows.append(duration + min(maximum_extension, trailing_silence))
    return windows


def deduplicate_adjacent_cues(segments: list[dict], maximum_gap: float = 0.5) -> list[dict]:
    """Merge same-speaker adjacent fragments when normalized text contains the other."""
    merged: list[dict] = []
    for original in segments:
        segment = json.loads(json.dumps(original))
        normalized = re.sub(r"\W+", "", segment.get("text", ""), flags=re.UNICODE).casefold()
        if merged:
            previous = merged[-1]
            previous_normalized = re.sub(
                r"\W+", "", previous.get("text", ""), flags=re.UNICODE
            ).casefold()
            gap = float(segment["start"]) - float(previous["end"])
            same_speaker = segment.get("speaker") == previous.get("speaker")
            contained = normalized and previous_normalized and (
                normalized in previous_normalized or previous_normalized in normalized
            )
            if same_speaker and gap <= maximum_gap and contained:
                longer = segment if len(normalized) > len(previous_normalized) else previous
                longer["start"] = min(float(previous["start"]), float(segment["start"]))
                longer["end"] = max(float(previous["end"]), float(segment["end"]))
                longer["merged_duplicate_cues"] = [
                    *previous.get("merged_duplicate_cues", [
                        {"start": previous["start"], "end": previous["end"], "text": previous["text"]}
                    ]),
                    {"start": segment["start"], "end": segment["end"], "text": segment["text"]},
                ]
                merged[-1] = longer
                continue
        merged.append(segment)
    repaired: list[dict] = []
    index = 0
    while index < len(merged):
        segment = merged[index]
        duration = float(segment["end"]) - float(segment["start"])
        following = merged[index + 1] if index + 1 < len(merged) else None
        if (
            duration < 0.1
            and following is not None
            and segment.get("speaker") == following.get("speaker")
            and float(following["start"]) - float(segment["end"]) <= 0.1
        ):
            combined = json.loads(json.dumps(following))
            combined["start"] = segment["start"]
            combined["text"] = f"{segment['text'].strip()} {following['text'].strip()}".strip()
            combined["merged_short_cues"] = [
                {"start": segment["start"], "end": segment["end"], "text": segment["text"]},
                {"start": following["start"], "end": following["end"], "text": following["text"]},
            ]
            repaired.append(combined)
            index += 2
            continue
        repaired.append(segment)
        index += 1
    return repaired


def voice_rates(
    voices: set[str], probe_dir: Path, probe_text: str, default_rate: float
) -> dict[str, float]:
    """Estimate spoken characters per second from cached neutral voice probes."""
    rates = {}
    for voice in voices:
        path = probe_dir / f"{voice}.wav"
        if path.is_file():
            duration = media_duration(path)
            rates[voice] = len(probe_text) / duration if duration > 0 else default_rate
        else:
            rates[voice] = default_rate
    return rates


def character_budget(window: float, characters_per_second: float, maximum_ratio: float) -> int:
    """Calculate a conservative nonzero target-text budget for one cue."""
    return max(4, math.floor(window * characters_per_second * maximum_ratio))


def estimated_duration(text: str, characters_per_second: float) -> float:
    """Estimate target speech duration using its assigned voice calibration."""
    return len(text.strip()) / characters_per_second if characters_per_second > 0 else math.inf


def generate_translation(
    model: Any,
    tokenizer: Any,
    text: str,
    target_token: int,
    maximum_tokens: int,
) -> str:
    """Generate one repetition-controlled NLLB translation."""
    inputs = tokenizer(text, return_tensors="pt", truncation=True)
    generated = model.generate(
        **inputs,
        forced_bos_token_id=target_token,
        max_new_tokens=max(2, maximum_tokens),
        no_repeat_ngram_size=3,
        repetition_penalty=1.15,
        num_beams=4,
        early_stopping=True,
    )
    return clean_translation_repetition(
        tokenizer.batch_decode(generated, skip_special_tokens=True)[0].strip()
    )


def translate_constrained(
    transcript: dict,
    target_language: str,
    model_name: str,
    source_model_language: str | None,
    target_model_language: str | None,
    probe_dir: Path,
    probe_text: str | None,
    maximum_ratio: float,
    maximum_extension: float,
    default_rate: float,
) -> tuple[dict, dict]:
    """Translate all cues, retry overlong lines, and return an automatic fit report."""
    source_language = transcript["language"]
    source_code = nllb_code(source_language, source_model_language)
    target_code = nllb_code(target_language, target_model_language)
    language = target_language.lower().split("-", 1)[0]
    sample_text = probe_text or PROBE_TEXT.get(language)
    if not sample_text:
        raise ValueError(f"Provide --probe-text for target language {target_language!r}")
    tokenizer = AutoTokenizer.from_pretrained(model_name, src_lang=source_code)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
    target_token = tokenizer.convert_tokens_to_ids(target_code)
    input_segments = transcript["segments"]
    segments = deduplicate_adjacent_cues(input_segments)
    windows = available_windows(segments, maximum_extension)
    rates = voice_rates({segment["voice"] for segment in segments}, probe_dir, sample_text, default_rate)
    translated_segments = []
    findings = []
    for segment, window in zip(segments, windows):
        voice = segment["voice"]
        rate = rates[voice]
        budget = character_budget(window, rate, maximum_ratio)
        text = generate_translation(model, tokenizer, segment["text"], target_token, 128)
        retried = False
        if len(text) > budget:
            output_tokens = len(tokenizer(text, add_special_tokens=False).input_ids)
            constrained_tokens = max(2, math.floor(output_tokens * budget / len(text) * 0.9))
            text = generate_translation(
                model, tokenizer, segment["text"], target_token, constrained_tokens
            )
            retried = True
        duration = estimated_duration(text, rate)
        ratio = duration / window if window > 0 else math.inf
        status = "fits" if text and ratio <= maximum_ratio else "overlong"
        translated = {
            **segment,
            "source_text": segment["text"],
            "text": text,
            "duration_constraint": {
                "status": status,
                "available_seconds": round(window, 3),
                "estimated_seconds": round(duration, 3),
                "estimated_ratio": round(ratio, 4),
                "character_budget": budget,
                "voice_characters_per_second": round(rate, 3),
                "retried": retried,
            },
        }
        translated_segments.append(translated)
        if status != "fits":
            findings.append(
                {
                    "start": segment["start"],
                    "end": segment["end"],
                    "speaker": segment["speaker"],
                    "source_text": segment["text"],
                    "translated_text": text,
                    "estimated_ratio": round(ratio, 4),
                }
            )
    output = {
        **transcript,
        "source_language": source_language,
        "language": target_language,
        "output_language": target_language,
        "translation_model": model_name,
        "segments": translated_segments,
    }
    report = {
        "schema_version": 1,
        "automatic": True,
        "status": "passed" if not findings else "failed",
        "source_language": source_language,
        "target_language": target_language,
        "model": model_name,
        "segment_count": len(segments),
        "input_segment_count": len(input_segments),
        "deduplicated_segment_count": len(input_segments) - len(segments),
        "fitted_segment_count": len(segments) - len(findings),
        "overlong_segment_count": len(findings),
        "retried_segment_count": sum(
            segment["duration_constraint"]["retried"] for segment in translated_segments
        ),
        "maximum_allowed_ratio": maximum_ratio,
        "maximum_estimated_ratio": max(
            segment["duration_constraint"]["estimated_ratio"] for segment in translated_segments
        ),
        "voice_rates": rates,
        "findings": findings,
    }
    return output, report


def main() -> None:
    """Parse options, translate with constraints, and stop on unresolved lines."""
    parser = argparse.ArgumentParser(description="Generate duration-constrained translations.")
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--target-language", default="en")
    parser.add_argument("--model", default="facebook/nllb-200-distilled-600M")
    parser.add_argument("--source-model-language")
    parser.add_argument("--target-model-language")
    parser.add_argument("--probe-dir", type=Path, required=True)
    parser.add_argument("--probe-text")
    parser.add_argument("--maximum-ratio", type=float, default=1.15)
    parser.add_argument("--maximum-extension", type=float, default=0.75)
    parser.add_argument("--default-characters-per-second", type=float, default=14.0)
    parser.add_argument("--output-script", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    args = parser.parse_args()
    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    translated, report = translate_constrained(
        transcript, args.target_language, args.model, args.source_model_language,
        args.target_model_language, args.probe_dir, args.probe_text,
        args.maximum_ratio, args.maximum_extension, args.default_characters_per_second,
    )
    args.output_script.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_script.write_text(json.dumps(translated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Duration-constrained translation: {report['status']}")
    print(f"Fitted {report['fitted_segment_count']}/{report['segment_count']} cues; retried {report['retried_segment_count']}")
    if report["status"] != "passed":
        raise RuntimeError(f"{report['overlong_segment_count']} translated cues remain overlong")


if __name__ == "__main__":
    main()
