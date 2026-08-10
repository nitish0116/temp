"""Synthesize dialogue inside its speaking windows without post-processing tempo."""

from __future__ import annotations


import argparse
import array
import json
import wave
from pathlib import Path
from typing import Any

from piper import PiperVoice, SynthesisConfig

try:
    from .generate_dub import ensure_voice, media_duration
except ImportError:  # Direct script execution.
    from generate_dub import ensure_voice, media_duration


def stable_segment_id(segment: dict, index: int) -> str:
    """Return an existing segment ID or a deterministic sequence-based fallback."""
    return str(segment.get("id") or segment.get("segment_id") or f"seg-{index + 1:04d}")


def permitted_duration(segment: dict) -> float:
    """Return the step-5 speaking window, falling back to the cue duration."""
    constraint = segment.get("duration_constraint", {})
    return float(
        constraint.get(
            "available_seconds", float(segment["end"]) - float(segment["start"])
        )
    )


def next_length_scale(
    current_scale: float,
    measured_duration: float,
    allowed_duration: float,
    minimum_scale: float,
) -> float:
    """Choose a bounded native TTS duration scale with a small fit margin."""
    if measured_duration <= 0 or allowed_duration <= 0:
        return current_scale
    desired = current_scale * allowed_duration / measured_duration * 0.97
    return round(max(minimum_scale, min(current_scale, desired)), 4)


def active_sample_bounds(
    samples: list[int], threshold: int, padding_samples: int
) -> tuple[int, int]:
    """Return padded bounds around non-silent edge samples without cutting pauses."""
    active = [index for index, sample in enumerate(samples) if abs(sample) >= threshold]
    if not active:
        return 0, len(samples)
    return max(0, active[0] - padding_samples), min(
        len(samples), active[-1] + padding_samples + 1
    )


def trim_edge_silence(output_path: Path, padding_seconds: float = 0.04) -> None:
    """Trim only quiet WAV edges while retaining a small natural boundary pad."""
    with wave.open(str(output_path), "rb") as wav_file:
        params = wav_file.getparams()
        if params.sampwidth != 2 or params.nchannels != 1:
            return
        frames = wav_file.readframes(params.nframes)
    samples = array.array("h")
    samples.frombytes(frames)
    if not samples:
        return
    peak = max(abs(sample) for sample in samples)
    threshold = max(100, round(peak * 0.02))
    start, end = active_sample_bounds(
        samples.tolist(), threshold, round(params.framerate * padding_seconds)
    )
    if start == 0 and end == len(samples):
        return
    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setparams(params)
        wav_file.writeframes(samples[start:end].tobytes())


def synthesize_attempt(
    voice: PiperVoice,
    text: str,
    output_path: Path,
    length_scale: float,
) -> float:
    """Render one Piper attempt and return its measured WAV duration."""
    with wave.open(str(output_path), "wb") as wav_file:
        voice.synthesize_wav(
            text,
            wav_file,
            syn_config=SynthesisConfig(length_scale=length_scale),
        )
    trim_edge_silence(output_path)
    return media_duration(output_path)


def synthesize_segment(
    voice: PiperVoice,
    segment: dict,
    segment_id: str,
    voice_name: str,
    output_path: Path,
    retries: int,
    minimum_length_scale: float,
    tolerance: float,
) -> tuple[dict, dict]:
    """Regenerate a cue with bounded native prosody until its audio fits."""
    allowed = permitted_duration(segment)
    scale = 1.0
    attempts = []
    error: Exception | None = None
    for attempt_number in range(1, retries + 2):
        try:
            duration = synthesize_attempt(
                voice, segment["text"], output_path, scale
            )
            ratio = duration / allowed if allowed > 0 else float("inf")
            attempts.append(
                {
                    "attempt": attempt_number,
                    "length_scale": scale,
                    "duration": duration,
                    "ratio": round(ratio, 4),
                }
            )
            if duration <= allowed * tolerance:
                clip = {
                    "segment_id": segment_id,
                    "start": segment["start"],
                    "end": segment["end"],
                    "text": segment["text"],
                    "voice": voice_name,
                    "audio_path": str(output_path.resolve()),
                    "generated_duration": duration,
                    "speed_ratio": 1.0,
                    "status": "generated",
                    "error": None,
                }
                return clip, {
                    "segment_id": segment_id,
                    "speaker": segment.get("speaker"),
                    "allowed_duration": allowed,
                    "status": "fits",
                    "regenerated": attempt_number > 1,
                    "attempts": attempts,
                }
            new_scale = next_length_scale(
                scale, duration, allowed * tolerance, minimum_length_scale
            )
            if new_scale == scale:
                break
            scale = new_scale
        except Exception as caught:
            error = caught
            attempts.append(
                {
                    "attempt": attempt_number,
                    "length_scale": scale,
                    "error": str(caught),
                }
            )
    output_path.unlink(missing_ok=True)
    message = str(error) if error else "clip exceeds its allowed speaking window"
    clip = {
        "segment_id": segment_id,
        "start": segment["start"],
        "end": segment["end"],
        "text": segment["text"],
        "voice": voice_name,
        "audio_path": str(output_path.resolve()),
        "generated_duration": 0.0,
        "speed_ratio": 1.0,
        "status": "failed",
        "error": message,
    }
    return clip, {
        "segment_id": segment_id,
        "speaker": segment.get("speaker"),
        "allowed_duration": allowed,
        "status": "failed",
        "regenerated": len(attempts) > 1,
        "attempts": attempts,
        "error": message,
    }


