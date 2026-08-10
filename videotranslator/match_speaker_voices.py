"""Match persistent speakers to synthetic voices using multi-feature acoustics."""

from __future__ import annotations

import argparse
import json
import math
import tempfile
import wave
from pathlib import Path
from typing import Any

import librosa
import numpy as np
from piper import PiperVoice, SynthesisConfig
from scipy.optimize import linear_sum_assignment

from generate_dub import ensure_voice


SAMPLE_RATE = 16_000
FEATURES = ("log_pitch", "pitch_range", "log_centroid", "log_bandwidth", "energy_range")
WEIGHTS = np.array([0.55, 0.10, 0.15, 0.10, 0.10], dtype=np.float64)
PROBE_TEXT = {
    "en": "I understand. Let us take a careful look at what happened here.",
    "de": "Ich verstehe. Sehen wir uns genau an, was hier passiert ist.",
    "es": "Entiendo. Veamos con cuidado lo que ha ocurrido aquí.",
    "fr": "Je comprends. Regardons attentivement ce qui s'est passé ici.",
}


def acoustic_profile(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> dict[str, float]:
    """Measure a voice using pitch, spectral shape, and energy dynamics.

    No feature is interpreted as gender. Log transforms make cross-language
    comparisons less sensitive to absolute units.
    """
    if len(audio) < sample_rate // 4:
        raise ValueError("At least 250 ms of speech is required for voice profiling")
    intervals = librosa.effects.split(audio, top_db=35)
    voiced_audio = np.concatenate([audio[start:end] for start, end in intervals]) if len(intervals) else audio
    f0, voiced, _probability = librosa.pyin(
        voiced_audio, fmin=65, fmax=400, sr=sample_rate, frame_length=1024
    )
    pitches = f0[voiced & np.isfinite(f0)] if f0 is not None else np.array([])
    median_pitch = float(np.median(pitches)) if len(pitches) else 165.0
    pitch_range = float(np.percentile(pitches, 90) - np.percentile(pitches, 10)) if len(pitches) > 4 else 0.0
    centroid = librosa.feature.spectral_centroid(y=voiced_audio, sr=sample_rate)[0]
    bandwidth = librosa.feature.spectral_bandwidth(y=voiced_audio, sr=sample_rate)[0]
    rms = librosa.feature.rms(y=voiced_audio)[0]
    nonzero_rms = rms[rms > 1e-7]
    energy_range = (
        float(20 * np.log10(np.percentile(nonzero_rms, 90) / np.percentile(nonzero_rms, 10)))
        if len(nonzero_rms) > 4 else 0.0
    )
    return {
        "log_pitch": math.log(max(median_pitch, 1.0)),
        "pitch_range": pitch_range,
        "log_centroid": math.log(max(float(np.median(centroid)), 1.0)),
        "log_bandwidth": math.log(max(float(np.median(bandwidth)), 1.0)),
        "energy_range": energy_range,
        "median_pitch_hz": median_pitch,
    }


def collect_speaker_audio(
    waveform: np.ndarray,
    turns: list[dict],
    source_to_stable: dict[str, str],
    maximum_seconds: float = 45.0,
) -> dict[str, np.ndarray]:
    """Collect bounded diarization turns for each stable persistent speaker."""
    pieces: dict[str, list[np.ndarray]] = {}
    lengths: dict[str, int] = {}
    maximum_samples = round(maximum_seconds * SAMPLE_RATE)
    for turn in turns:
        speaker = source_to_stable.get(turn["speaker"])
        if not speaker or lengths.get(speaker, 0) >= maximum_samples:
            continue
        start = max(0, round(float(turn["start"]) * SAMPLE_RATE))
        end = min(len(waveform), round(float(turn["end"]) * SAMPLE_RATE))
        piece = waveform[start:end]
        remaining = maximum_samples - lengths.get(speaker, 0)
        piece = piece[:remaining]
        if len(piece):
            pieces.setdefault(speaker, []).append(piece)
            lengths[speaker] = lengths.get(speaker, 0) + len(piece)
    return {speaker: np.concatenate(items) for speaker, items in pieces.items()}


def match_profiles(
    speaker_profiles: dict[str, dict[str, float]],
    voice_profiles: dict[str, dict[str, float]],
) -> tuple[dict[str, str], dict[str, float]]:
    """Find the globally optimal unique voice assignment by weighted distance."""
    speakers = sorted(speaker_profiles)
    voices = sorted(voice_profiles)
    if len(voices) < len(speakers):
        raise ValueError(f"Need at least {len(speakers)} candidate voices, found {len(voices)}")
    all_rows = np.array(
        [[profile[name] for name in FEATURES] for profile in [*speaker_profiles.values(), *voice_profiles.values()]],
        dtype=np.float64,
    )
    scale = all_rows.std(axis=0)
    scale[scale < 1e-6] = 1.0
    costs = np.zeros((len(speakers), len(voices)), dtype=np.float64)
    for row, speaker in enumerate(speakers):
        source = np.array([speaker_profiles[speaker][name] for name in FEATURES])
        for column, voice in enumerate(voices):
            target = np.array([voice_profiles[voice][name] for name in FEATURES])
            costs[row, column] = float(np.sqrt(np.sum(WEIGHTS * ((source - target) / scale) ** 2)))
    rows, columns = linear_sum_assignment(costs)
    assignments = {speakers[row]: voices[column] for row, column in zip(rows, columns)}
    distances = {speakers[row]: round(float(costs[row, column]), 4) for row, column in zip(rows, columns)}
    return assignments, distances


def synthesize_probe(voice_name: str, models_dir: Path, probe_dir: Path, text: str) -> Path:
    """Synthesize and cache neutral comparison speech for one Piper voice."""
    output = probe_dir / f"{voice_name}.wav"
    if output.is_file() and output.stat().st_size > 0:
        return output
    probe_dir.mkdir(parents=True, exist_ok=True)
    voice = PiperVoice.load(ensure_voice(voice_name, models_dir))
    with wave.open(str(output), "wb") as wav_file:
        voice.synthesize_wav(text, wav_file, syn_config=SynthesisConfig(length_scale=1.0))
    return output


def match_voices(
    transcript: dict,
    diarization_report: dict,
    audio_path: Path,
    voices: list[str],
    models_dir: Path,
    probe_dir: Path,
    target_language: str,
    probe_text: str | None,
) -> tuple[dict, dict]:
    """Profile persistent speakers and candidate voices, then assign one-to-one."""
    source_to_stable = {}
    for segment in transcript["segments"]:
        assignment = segment.get("speaker_assignment", {})
        if assignment.get("source_label") and segment.get("speaker"):
            source_to_stable[assignment["source_label"]] = segment["speaker"]
    waveform, _ = librosa.load(audio_path, sr=SAMPLE_RATE, mono=True)
    speaker_audio = collect_speaker_audio(waveform, diarization_report["turns"], source_to_stable)
    speaker_profiles = {speaker: acoustic_profile(audio) for speaker, audio in speaker_audio.items()}
    language = target_language.lower().split("-", 1)[0]
    sample_text = probe_text or PROBE_TEXT.get(language)
    if not sample_text:
        raise ValueError(f"Provide --probe-text for target language {target_language!r}")
    voice_profiles = {}
    for voice_name in voices:
        path = synthesize_probe(voice_name, models_dir, probe_dir, sample_text)
        audio, _ = librosa.load(path, sr=SAMPLE_RATE, mono=True)
        voice_profiles[voice_name] = acoustic_profile(audio)
    assignments, distances = match_profiles(speaker_profiles, voice_profiles)
    updated = json.loads(json.dumps(transcript))
    for segment in updated["segments"]:
        speaker = segment["speaker"]
        segment["voice"] = assignments[speaker]
        segment["voice_match"] = {"method": "multi-feature-acoustic-distance", "distance": distances[speaker]}
    report = {
        "schema_version": 1,
        "method": "multi-feature-acoustic-hungarian",
        "gender_inference": False,
        "features": list(FEATURES),
        "weights": {name: float(weight) for name, weight in zip(FEATURES, WEIGHTS)},
        "speaker_profiles": speaker_profiles,
        "voice_profiles": voice_profiles,
        "assignments": [
            {"speaker": speaker, "voice": voice, "distance": distances[speaker]}
            for speaker, voice in sorted(assignments.items())
        ],
    }
    return updated, report


def main() -> None:
    """Parse artifacts, perform acoustic voice matching, and persist its audit."""
    parser = argparse.ArgumentParser(description="Match persistent speakers to target voices acoustically.")
    parser.add_argument("transcript", type=Path)
    parser.add_argument("diarization_report", type=Path)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--voice", action="append", required=True, dest="voices")
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--probe-dir", type=Path, required=True)
    parser.add_argument("--target-language", default="en")
    parser.add_argument("--probe-text")
    parser.add_argument("--output-script", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    args = parser.parse_args()
    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    diarization_report = json.loads(args.diarization_report.read_text(encoding="utf-8"))
    updated, report = match_voices(
        transcript, diarization_report, args.audio, args.voices, args.models_dir,
        args.probe_dir, args.target_language, args.probe_text,
    )
    args.output_script.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_script.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Matched {len(report['assignments'])} persistent speakers without gender inference")


if __name__ == "__main__":
    main()
