"""Tests for independent SeamlessM4T speech-to-English evidence."""

import json
import wave
from pathlib import Path

import pytest

from videotranslator.commands.create_subtitles import parse_args, prepare_runtime_environment
from videotranslator.commands.runtime_device import run_preferring_cuda
from videotranslator.commands.run_canonical_subtitles import run_canonical_attempt
from videotranslator.commands.qualify_speech_translation import WORKSPACE, workspace_path
from videotranslator.commands.speech_translate import (
    SeamlessSpeechTranslator,
    collect_speech_translation_evidence,
    region_audio_hash,
    slice_audio_region,
    speech_cache_key,
)


def write_silence(path: Path, seconds: float = 3.0, sample_rate: int = 16_000) -> None:
    """Write a tiny mono PCM WAV for offline speech-translation tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = b"\x00\x00" * int(sample_rate * seconds)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(frames)


def test_qualification_paths_are_workspace_relative():
    assert workspace_path(WORKSPACE / ".model-cache" / "huggingface") == ".model-cache/huggingface"
    assert workspace_path(WORKSPACE / "videotranslator" / "outputs" / "sample.wav") == (
        "videotranslator/outputs/sample.wav"
    )
    with pytest.raises(ValueError, match="Path must remain inside workspace"):
        workspace_path(WORKSPACE.parent / "outside-cache")


def translated_document() -> dict:
    """Return one translated group whose source text is intentionally wrong."""
    return {
        "schema_version": 1, "artifact_type": "canonical_timed_text",
        "stage": "translated", "source_language": "zh", "output_language": "en",
        "language_probability": 1.0, "metadata": {},
        "segments": [{
            "id": "group-1", "semantic_group_id": "group-1",
            "source_cue_ids": [1], "start": 0.5, "end": 2.5,
            "source_text": "一共二万二千人。",
            "translated_text": "There were 22,000 people in all.",
            "speaker": "speaker-01", "words": [], "confidence": {},
            "provenance": [], "metadata": {},
        }],
    }


def test_speech_cache_key_ignores_whisper_text():
    first = speech_cache_key("abc", "zh", "eng", "model", {"max_new_tokens": 256})
    second = speech_cache_key("abc", "zh", "eng", "model", {"max_new_tokens": 256})
    third = speech_cache_key("abc", "zh", "eng", "model", {"max_new_tokens": 128})
    assert first == second
    assert first != third


def test_audio_hash_changes_with_samples_not_transcript_text():
    import numpy as np

    quiet = np.zeros(1600, dtype=np.float32)
    loud = np.ones(1600, dtype=np.float32) * 0.2
    assert region_audio_hash(quiet, 0.0, 0.1, 16000) != region_audio_hash(loud, 0.0, 0.1, 16000)
    assert slice_audio_region(loud, 0.0, 0.05, 16000).shape[0] == 800


def test_speech_evidence_does_not_replace_canonical_text(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    write_silence(audio)
    scores = {
        ("一共二万二千人。", "There were 22,000 people in all."): 0.40,
        ("一共二万二千人。", "the Treaty of Shimonoseki"): 0.55,
        ("the Treaty of Shimonoseki", "There were 22,000 people in all."): 0.10,
    }
    output, report = collect_speech_translation_evidence(
        translated_document(), audio,
        lambda samples, source, target: "the Treaty of Shimonoseki",
        model_name="fixture-seamless",
        cache_directory=tmp_path / "cache",
        similarity=lambda left, right: scores[(left, right)],
    )
    segment = output["segments"][0]
    assert segment["source_text"] == "一共二万二千人。"
    assert segment["translated_text"] == "There were 22,000 people in all."
    assert segment["metadata"]["speech_translation"]["text"] == "the Treaty of Shimonoseki"
    assert "source_asr_suspect" in report["checks"][0]["issues"]
    assert report["passed"] is True
    assert report["source_asr_suspect_count"] == 1
    assert segment["provenance"][-1]["replaced_translated_text"] is False


def test_speech_translation_reuses_audio_cache(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    write_silence(audio)
    calls = []

    def translate(samples, source, target):
        calls.append((len(samples), source, target))
        return "Hello."

    collect_speech_translation_evidence(
        translated_document(), audio, translate,
        model_name="fixture-seamless", cache_directory=tmp_path / "cache",
        similarity=lambda left, right: 0.9,
    )
    collect_speech_translation_evidence(
        translated_document(), audio, translate,
        model_name="fixture-seamless", cache_directory=tmp_path / "cache",
        similarity=lambda left, right: 0.9,
    )
    assert len(calls) == 1


def test_short_audio_region_is_explicitly_unsupported(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    write_silence(audio, seconds=1.0)
    document = translated_document()
    document["segments"][0]["start"] = 0.0
    document["segments"][0]["end"] = 0.02
    _output, report = collect_speech_translation_evidence(
        document, audio, lambda samples, source, target: "unused",
        model_name="fixture-seamless",
    )
    assert report["checks"][0]["status"] == "unsupported"
    assert report["passed"] is True
    assert report["unsupported_count"] == 1


def test_failed_speech_decode_is_recorded_without_promotion(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    write_silence(audio)

    def explode(samples, source, target):
        raise RuntimeError("decoder exploded")

    output, report = collect_speech_translation_evidence(
        translated_document(), audio, explode, model_name="fixture-seamless",
    )
    assert report["passed"] is False
    assert report["failed_count"] == 1
    assert output["segments"][0]["translated_text"].startswith("There were 22,000")
    assert output["segments"][0]["metadata"]["speech_translation"]["status"] == "failed"


def test_canonical_attempt_writes_speech_report(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    write_silence(audio, seconds=3.0)
    source = {
        "language": "en", "language_probability": 1.0,
        "task": "transcribe", "output_language": "en",
        "segments": [{
            "id": 1, "start": 0.0, "end": 2.0,
            "text": "Synthetic source.",
            "words": [{"start": 0.0, "end": 1.8, "word": "Synthetic source."}],
        }],
    }
    diarization = {"turns": [{"start": 0.0, "end": 2.0, "speaker": "RAW_A"}]}
    result = run_canonical_attempt(
        source, source, diarization, "en", "model",
        lambda request: "Synthetic source.", tmp_path,
        audio_path=audio,
        speech_translate=lambda samples, source_language, target: "Synthetic source.",
        speech_model="fixture-seamless",
        semantic_similarity=lambda left, right: 0.95,
    )
    assert result["speech_translation"]["evaluated"] is True
    assert result["speech_translation"]["passed"] is True
    assert (tmp_path / "speech-translation.json").is_file()
    canonical = json.loads((tmp_path / "canonical-subtitles.json").read_text(encoding="utf-8"))
    assert canonical["segments"][0]["translated_text"] == "Synthetic source."
    assert canonical["segments"][0]["metadata"]["speech_translation"]["text"] == "Synthetic source."


def test_cuda_oom_falls_back_to_cpu_once(monkeypatch):
    devices = []
    monkeypatch.setattr(
        "videotranslator.commands.runtime_device.resolve_device",
        lambda requested: "cuda" if requested in {None, "auto", "cuda"} else "cpu",
    )

    def operation(device: str) -> str:
        devices.append(device)
        if device == "cuda":
            raise RuntimeError("CUDA out of memory")
        return "ok"

    result, selected = run_preferring_cuda(operation, "cuda")
    assert result == "ok"
    assert selected == "cpu"
    assert devices == ["cuda", "cpu"]


def test_speech_report_records_group_latency(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    write_silence(audio)
    _output, report = collect_speech_translation_evidence(
        translated_document(), audio, lambda samples, source, target: "Hello.",
        model_name="fixture-seamless", similarity=lambda left, right: 0.9,
    )
    assert "latency_ms" in report["checks"][0]
    assert report["latency_ms"]["worst"] >= 0


def test_seamless_skips_small_gpus_in_auto_mode(monkeypatch):
    monkeypatch.setattr(
        "videotranslator.commands.speech_translate.large_model_cuda_available",
        lambda requested="auto": False,
    )
    monkeypatch.setattr(
        "videotranslator.commands.speech_translate.resolve_device",
        lambda requested: "cuda",
    )
    loaded = []

    translator = SeamlessSpeechTranslator("facebook/seamless-m4t-v2-large", "auto")
    translator._load = lambda device: loaded.append(device) or setattr(translator, "device", device)
    translator._ensure_loaded()
    assert loaded == ["cpu"]
    assert translator.fallback_events[0]["reason"] == "insufficient-vram-for-seamless-m4t-v2"


def test_speech_translation_flag_is_opt_in():
    args = parse_args(["video.mp4"])
    assert args.speech_translation is False
    assert args.speech_translation_model == "facebook/seamless-m4t-v2-large"


def test_runtime_redirects_hub_and_temp_onto_shared_root(tmp_path: Path):
    shared = tmp_path / "shared"
    env, events = prepare_runtime_environment(
        tmp_path / "output", {"PYTHON_CACHE_HOME": str(shared)},
    )
    assert env["HF_HOME"] == str(shared / "huggingface")
    assert env["HF_HUB_CACHE"] == str(shared / "huggingface" / "hub")
    assert env["TEMP"] == str(shared / "tmp")
    assert env["TMP"] == str(shared / "tmp")
    assert env["TORCH_HOME"] == str(shared / "torch")
    assert events == []
    assert Path(env["TEMP"]).is_dir()


def test_workstation_b_model_cache_root_is_honored(tmp_path: Path):
    root = tmp_path / "Users" / "z005537p" / "NitishWork" / "HM" / "temp" / ".model-cache"
    env, _events = prepare_runtime_environment(
        tmp_path / "output", {"PYTHON_CACHE_HOME": str(root)},
    )
    assert env["PYTHON_CACHE_HOME"] == str(root)
    assert env["HF_HOME"] == str(root / "huggingface")
    assert env["TEMP"] == str(root / "tmp")


def test_workstation_profile_keeps_explicit_hub_home(tmp_path: Path):
    root = tmp_path / "PythonCaches"
    hub = tmp_path / "huggingface-profile"
    env, _events = prepare_runtime_environment(
        tmp_path / "output",
        {"PYTHON_CACHE_HOME": str(root), "HF_HOME": str(hub)},
    )
    assert env["PYTHON_CACHE_HOME"] == str(root)
    assert env["HF_HOME"] == str(hub)
    assert env["HF_HUB_CACHE"] == str(hub / "hub")
    assert env["TEMP"] == str(root / "tmp")
