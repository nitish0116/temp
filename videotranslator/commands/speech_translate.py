"""Collect independent SeamlessM4T speech-to-English evidence per semantic group."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import wave
from copy import deepcopy
from pathlib import Path
from typing import Callable

import numpy as np

try:
    from .canonical_timed_text import append_provenance, validate_canonical_timed_text
    from .qa_translation_agreement import agreement_issues
    from .runtime_device import (
        is_cuda_out_of_memory,
        large_model_cuda_available,
        release_cuda_cache,
        resolve_device,
        run_preferring_cuda,
        torch_dtype_for_device,
    )
    from .translate_contextual import TranslationRequest, valid_translation_response
except ImportError:
    from canonical_timed_text import append_provenance, validate_canonical_timed_text
    from qa_translation_agreement import agreement_issues
    from runtime_device import (
        is_cuda_out_of_memory,
        large_model_cuda_available,
        release_cuda_cache,
        resolve_device,
        run_preferring_cuda,
        torch_dtype_for_device,
    )
    from translate_contextual import TranslationRequest, valid_translation_response


SPEECH_TRANSLATION_PROTOCOL_VERSION = 1
DEFAULT_SPEECH_MODEL = "facebook/seamless-m4t-v2-large"
SAMPLE_RATE = 16_000
MINIMUM_REGION_SECONDS = 0.08
GENERATE_CONFIG = {"max_new_tokens": 256, "num_beams": 1, "do_sample": False}
SEAMLESS_LANGUAGE = {
    "en": "eng", "eng": "eng", "english": "eng",
    "ja": "jpn", "jp": "jpn", "jpn": "jpn", "japanese": "jpn",
    "ko": "kor", "kor": "kor", "korean": "kor",
    "zh": "cmn", "cmn": "cmn", "zh-cn": "cmn", "zh_cn": "cmn",
    "chinese": "cmn", "mandarin": "cmn",
}


def seamless_language_code(language: str, default: str = "eng") -> str:
    """Map Whisper-style codes onto SeamlessM4T language identifiers.

    Example:: ``ja`` becomes ``jpn`` and an empty value falls back to ``eng``.
    """
    normalized = (language or "").strip().casefold()
    return SEAMLESS_LANGUAGE.get(normalized, default if not normalized else normalized)


def load_mono_pcm16(path: Path, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Load 16-bit mono WAV audio without depending on a particular cache root.

    Example:: the subtitle extract stage writes 16 kHz PCM that this loader
    returns as float32 samples in ``[-1, 1]``.
    """
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1:
            raise ValueError(f"Speech translation requires mono audio: {path}")
        if handle.getsampwidth() != 2:
            raise ValueError(f"Speech translation requires 16-bit PCM audio: {path}")
        if handle.getframerate() != sample_rate:
            raise ValueError(
                f"Speech translation requires {sample_rate} Hz audio, "
                f"got {handle.getframerate()} Hz: {path}"
            )
        frames = handle.readframes(handle.getnframes())
    integers = np.frombuffer(frames, dtype="<i2")
    return integers.astype(np.float32) / 32768.0


