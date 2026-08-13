"""Generate one target-language clip per approved segment using local Piper TTS."""

from __future__ import annotations


import argparse
import json
import os
import shutil
import subprocess
import wave
from pathlib import Path
from urllib.request import urlopen

try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

from piper import PiperVoice, SynthesisConfig
from piper.download_voices import VOICES_JSON, download_voice


def media_duration(path: Path) -> float:
    """Return media duration in seconds using FFprobe."""
    if shutil.which("ffprobe") is None:
        raise RuntimeError("FFprobe is not installed or is not available on PATH")
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return round(float(result.stdout.strip()), 3)


def select_voice(target_language: str, requested_voice: str | None) -> str:
    """Select a deterministic Piper voice name for a language or locale.

    An explicit voice always wins. Otherwise the public Piper voice index is
    queried and a medium-quality voice is preferred. No transcript text is sent.
    """
    if requested_voice:
        return requested_voice
    candidates = available_voices(target_language)
    if not candidates:
        raise ValueError(f"No Piper voice found for target language: {target_language}")
    return candidates[0]


def available_voices(target_language: str) -> list[str]:
    """Return deterministic medium-first Piper voice names for a language."""
    normalized = target_language.replace("-", "_").lower()
    language = normalized.split("_", 1)[0]
    with urlopen(VOICES_JSON) as response:
        names = list(json.load(response))
    candidates = [
        name
        for name in names
        if name.lower().startswith(f"{normalized}-")
        or ("_" not in normalized and name.lower().startswith(f"{language}_"))
    ]
    candidates.sort(key=lambda name: ("-medium" not in name, name))
    return candidates


def ensure_voice(voice_name: str, models_dir: Path) -> Path:
    """Download a public Piper voice model when it is not already cached."""
    models_dir.mkdir(parents=True, exist_ok=True)
    model_path = models_dir / f"{voice_name}.onnx"
    config_path = models_dir / f"{voice_name}.onnx.json"
    if not model_path.is_file() or not config_path.is_file():
        download_voice(voice_name, models_dir)
    return model_path


def piper_models_dir(output_dir: Path) -> Path:
    """Return the shared Piper cache override or the run-local fallback."""
    return Path(os.environ.get("PIPER_MODELS_DIR", str(output_dir / "models")))


def rate_to_length_scale(rate: str) -> float:
    """Convert a percentage speech rate into Piper's duration scale.

    Example: ``+10%`` produces ``1 / 1.1``, making speech roughly ten percent
    faster. ``+0%`` returns ``1.0``.
    """
    percent = int(rate.removesuffix("%"))
    factor = 1.0 + (percent / 100.0)
    if factor <= 0:
        raise ValueError("Speech rate must be greater than -100%")
    return 1.0 / factor


def generate_clip(
    piper_voice: PiperVoice,
    segment: dict,
    output_path: Path,
    voice_name: str,
    synthesis_config: SynthesisConfig,
    retries: int,
) -> dict:
    """Synthesize and measure one local WAV clip with automatic retries."""
    error: Exception | None = None
    for _attempt in range(retries):
        try:
            with wave.open(str(output_path), "wb") as wav_file:
                piper_voice.synthesize_wav(
                    segment["text"], wav_file, syn_config=synthesis_config
                )
            duration = media_duration(output_path)
            window = float(segment["end"]) - float(segment["start"])
            return {
                "segment_id": segment["id"],
                "start": segment["start"],
                "end": segment["end"],
                "text": segment["text"],
                "voice": voice_name,
                "audio_path": str(output_path.resolve()),
                "generated_duration": duration,
                "speed_ratio": round(duration / window, 4),
                "status": "generated",
                "error": None,
            }
        except Exception as caught:
            error = caught
            output_path.unlink(missing_ok=True)
    return {
        "segment_id": segment["id"],
        "start": segment["start"],
        "end": segment["end"],
        "text": segment["text"],
        "voice": voice_name,
        "audio_path": str(output_path.resolve()),
        "generated_duration": 0.0,
        "speed_ratio": 1.0,
        "status": "failed",
        "error": str(error),
    }


def generate_dub(
    approved_script: dict,
    output_dir: Path,
    target_language: str,
    requested_voice: str | None,
    rate: str,
    retries: int,
) -> dict:
    """Generate or reuse local clips and return their complete dub manifest."""
    if approved_script.get("approval", {}).get("status") != "approved":
        raise ValueError("TTS input must be an automatically approved script")
    default_voice = select_voice(target_language, requested_voice)
    voice_names = sorted(
        {segment.get("voice") or default_voice for segment in approved_script["segments"]}
    )
    models_dir = piper_models_dir(output_dir)
    piper_voices = {
        voice_name: PiperVoice.load(ensure_voice(voice_name, models_dir))
        for voice_name in voice_names
    }
    synthesis_config = SynthesisConfig(length_scale=rate_to_length_scale(rate))
    clips_dir = output_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    previous_path = output_dir / "dub-manifest.json"
    previous = {}
    if previous_path.is_file():
        old_manifest = json.loads(previous_path.read_text(encoding="utf-8"))
        previous = {clip["segment_id"]: clip for clip in old_manifest.get("clips", [])}
    clips = []
    for segment in approved_script["segments"]:
        voice_name = segment.get("voice") or default_voice
        path = clips_dir / f"{segment['id']}.wav"
        old = previous.get(segment["id"])
        cache_matches = (
            old
            and old.get("text") == segment["text"]
            and old.get("voice") == voice_name
            and old.get("status") == "generated"
        )
        if cache_matches and path.is_file() and path.stat().st_size > 0:
            duration = media_duration(path)
            window = float(segment["end"]) - float(segment["start"])
            clips.append(
                {
                    "segment_id": segment["id"], "start": segment["start"],
                    "end": segment["end"], "text": segment["text"],
                    "voice": voice_name, "audio_path": str(path.resolve()),
                    "generated_duration": duration,
                    "speed_ratio": round(duration / window, 4),
                    "status": "generated", "error": None,
                }
            )
            continue
        clips.append(
            generate_clip(
                piper_voices[voice_name], segment, path, voice_name, synthesis_config, retries
            )
        )
    return {
        "schema_version": 1,
        "project_id": approved_script["project_id"],
        "provider": "piper",
        "target_language": target_language,
        "voices": voice_names,
        "clips": clips,
    }


def parse_args() -> argparse.Namespace:
    """Parse approved-script, target-language, voice, and generation options."""
    parser = argparse.ArgumentParser(description="Generate local target-language dub clips.")
    parser.add_argument("approved_script", type=Path)
    parser.add_argument("-o", "--output-dir", type=Path, required=True)
    parser.add_argument("--target-language", default="en")
    parser.add_argument("--voice", help="Piper voice name; auto-selected when omitted")
    parser.add_argument("--rate", default="+0%")
    parser.add_argument("--retries", type=int, default=3)
    return parser.parse_args()


def main() -> None:
    """Generate clips, persist the manifest, and fail if any clip failed."""
    args = parse_args()
    approved = json.loads(args.approved_script.read_text(encoding="utf-8"))
    manifest = generate_dub(
        approved, args.output_dir, args.target_language, args.voice, args.rate, args.retries
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "dub-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    failed = [clip for clip in manifest["clips"] if clip["status"] == "failed"]
    print(f"Generated {len(manifest['clips']) - len(failed)}/{len(manifest['clips'])} clips using {len(manifest['voices'])} voices")
    print(f"Dub manifest: {manifest_path.resolve()}")
    if failed:
        raise RuntimeError(f"TTS failed for {len(failed)} segments; see dub manifest")


if __name__ == "__main__":
    main()
