"""Synthesize persistent cloned voices with XTTS-v2 and bounded retries.

The model is optional and licensed for non-commercial use under CPML.
"""

from __future__ import annotations


import argparse
import json
import os
import math
from pathlib import Path

try:
    from .generate_dub import media_duration
    from .synthesize_constrained import permitted_duration, stable_segment_id, trim_edge_silence
except ImportError:  # Direct script execution.
    from generate_dub import media_duration
    from synthesize_constrained import permitted_duration, stable_segment_id, trim_edge_silence

MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"


def install_pcm_loader() -> None:
    """Use SoundFile for PCM references when Torchaudio requires TorchCodec."""
    import numpy as np
    import soundfile as sf
    import torch
    from scipy.signal import resample_poly
    from TTS.tts.models import xtts

    def load_audio(path: str, sampling_rate: int) -> torch.Tensor:
        """Load a PCM WAV as mono and resample it without TorchCodec."""
        samples, source_rate = sf.read(path, dtype="float32", always_2d=True)
        mono = samples.mean(axis=1)
        if source_rate != sampling_rate:
            divisor = math.gcd(source_rate, sampling_rate)
            mono = resample_poly(mono, sampling_rate // divisor, source_rate // divisor)
        return torch.from_numpy(np.asarray(mono, dtype=np.float32)).unsqueeze(0).clamp_(-1, 1)

    xtts.load_audio = load_audio


def select_pilot(segments: list[dict], count: int) -> list[tuple[int, dict]]:
    """Select a speaker-diverse pilot, preferring longer expressive cues."""
    ranked = sorted(enumerate(segments), key=lambda item: float(item[1]["end"]) - float(item[1]["start"]), reverse=True)
    selected, seen = [], set()
    for item in ranked:
        speaker = item[1].get("speaker")
        if speaker not in seen:
            selected.append(item)
            seen.add(speaker)
        if len(selected) >= count:
            return sorted(selected)
    for item in ranked:
        if item not in selected:
            selected.append(item)
        if len(selected) >= count:
            break
    return sorted(selected)


def synthesize_xtts(
    script: dict, references: dict, output_dir: Path, language: str = "en",
    pilot_count: int = 0, tolerance: float = 1.06,
) -> tuple[dict, dict]:
    """Render cloned speech and reject clips outside their timing windows."""
    from TTS.api import TTS

    os.environ.setdefault("COQUI_TOS_AGREED", "1")
    install_pcm_loader()
    model = TTS(MODEL, progress_bar=True).to("cpu")
    indexed = list(enumerate(script["segments"]))
    if pilot_count:
        indexed = select_pilot(script["segments"], pilot_count)
    clips_dir = output_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    clips, results = [], []
    for index, segment in indexed:
        segment_id = stable_segment_id(segment, index)
        speaker = str(segment.get("speaker"))
        refs = [clip["path"] for clip in references.get("speakers", {}).get(speaker, {}).get("clips", [])]
        output = clips_dir / f"{segment_id}.wav"
        allowed = permitted_duration(segment)
        if output.is_file():
            duration = media_duration(output)
            status, error = "generated", None
        elif not refs:
            status, error, duration = "failed", f"no reference audio for {speaker}", 0.0
        else:
            try:
                model.tts_to_file(text=segment["text"], language=language, speaker_wav=refs, file_path=str(output), split_sentences=False)
                trim_edge_silence(output)
                duration = media_duration(output)
                status = "generated" if duration <= allowed * tolerance else "failed"
                error = None if status == "generated" else "XTTS clip exceeds its allowed speaking window"
                if status == "failed":
                    output.unlink(missing_ok=True)
                    duration = 0.0
            except Exception as caught:
                status, error, duration = "failed", str(caught), 0.0
                output.unlink(missing_ok=True)
        clips.append({
            "segment_id": segment_id, "start": segment["start"], "end": segment["end"],
            "text": segment["text"], "speaker": speaker, "voice": f"xtts:{speaker}",
            "audio_path": str(output.resolve()), "generated_duration": duration,
            "speed_ratio": 1.0, "status": status, "error": error,
        })
        results.append({"segment_id": segment_id, "speaker": speaker, "allowed_duration": allowed, "status": status, "error": error})
    manifest = {
        "schema_version": 1,
        "project_id": "xtts",
        "provider": "xtts-v2-duration-constrained",
        "target_language": language,
        "voices": sorted({clip["voice"] for clip in clips}),
        "clips": clips,
    }
    report = {"schema_version": 1, "backend": "xtts-v2", "processed": len(clips), "generated": sum(c["status"] == "generated" for c in clips), "failed": sum(c["status"] != "generated" for c in clips), "results": results}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "dub-manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (output_dir / "synthesis-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest, report


def main() -> None:
    """Parse CLI arguments and run expressive synthesis."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script", type=Path)
    parser.add_argument("references", type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path)
    parser.add_argument("--language", default="en")
    parser.add_argument("--pilot-count", type=int, default=0)
    parser.add_argument("--tolerance", type=float, default=1.06)
    args = parser.parse_args()
    _, report = synthesize_xtts(json.loads(args.script.read_text(encoding="utf-8")), json.loads(args.references.read_text(encoding="utf-8")), args.output, args.language, args.pilot_count, args.tolerance)
    print(json.dumps({key: report[key] for key in ("processed", "generated", "failed")}, indent=2))


if __name__ == "__main__":
    main()
