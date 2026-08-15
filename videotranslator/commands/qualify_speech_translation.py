"""Qualify SeamlessM4T-v2 on the three cached multilingual sample runs."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

try:
    from .create_subtitles import prepare_runtime_environment
    from .qa_translation_agreement import MultilingualSimilarity
    from .runtime_device import resolve_device
    from .speech_translate import DEFAULT_SPEECH_MODEL, SeamlessSpeechTranslator, collect_speech_translation_evidence
except ImportError:
    from create_subtitles import prepare_runtime_environment
    from qa_translation_agreement import MultilingualSimilarity
    from runtime_device import resolve_device
    from speech_translate import DEFAULT_SPEECH_MODEL, SeamlessSpeechTranslator, collect_speech_translation_evidence


PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT.parent
MANIFEST = PROJECT / "tests" / "fixtures" / "three_sample_release_review.json"
SAMPLES = (
    {
        "id": "duty-first-japanese",
        "output_directory": "duty-first-kiss-later-episode-1-subtitles",
        "language": "Japanese",
    },
    {
        "id": "episode-one-korean",
        "output_directory": "ep-1-v0-1639315485-720p-subtitles",
        "language": "Korean",
    },
    {
        "id": "linglong-ferry-mandarin",
        "output_directory": "linglongs-ferry-episode-24-subtitles",
        "language": "Mandarin",
    },
)


def workspace_path(value: str | Path | None) -> str | None:
    """Serialize a local path relative to the shared workspace root."""
    if value is None:
        return None
    path = Path(value)
    try:
        return path.resolve().relative_to(WORKSPACE.resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"Path must remain inside workspace {WORKSPACE}: {path}") from error


def working_set_bytes() -> int | None:
    """Return the current process working set on Windows, if available."""
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        psapi = ctypes.WinDLL("psapi")
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
    except (AttributeError, OSError, ValueError):
        return None
    return None


def locate_audio(output: Path) -> Path:
    """Return the extracted 16 kHz WAV used by the cached subtitle run."""
    audio_dir = output / "audio"
    wavs = sorted(audio_dir.glob("*.wav"))
    if not wavs:
        raise FileNotFoundError(f"No extracted WAV found under {audio_dir}")
    return wavs[0]


def locate_translated(output: Path) -> Path:
    """Prefer semantic-group translations over mapped display cues."""
    contextual = output / "attempts" / "01-conservative" / "canonical" / "contextual-translation.json"
    if contextual.is_file():
        return contextual
    fallback = output / "final.en.json"
    if fallback.is_file():
        return fallback
    raise FileNotFoundError(f"No translated canonical JSON found under {output}")


def select_probe_segments(document: dict, references: list[dict]) -> dict:
    """Keep only semantic groups nearest the reviewed defect timestamps.

    Example:: the Mandarin Treaty of Shimonoseki cue is retained without decoding
    every other group in the episode.
    """
    if not references:
        return {**document, "segments": document["segments"][:2]}
    selected = []
    used = set()
    for reference in references:
        timestamp = float(reference["timestamp_seconds"])
        matched = min(
            document["segments"],
            key=lambda item: abs(((float(item["start"]) + float(item["end"])) / 2) - timestamp),
        )
        if matched["id"] not in used:
            selected.append(matched)
            used.add(matched["id"])
    if not selected:
        selected = document["segments"][:1]
    return {**document, "segments": selected}


def fixture_checks(sample_id: str, report: dict) -> list[dict]:
    """Compare audio-English with reviewed required and forbidden terms."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    references = []
    for sample in manifest.get("samples", []):
        if sample.get("id") == sample_id:
            references = sample.get("manual_review", {}).get("verified_defects", [])
    results = []
    for reference in references:
        timestamp = float(reference["timestamp_seconds"])
        matched = min(
            report["checks"],
            key=lambda item: abs(((item["start"] + item["end"]) / 2) - timestamp),
            default=None,
        )
        text = "" if matched is None else str(matched.get("translated_text") or "")
        folded = text.casefold()
        required_present = [term for term in reference.get("required_terms", []) if term.casefold() in folded]
        forbidden_present = [term for term in reference.get("forbidden_terms", []) if term.casefold() in folded]
        results.append({
            "timestamp_seconds": timestamp,
            "required_terms": reference.get("required_terms", []),
            "forbidden_terms": reference.get("forbidden_terms", []),
            "speech_text": text,
            "status": None if matched is None else matched.get("status"),
            "required_present": required_present,
            "forbidden_present": forbidden_present,
            "diagnosable": bool(text)
            and not forbidden_present
            and len(required_present) == len(reference.get("required_terms", [])),
        })
    return results