def synthesize_constrained(
    script: dict,
    output_dir: Path,
    models_dir: Path,
    retries: int,
    minimum_length_scale: float,
    tolerance: float,
    project_id: str,
) -> tuple[dict, dict]:
    """Synthesize every assigned voice and return manifest and blocking report."""
    segments = script["segments"]
    if not segments or any(not segment.get("voice") for segment in segments):
        raise ValueError("Every constrained segment must have an assigned voice")
    voice_names = sorted({segment["voice"] for segment in segments})
    voices: dict[str, Any] = {
        name: PiperVoice.load(ensure_voice(name, models_dir)) for name in voice_names
    }
    clips_dir = output_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    clips = []
    results = []
    for index, segment in enumerate(segments):
        segment_id = stable_segment_id(segment, index)
        voice_name = segment["voice"]
        clip, result = synthesize_segment(
            voices[voice_name],
            segment,
            segment_id,
            voice_name,
            clips_dir / f"{segment_id}.wav",
            retries,
            minimum_length_scale,
            tolerance,
        )
        clips.append(clip)
        results.append(result)
    failed = [result for result in results if result["status"] == "failed"]
    manifest = {
        "schema_version": 1,
        "project_id": project_id,
        "provider": "piper-duration-constrained",
        "target_language": script.get("output_language", script.get("language", "en")),
        "voices": voice_names,
        "clips": clips,
    }
    report = {
        "schema_version": 1,
        "automatic": True,
        "status": "passed" if not failed else "failed",
        "segment_count": len(segments),
        "fitted_segment_count": len(segments) - len(failed),
        "failed_segment_count": len(failed),
        "regenerated_segment_count": sum(result["regenerated"] for result in results),
        "minimum_length_scale": minimum_length_scale,
        "duration_tolerance": tolerance,
        "post_processing_tempo_used": False,
        "segments": results,
    }
    return manifest, report


def main() -> None:
    """Parse CLI options, write constrained clips, and reject unresolved overruns."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script", type=Path)
    parser.add_argument("-o", "--output-dir", type=Path, required=True)
    parser.add_argument("--models-dir", type=Path)
    parser.add_argument("--project-id", default="auto-review")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--minimum-length-scale", type=float, default=0.85)
    parser.add_argument("--tolerance", type=float, default=1.0)
    args = parser.parse_args()
    script = json.loads(args.script.read_text(encoding="utf-8"))
    models_dir = args.models_dir or args.output_dir / "models"
    manifest, report = synthesize_constrained(
        script,
        args.output_dir,
        models_dir,
        args.retries,
        args.minimum_length_scale,
        args.tolerance,
        args.project_id,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "dub-manifest.json"
    report_path = args.output_dir / "synthesis-report.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Duration-constrained synthesis: {report['status']} "
        f"({report['fitted_segment_count']}/{report['segment_count']} fit; "
        f"{report['regenerated_segment_count']} regenerated)"
    )
    if report["status"] != "passed":
        raise RuntimeError(
            f"{report['failed_segment_count']} clips remain outside allowed windows"
        )


if __name__ == "__main__":
    main()
