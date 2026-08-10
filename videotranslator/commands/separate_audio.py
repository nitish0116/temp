"""Separate source vocals from accompaniment with a local Demucs model."""

from __future__ import annotations


import argparse
import json
import subprocess
from pathlib import Path

import soundfile
import torch
from demucs.apply import apply_model
from demucs.audio import AudioFile
from demucs.pretrained import get_model


def separate_audio(video: Path, output_dir: Path, model: str = "htdemucs", device: str = "cpu", shifts: int = 1) -> dict:
    """Extract full-quality audio and write ``vocals.wav`` and ``accompaniment.wav``.

    Example: separating a film soundtrack lets the dub replace speech without
    lowering music and effects whenever a translated line is spoken.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    source_mix = output_dir / "source-mix.wav"
    vocals = output_dir / "vocals.wav"
    accompaniment = output_dir / "accompaniment.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(source_mix)],
        check=True,
    )
    separator = get_model(model)
    separator.to(device)
    separator.eval()
    waveform = AudioFile(source_mix).read(
        streams=0,
        samplerate=separator.samplerate,
        channels=separator.audio_channels,
    )
    reference = waveform.mean(0)
    mean = reference.mean()
    standard_deviation = reference.std()
    normalized = (waveform - mean) / standard_deviation
    with torch.no_grad():
        sources = apply_model(
            separator,
            normalized[None],
            device=device,
            shifts=shifts,
            split=True,
            overlap=0.25,
            progress=True,
        )[0]
    sources = sources * standard_deviation + mean
    vocal_index = separator.sources.index("vocals")
    vocal_audio = sources[vocal_index]
    accompaniment_audio = sources.sum(0) - vocal_audio
    soundfile.write(vocals, vocal_audio.cpu().numpy().T, separator.samplerate, subtype="PCM_16")
    soundfile.write(accompaniment, accompaniment_audio.cpu().numpy().T, separator.samplerate, subtype="PCM_16")
    report = {
        "schema_version": 1,
        "method": "demucs-two-stems",
        "model": model,
        "device": device,
        "source_mix": str(source_mix.resolve()),
        "vocals": str(vocals.resolve()),
        "accompaniment": str(accompaniment.resolve()),
    }
    (output_dir / "separation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    """Parse CLI options and separate the source soundtrack."""
    parser = argparse.ArgumentParser(description="Separate vocals from a video soundtrack.")
    parser.add_argument("video", type=Path)
    parser.add_argument("-o", "--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="htdemucs")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--shifts", type=int, default=1)
    args = parser.parse_args()
    report = separate_audio(args.video, args.output_dir, args.model, args.device, args.shifts)
    print(f"Vocals: {report['vocals']}")
    print(f"Accompaniment: {report['accompaniment']}")


if __name__ == "__main__":
    main()