def slice_audio_region(
    waveform: np.ndarray, start: float, end: float, sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """Copy the exclusive sample window covering one semantic group's timing.

    Example:: a group from 1.0 s to 2.0 s at 16 kHz yields 16000 samples.
    """
    begin = max(0, int(round(float(start) * sample_rate)))
    stop = min(len(waveform), max(begin, int(round(float(end) * sample_rate))))
    return np.array(waveform[begin:stop], dtype=np.float32, copy=True)


def region_audio_hash(samples: np.ndarray, start: float, end: float, sample_rate: int) -> str:
    """Fingerprint the PCM region independently of Whisper transcript text.

    Example:: changing the source ASR string does not change this hash, so a
    corrupt transcript cannot reuse another group's speech evidence.
    """
    payload = np.clip(np.rint(samples * 32768.0), -32768, 32767).astype("<i2").tobytes()
    digest = hashlib.sha256()
    digest.update(payload)
    digest.update(
        json.dumps(
            {
                "start_ms": int(round(float(start) * 1000)),
                "end_ms": int(round(float(end) * 1000)),
                "sample_rate": sample_rate,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    return digest.hexdigest()


def speech_cache_key(
    audio_hash: str,
    source_language: str,
    target_language: str,
    model: str,
    generate_config: dict,
) -> str:
    """Version speech translations by audio, languages, model, and decoder settings.

    Example:: raising ``max_new_tokens`` produces a different cache filename.
    """
    payload = json.dumps(
        {
            "protocol_version": SPEECH_TRANSLATION_PROTOCOL_VERSION,
            "audio_hash": audio_hash,
            "source_language": source_language,
            "target_language": target_language,
            "model": model,
            "generate_config": generate_config,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_cached(path: Path | None) -> str | None:
    """Return cached speech-English text, or ``None`` when the file is absent."""
    if path is None or not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    text = value.get("translated_text")
    return text if isinstance(text, str) and text.strip() else None


def _write_cached(path: Path | None, text: str, details: dict) -> None:
    """Atomically persist one speech-translation cache record."""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"translated_text": text, **details}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class SeamlessSpeechTranslator:
    """GPU-first SeamlessM4T-v2 speech-to-text translator with CPU fallback."""

    def __init__(
        self,
        model_name: str = DEFAULT_SPEECH_MODEL,
        device: str = "auto",
        *,
        local_files_only: bool = False,
    ) -> None:
        """Defer weight loading until the first uncached audio region.

        Example:: ``device="auto"`` tries CUDA, then reloads on CPU after OOM.
        """
        self.model_name = model_name
        self.requested_device = device
        self.local_files_only = local_files_only
        self.device: str | None = None
        self.processor = None
        self.model = None
        self.fallback_events: list[dict[str, str]] = []

    def unload(self) -> None:
        """Drop weights so a later GPU stage can reclaim memory.

        Example:: agreement embeddings can load after speech evidence is cached.
        """
        self.model = None
        self.processor = None
        release_cuda_cache()

    def _load(self, device: str) -> None:
        """Load the speech-to-text checkpoint onto one resolved device."""
        import torch
        from transformers import AutoProcessor, SeamlessM4Tv2ForSpeechToText

        print(f"Loading SeamlessM4T processor from cache: {self.model_name}", flush=True)
        self.processor = AutoProcessor.from_pretrained(
            self.model_name, local_files_only=self.local_files_only,
        )
        dtype = torch_dtype_for_device(device)
        print(f"Loading SeamlessM4T speech-to-text on {device}: {self.model_name}", flush=True)
        self.model = SeamlessM4Tv2ForSpeechToText.from_pretrained(
            self.model_name,
            dtype=dtype,
            local_files_only=self.local_files_only,
        ).to(device)
        self.model.eval()
        self.device = device

    def _ensure_loaded(self) -> None:
        """Load once, preferring CUDA when VRAM is sufficient.

        Example:: automatic mode skips a 4 GiB GPU instead of paging a 9 GiB
        checkpoint into VRAM until the allocator fails.
        """
        if self.model is not None:
            return
        if self.requested_device != "cuda" and not large_model_cuda_available(self.requested_device):
            self._load("cpu")
            if resolve_device(self.requested_device) == "cuda":
                self.fallback_events.append({
                    "stage": "speech-translation",
                    "reason": "insufficient-vram-for-seamless-m4t-v2",
                    "resolution": "loaded model on cpu",
                })
            return

        def load(device: str) -> None:
            """Load SeamlessM4T weights onto the requested device."""
            try:
                self._load(device)
            except Exception:
                self.unload()
                raise

        _, selected = run_preferring_cuda(load, self.requested_device)
        if selected != resolve_device(self.requested_device):
            self.fallback_events.append({
                "stage": "speech-translation",
                "reason": "cuda-out-of-memory-during-load",
                "resolution": f"loaded model on {selected}",
            })

    def _move_to_cpu(self) -> None:
        """Continue generation on CPU after a mid-run CUDA allocation failure."""
        if self.model is None or self.device == "cpu":
            return
        self.model = self.model.to("cpu")
        self.device = "cpu"
        release_cuda_cache()
        self.fallback_events.append({
            "stage": "speech-translation",
            "reason": "cuda-out-of-memory-during-generate",
            "resolution": "continue-speech-translation-on-cpu",
        })

    def _generate(self, samples: np.ndarray, target_language: str) -> str:
        """Decode one audio region to target-language text on the loaded device."""
        import torch

        assert self.processor is not None and self.model is not None and self.device is not None
        tgt_lang = seamless_language_code(target_language, "eng")
        inputs = self.processor(
            audios=samples, sampling_rate=SAMPLE_RATE, return_tensors="pt",
        )
        model_dtype = next(self.model.parameters()).dtype
        moved = {}
        for key, value in inputs.items():
            tensor = value.to(self.device)
            if torch.is_floating_point(tensor):
                tensor = tensor.to(dtype=model_dtype)
            moved[key] = tensor
        with torch.no_grad():
            tokens = self.model.generate(**moved, tgt_lang=tgt_lang, **GENERATE_CONFIG)
        decoded = self.processor.batch_decode(tokens, skip_special_tokens=True)
        return str(decoded[0]).strip()

    def __call__(self, samples: np.ndarray, source_language: str, target_language: str = "eng") -> str:
        """Translate one speech region without reading source transcript text.

        Example:: Japanese audio can yield English even when Whisper text is wrong.
        """
        del source_language
        self._ensure_loaded()
        try:
            return self._generate(samples, target_language)
        except RuntimeError as error:
            if self.device != "cuda" or not is_cuda_out_of_memory(error):
                raise
            self._move_to_cpu()
            return self._generate(samples, target_language)


def compare_speech_and_text(
    source: str,
    primary: str,
    speech: str,
    similarity: Callable[[str, str], float] | None,
    *,
    source_language: str,
    target_language: str,
) -> tuple[list[str], dict[str, float]]:
    """Flag disagreements that can diagnose corrupt source ASR.

    Example:: ``Treaty of Shimonoseki`` from audio versus a ``22,000`` text
    translation records semantic and number disagreements plus ASR suspicion
    when the audio English is closer to the source embedding.
    """
    scores = {
        "source_primary_similarity": 0.0,
        "source_speech_similarity": 0.0,
        "speech_primary_similarity": 0.0,
    }
    if similarity is not None:
        scores["source_primary_similarity"] = float(similarity(source, primary))
        scores["source_speech_similarity"] = float(similarity(source, speech))
        scores["speech_primary_similarity"] = float(similarity(speech, primary))
    issues = agreement_issues(
        source, primary, speech,
        scores["source_primary_similarity"],
        scores["source_speech_similarity"],
        scores["speech_primary_similarity"],
    )
    request = TranslationRequest(
        group_id="speech-output-contract",
        source_language=source_language,
        target_language=target_language,
        current_text=source,
        previous=(),
        following=(),
    )
    if not valid_translation_response(speech, request):
        issues.append("speech_output_contract_failure")
    if (
        "speech_output_contract_failure" not in issues
        and "semantic_disagreement" in issues
        and scores["source_speech_similarity"]
        > scores["source_primary_similarity"] + 0.025
    ):
        issues.append("source_asr_suspect")
    return issues, scores


def _median(values: list[float]) -> float:
    """Return the median of a possibly empty latency sample.

    Example:: ``[1.0, 3.0, 2.0]`` yields ``2.0``; an empty list yields ``0.0``.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return float((ordered[middle - 1] + ordered[middle]) / 2)


def collect_speech_translation_evidence(
    document: dict,
    audio_path: Path,
    translate_speech: Callable[[np.ndarray, str, str], str],
    *,
    model_name: str,
    cache_directory: Path | None = None,
    similarity: Callable[[str, str], float] | None = None,
    target_language: str = "eng",
    generate_config: dict | None = None,
) -> tuple[dict, dict]:
    """Attach audio-derived English to every group without changing translations.

    Example:: a failed decode stores ``status=failed`` and keeps the primary
    ``translated_text`` so Whisper timing remains the canonical source.
    """
    validate_canonical_timed_text(document)
    output = deepcopy(document)
    config = dict(generate_config or GENERATE_CONFIG)
    waveform = load_mono_pcm16(audio_path)
    checks = []
    for segment in output["segments"]:
        start, end = float(segment["start"]), float(segment["end"])
        samples = slice_audio_region(waveform, start, end)
        duration = len(samples) / SAMPLE_RATE
        source = str(segment.get("source_text") or "")
        primary = str(segment.get("translated_text") or "")
        audio_hash = region_audio_hash(samples, start, end, SAMPLE_RATE)
        key = speech_cache_key(
            audio_hash, output["source_language"], target_language, model_name, config,
        )
        cache_path = None if cache_directory is None else cache_directory / f"{key}.json"
        record: dict = {
            "semantic_group_id": segment["semantic_group_id"],
            "start": start,
            "end": end,
            "audio_hash": audio_hash,
            "cache_key": key,
            "cache_hit": False,
            "status": "ok",
            "translated_text": None,
            "issues": [],
            "scores": {},
            "latency_ms": 0.0,
        }
        started = time.perf_counter()
        if duration < MINIMUM_REGION_SECONDS:
            record["status"] = "unsupported"
            record["issues"] = ["audio_region_too_short"]
        else:
            cached = _read_cached(cache_path)
            try:
                if cached is not None:
                    speech = cached
                    record["cache_hit"] = True
                else:
                    speech = str(
                        translate_speech(samples, output["source_language"], target_language)
                    ).strip()
                    if not speech:
                        raise RuntimeError("Speech translation returned empty text")
                    _write_cached(cache_path, speech, {"audio_hash": audio_hash})
                record["translated_text"] = speech
                issues, scores = compare_speech_and_text(
                    source, primary, speech, similarity,
                    source_language=output["source_language"],
                    target_language=target_language,
                )
                record["issues"] = issues
                record["scores"] = scores
            except Exception as error:
                record["status"] = "failed"
                record["issues"] = [f"{type(error).__name__}: {error}"]
        record["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        print(
            f"Speech translation {len(checks) + 1}/{len(output['segments'])} "
            f"{segment['semantic_group_id']} status={record['status']} "
            f"{record['latency_ms']}ms",
            flush=True,
        )
        metadata = dict(segment.get("metadata") or {})
        metadata["speech_translation"] = {
            "text": record["translated_text"],
            "status": record["status"],
            "model": model_name,
            "cache_hit": record["cache_hit"],
            "issues": list(record["issues"]),
        }
        segment["metadata"] = metadata
        segment["provenance"] = append_provenance(
            segment, "speech-translation", "seamless-m4t-v2-audio-to-english",
            model=model_name,
            status=record["status"],
            cache_key=key,
            cache_hit=record["cache_hit"],
            protocol_version=SPEECH_TRANSLATION_PROTOCOL_VERSION,
            replaced_source_text=False,
            replaced_translated_text=False,
        )
        checks.append(record)
    failed = sum(1 for item in checks if item["status"] == "failed")
    unsupported = sum(1 for item in checks if item["status"] == "unsupported")
    ok = sum(1 for item in checks if item["status"] == "ok")
    suspect = sum(1 for item in checks if "source_asr_suspect" in item["issues"])
    report = {
        "schema_version": 1,
        "artifact_type": "speech_translation",
        "protocol_version": SPEECH_TRANSLATION_PROTOCOL_VERSION,
        "model": model_name,
        "audio": str(audio_path.resolve()),
        "target_language": target_language,
        "evaluated": True,
        "passed": failed == 0 and (ok + unsupported) == len(checks),
        "group_count": len(checks),
        "ok_count": ok,
        "unsupported_count": unsupported,
        "failed_count": failed,
        "source_asr_suspect_count": suspect,
        "disagreement_count": sum(
            1 for item in checks
            if item["status"] == "ok" and item["issues"]
        ),
        "generate_config": config,
        "latency_ms": {
            "median": _median([item["latency_ms"] for item in checks]),
            "worst": max((item["latency_ms"] for item in checks), default=0.0),
        },
        "checks": checks,
    }
    output["metadata"] = {
        **output.get("metadata", {}),
        "speech_translation": {
            "protocol_version": SPEECH_TRANSLATION_PROTOCOL_VERSION,
            "model": model_name,
            "ok_count": ok,
            "unsupported_count": unsupported,
            "failed_count": failed,
            "source_asr_suspect_count": suspect,
        },
    }
    validate_canonical_timed_text(output)
    return output, report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the standalone speech-translation command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, help="Mono 16 kHz WAV used by canonical ASR")
    parser.add_argument("translated", type=Path, help="Canonical translated timed-text JSON")
    parser.add_argument("--output-document", type=Path)
    parser.add_argument("--output-report", type=Path)
    parser.add_argument("--cache-directory", type=Path)
    parser.add_argument("--model", default=DEFAULT_SPEECH_MODEL)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--offline", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Write speech-English evidence beside an existing translated document."""
    args = parse_args(argv)
    document = json.loads(args.translated.read_text(encoding="utf-8"))
    translator = SeamlessSpeechTranslator(
        args.model, args.device, local_files_only=args.offline,
    )
    output, report = collect_speech_translation_evidence(
        document, args.audio, translator, model_name=args.model,
        cache_directory=args.cache_directory,
    )
    translator.unload()
    document_path = args.output_document or args.translated.with_name(
        args.translated.stem + ".speech.json"
    )
    report_path = args.output_report or args.translated.with_name("speech-translation.json")
    document_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Speech translation { 'passed' if report['passed'] else 'failed' }: {report_path}")
    if not report["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
