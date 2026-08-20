"""Offline narration synthesis with retry, hashing, and selective reuse."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Protocol

from .artifacts import sha256_file


class NarrationProvider(Protocol):
    name: str

    def synthesize(self, text: str, output: Path) -> None: ...


class DeterministicToneProvider:
    name = "deterministic-tone-fixture-v1"

    def synthesize(self, text: str, output: Path) -> None:
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

    def synthesize(self, text: str, output: Path) -> None:
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


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as source:
        return source.getnframes() / source.getframerate()


def generate_narration_audio(
    narration: dict, root: Path, provider: NarrationProvider | None = None, *,
    previous: dict | None = None, maximum_attempts: int = 2,
) -> dict:
    if narration.get("status") != "adapted_draft":
        raise ValueError("audio generation requires adapted narration")
    selected = provider or FfmpegFliteNarrationProvider()
    prior = {item["narration_id"]: item for item in (previous or {}).get("clips", [])}
    clips, reused, regenerated = [], [], []
    for block in narration["blocks"]:
        identifier = block["narration_id"]
        dependency = hashlib.sha256(json.dumps({
            "text": block["adapted_text"], "provider": selected.name,
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
        for attempt in range(1, maximum_attempts + 1):
            try:
                selected.synthesize(block["adapted_text"], output)
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
    return issues