def qualify_sample(
    sample: dict,
    translator: SeamlessSpeechTranslator,
    similarity: MultilingualSimilarity | None,
    device: str,
    offline: bool,
    probe: bool,
) -> dict:
    """Run speech-to-English on one cached sample and record qualification metrics."""
    output = PROJECT / "outputs" / sample["output_directory"]
    audio = locate_audio(output)
    translated = locate_translated(output)
    document = json.loads(translated.read_text(encoding="utf-8"))
    if probe:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        references = []
        for item in manifest.get("samples", []):
            if item.get("id") == sample["id"]:
                references = item.get("manual_review", {}).get("verified_defects", [])
        document = select_probe_segments(document, references)
    cache = output / "attempts" / "01-conservative" / "canonical" / "speech-translation-cache"
    started = time.perf_counter()
    memory_before = working_set_bytes()
    annotated, report = collect_speech_translation_evidence(
        document, audio, translator, model_name=translator.model_name,
        cache_directory=cache, similarity=similarity,
    )
    elapsed = time.perf_counter() - started
    report_path = output / "attempts" / "01-conservative" / "canonical" / "speech-translation.json"
    annotated_path = output / "attempts" / "01-conservative" / "canonical" / "speech-translation.document.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    annotated_path.write_text(json.dumps(annotated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    coverage_ok = report["failed_count"] == 0 and (
        report["ok_count"] + report["unsupported_count"] == report["group_count"]
    )
    return {
        "id": sample["id"],
        "language": sample["language"],
        "output_directory": sample["output_directory"],
        "audio": workspace_path(audio),
        "translated": workspace_path(translated),
        "report": workspace_path(report_path),
        "device_requested": device,
        "device_used": translator.device,
        "offline": offline,
        "fallback_events": list(translator.fallback_events),
        "elapsed_seconds": round(elapsed, 2),
        "working_set_bytes_before": memory_before,
        "working_set_bytes_after": working_set_bytes(),
        "group_count": report["group_count"],
        "ok_count": report["ok_count"],
        "unsupported_count": report["unsupported_count"],
        "failed_count": report["failed_count"],
        "source_asr_suspect_count": report["source_asr_suspect_count"],
        "disagreement_count": report["disagreement_count"],
        "latency_ms": report.get("latency_ms"),
        "coverage_complete": coverage_ok,
        "fixture_checks": fixture_checks(sample["id"], report),
        "license": "CC-BY-NC-4.0",
        "model": translator.model_name,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the three-sample speech-translation qualification command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--model", default=DEFAULT_SPEECH_MODEL)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--probe", action="store_true",
        help="Decode only reviewed defect groups before a full-episode run",
    )
    parser.add_argument(
        "--output-report",
        type=Path,
        default=PROJECT / "docs" / "speech-translation-qualification.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Prefetch-independent qualification against cached sample artifacts."""
    args = parse_args(argv)
    env, _events = prepare_runtime_environment(PROJECT / "outputs")
    os.environ.update({key: env[key] for key in env if key in {
        "PYTHON_CACHE_HOME", "HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE",
        "TORCH_HOME", "TEMP", "TMP", "TMPDIR",
    }})
    requested = resolve_device(args.device)
    print(f"Qualifying SeamlessM4T on requested device {args.device} -> {requested}", flush=True)
    translator = SeamlessSpeechTranslator(
        args.model, args.device, local_files_only=args.offline,
    )
    similarity = MultilingualSimilarity(
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "cpu", local_files_only=args.offline,
    )
    results = []
    try:
        for sample in SAMPLES:
            print(f"Qualifying {sample['id']}", flush=True)
            results.append(qualify_sample(
                sample, translator, similarity, requested, args.offline, args.probe,
            ))
    finally:
        translator.unload()
    summary = {
        "schema_version": 1,
        "model": args.model,
        "license": "CC-BY-NC-4.0",
        "cache_root": workspace_path(env.get("PYTHON_CACHE_HOME")),
        "hf_home": workspace_path(env.get("HF_HOME")),
        "device_requested": args.device,
        "device_resolved": requested,
        "offline": args.offline,
        "probe": args.probe,
        "coverage_complete": all(item["coverage_complete"] for item in results) and not args.probe,
        "enable_by_default": False,
        "enable_by_default_reason": (
            "Keep opt-in until every sample is coverage-complete and the reviewed "
            "Shimonoseki, Seoul, and cute groups are diagnosable from audio."
        ),
        "samples": results,
    }
    if (
        summary["coverage_complete"]
        and all(
            check["diagnosable"]
            for item in results
            for check in item["fixture_checks"]
        )
    ):
        summary["enable_by_default_reason"] = (
            "Coverage is complete and reviewed defects are diagnosable; default "
            "enablement still requires a GPU-capable workstation measurement."
        )
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({
        "coverage_complete": summary["coverage_complete"],
        "enable_by_default": summary["enable_by_default"],
        "report": workspace_path(args.output_report),
    }, indent=2))
    if not summary["coverage_complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
