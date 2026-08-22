"""Offline narration synthesis with retry, hashing, and selective reuse."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import wave
import re
from pathlib import Path
from typing import Protocol

from .artifacts import sha256_file


class NarrationProvider(Protocol):
    name: str

    def synthesize(self, text: str, output: Path, *, trailing_pause_seconds: float = 0.65) -> dict | None: ...


class DeterministicToneProvider:
    name = "deterministic-tone-fixture-v1"

    def synthesize(self, text: str, output: Path, *, trailing_pause_seconds: float = 0.65) -> None:
        import math
        import struct
        rate = 16000
        duration = max(0.25, len(text.split()) / 12)
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), "wb") as target:
            target.setparams((1, 2, rate, int(rate * duration), "NONE", "not compressed"))
            target.writeframes(b"".join(
                struct.pack("<h", int(1800 * math.sin(2 * math.pi * 220 * index / rate)))
                for index in range(int(rate * duration))
            ))


class FfmpegFliteNarrationProvider:
    """Use FFmpeg's bundled open-source Flite synthesizer entirely offline."""

    name = "ffmpeg-flite:slt:48khz:loudnorm-v1"

    def __init__(self, executable: str = "ffmpeg") -> None:
        self.executable = executable

    def synthesize(self, text: str, output: Path, *, trailing_pause_seconds: float = 0.65) -> None:
        if not shutil.which(self.executable):
            raise RuntimeError("FFmpeg with the flite filter is required for narration")
        output.parent.mkdir(parents=True, exist_ok=True)
        textfile = output.with_suffix(".txt")
        textfile.write_text(text, encoding="utf-8")
        escaped = textfile.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")
        command = [
            self.executable, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"flite=textfile='{escaped}':voice=slt",
            "-af", "aresample=48000,loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(output),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        textfile.unlink(missing_ok=True)
        if completed.returncode or not output.is_file():
            raise RuntimeError(completed.stderr.strip() or "FFmpeg Flite synthesis failed")


class KokoroNarrationProvider:
    """Pinned, offline Kokoro voice with sentence-level pacing and silence."""

    def __init__(self, *, voice: str = "af_bella", speed: float = 0.92) -> None:
        self.voice = voice; self.speed = speed; self._pipeline = None; self._voice_path = None

    @property
    def name(self) -> str:
        return f"kokoro-local:hexgrad/Kokoro-82M@fbba31e67ad8:voice={self.voice}:speed={self.speed}:pacing=v2"

    def _load(self):
        if self._pipeline is not None: return self._pipeline
        from huggingface_hub import snapshot_download
        from kokoro import KModel, KPipeline
        snapshot = Path(snapshot_download(
            "hexgrad/Kokoro-82M", revision="fbba31e67ad83eb66394c926627e99d35abeb087",
            local_files_only=True,
        ))
        model = KModel(repo_id="hexgrad/Kokoro-82M", config=str(snapshot / "config.json"),
                       model=str(snapshot / "kokoro-v1_0.pth"))
        self._pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M", model=model)
        self._voice_path = snapshot / "voices" / f"{self.voice}.pt"
        return self._pipeline

    def synthesize(self, text: str, output: Path, *, trailing_pause_seconds: float = 0.65) -> dict:
        import numpy as np
        import soundfile as sf
        pipeline = self._load(); rate = 24000; cursor = 0.0; pieces = []; segments = []
        spoken_text = " ".join(text.replace("\ufffd", "—").split())
        sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", spoken_text) if x.strip()]
        for index, sentence in enumerate(sentences):
            generated = [result.audio.detach().cpu().numpy() for result in pipeline(
                sentence, voice=str(self._voice_path), speed=self.speed, split_pattern=None,
            ) if result.audio is not None]
            if not generated: raise RuntimeError(f"Kokoro returned no audio for sentence {index + 1}")
            speech = np.concatenate(generated).astype("float32"); start = cursor; cursor += len(speech) / rate
            segments.append({"text": sentence, "start_seconds": round(start, 3), "end_seconds": round(cursor, 3)})
            pieces.append(speech)
            pause = trailing_pause_seconds if index == len(sentences) - 1 else 0.35
            pieces.append(np.zeros(round(rate * pause), dtype="float32")); cursor += pause
        output.parent.mkdir(parents=True, exist_ok=True); raw = output.with_suffix(".raw.wav")
        sf.write(raw, np.concatenate(pieces), rate, subtype="PCM_16")
        completed = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
            "-af", "aresample=48000,loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", "-ac", "1",
            "-c:a", "pcm_s16le", str(output)], capture_output=True, text=True, check=False)
        raw.unlink(missing_ok=True)
        if completed.returncode: raise RuntimeError(completed.stderr.strip() or "Kokoro normalization failed")
        return {"segments": segments, "sentence_pause_seconds": 0.35,
                "trailing_pause_seconds": trailing_pause_seconds}


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as source:
        return source.getnframes() / source.getframerate()


