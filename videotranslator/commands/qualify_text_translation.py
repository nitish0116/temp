"""Benchmark dedicated MT candidates against cached multilingual samples."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from .create_subtitles import prepare_runtime_environment
from .dedicated_mt import DedicatedMTTranslator, MADLAD_MODEL
from .qualify_speech_translation import MANIFEST, PROJECT, SAMPLES, locate_translated, select_probe_segments, workspace_path


def references(sample_id: str) -> list[dict]:
    """Return reviewed semantic defects for one qualification sample."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sample = next((item for item in manifest["samples"] if item["id"] == sample_id), {})
    return sample.get("manual_review", {}).get("verified_defects", [])


def cache_file(cache: Path, model: str, source: str, text: str) -> Path:
    """Return a deterministic model/language/text-specific cache path."""
    key = hashlib.sha256(json.dumps([model, source, "en", text], ensure_ascii=False).encode()).hexdigest()
    return cache / f"{key}.json"


def qualify_sample(sample: dict, translator: DedicatedMTTranslator, probe: bool) -> dict:
    """Translate one cached sample and evaluate its reviewed fixtures."""
    path = locate_translated(PROJECT / "outputs" / sample["output_directory"])
    document = json.loads(path.read_text(encoding="utf-8"))
    refs = references(sample["id"])
    if probe:
        document = select_probe_segments(document, refs)
    cache = PROJECT / "outputs" / sample["output_directory"] / "attempts" / "01-conservative" / "canonical" / "dedicated-mt-cache"
    cache.mkdir(parents=True, exist_ok=True)
    checks = []
    for segment in document["segments"]:
        target = cache_file(cache, translator.model_name, document["source_language"], segment["source_text"])
        started = time.perf_counter()
        if target.is_file():
            text = json.loads(target.read_text(encoding="utf-8"))["text"]
            cached = True
        else:
            text = translator.translate(segment["source_text"], document["source_language"])
            target.write_text(json.dumps({"text": text}, ensure_ascii=False) + "\n", encoding="utf-8")
            cached = False
        checks.append({"id": segment["id"], "start": segment["start"], "end": segment["end"], "source_text": segment["source_text"], "translated_text": text, "cached": cached, "latency_ms": round((time.perf_counter()-started)*1000, 1)})
    fixture_checks = []
    for ref in refs:
        match = min(checks, key=lambda item: abs((item["start"] + item["end"])/2 - ref["timestamp_seconds"]))
        folded = match["translated_text"].casefold()
        required = [term for term in ref["required_terms"] if term.casefold() in folded]
        forbidden = [term for term in ref["forbidden_terms"] if term.casefold() in folded]
        fixture_checks.append({"timestamp_seconds": ref["timestamp_seconds"], "source_text": match["source_text"], "translated_text": match["translated_text"], "required_present": required, "forbidden_present": forbidden, "passed": len(required)==len(ref["required_terms"]) and not forbidden})
    return {"id": sample["id"], "source": workspace_path(path), "group_count": len(checks), "checks": checks, "fixture_checks": fixture_checks}


def main(argv: list[str] | None = None) -> None:
    """Run probe or full dedicated-MT qualification and write evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=MADLAD_MODEL)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--output-report", type=Path, default=PROJECT / "docs" / "text-translation-qualification.json")
    args = parser.parse_args(argv)
    env, _ = prepare_runtime_environment(PROJECT / "outputs")
    os.environ.update({key: env[key] for key in env if key in {"HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TORCH_HOME", "TEMP", "TMP"}})
    translator = DedicatedMTTranslator(args.model, args.device, args.offline)
    try:
        samples = [qualify_sample(sample, translator, args.probe) for sample in SAMPLES]
    finally:
        translator.unload()
    fixture_passed = all(check["passed"] for sample in samples for check in sample["fixture_checks"])
    report = {"schema_version": 1, "model": args.model, "device": translator.device, "offline": args.offline, "probe": args.probe, "fixture_passed": fixture_passed, "release_qualified": fixture_passed and not args.probe, "samples": samples}
    args.output_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"fixture_passed": fixture_passed, "release_qualified": report["release_qualified"], "report": workspace_path(args.output_report)}, indent=2))


if __name__ == "__main__":
    main()