def generate_narration_audio(
    narration: dict, root: Path, provider: NarrationProvider | None = None, *,
    previous: dict | None = None, maximum_attempts: int = 2,
    scene_end_ids: frozenset[str] = frozenset(),
) -> dict:
    if narration.get("status") != "adapted_draft":
        raise ValueError("audio generation requires adapted narration")
    selected = provider or KokoroNarrationProvider()
    prior = {item["narration_id"]: item for item in (previous or {}).get("clips", [])}
    clips, reused, regenerated = [], [], []
    for block in narration["blocks"]:
        identifier = block["narration_id"]
        dependency = hashlib.sha256(json.dumps({
            "text": block["adapted_text"], "provider": selected.name,
            "trailing_pause_seconds": 1.2 if identifier in scene_end_ids else 0.65,
        }, sort_keys=True).encode()).hexdigest()
        existing = prior.get(identifier)
        if existing:
            path = root / existing["path"]
            if (
                existing.get("dependency_sha256") == dependency and path.is_file()
                and sha256_file(path) == existing.get("sha256")
            ):
                clips.append(existing); reused.append(identifier); continue
        regenerated.append(identifier)
        relative = Path("audio/narration") / f"{identifier}.wav"
        output = root / relative
        attempts = []
        timing = None
        for attempt in range(1, maximum_attempts + 1):
            try:
                timing = selected.synthesize(
                    block["adapted_text"], output,
                    trailing_pause_seconds=1.2 if identifier in scene_end_ids else 0.65,
                )
                attempts.append({"attempt": attempt, "status": "generated", "error": None})
                break
            except Exception as error:
                attempts.append({"attempt": attempt, "status": "failed", "error": str(error)})
        else:
            raise ValueError(f"narration synthesis failed for {identifier}: {attempts[-1]['error']}")
        clips.append({
            "narration_id": identifier, "path": relative.as_posix(),
            "sha256": sha256_file(output), "duration_seconds": round(wav_duration(output), 3),
            "dependency_sha256": dependency, "provider": selected.name,
            "attempts": attempts, "status": "auto_accepted",
            "timing": timing,
        })
    return {
        "schema_version": 1, "status": "auto_accepted", "provider": selected.name,
        "source_sha256": narration["source_sha256"], "clips": clips,
        "regeneration": {"reused_ids": reused, "regenerated_ids": regenerated},
    }


def validate_narration_audio(audio: dict, narration: dict, root: Path) -> list[str]:
    issues = []
    if audio.get("status") != "auto_accepted": issues.append("narration audio is not accepted")
    if [x.get("narration_id") for x in audio.get("clips", [])] != [x["narration_id"] for x in narration["blocks"]]:
        issues.append("narration audio coverage is incomplete")
    for clip in audio.get("clips", []):
        path = root / str(clip.get("path") or "")
        if not path.is_file(): issues.append(f"missing narration clip: {clip.get('narration_id')}")
        elif sha256_file(path) != clip.get("sha256"): issues.append(f"narration hash mismatch: {clip.get('narration_id')}")
        if float(clip.get("duration_seconds", 0)) <= 0: issues.append(f"empty narration clip: {clip.get('narration_id')}")
        if str(clip.get("provider", "")).startswith("kokoro-local:"):
            timing = clip.get("timing") or {}; segments = timing.get("segments", [])
            if not segments: issues.append(f"missing sentence timing: {clip.get('narration_id')}")
            previous_end = 0.0
            for segment in segments:
                if "\ufffd" in segment.get("text", ""): issues.append(f"invalid spoken character: {clip.get('narration_id')}")
                if previous_end and float(segment["start_seconds"]) - previous_end < 0.3:
                    issues.append(f"sentence pause too short: {clip.get('narration_id')}")
                previous_end = float(segment["end_seconds"])
            if segments and float(clip["duration_seconds"]) - previous_end < 0.6:
                issues.append(f"trailing pause too short: {clip.get('narration_id')}")
    return issues
