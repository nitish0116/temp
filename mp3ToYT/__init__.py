"""Create YouTube-ready MP4 videos from audiobook audio files.

The package exposes the conversion helpers for programmatic use. Run the CLI
with ``python -m mp3ToYT`` from the repository root.
"""

from .mp3_to_youtube import (
    AUDIO_BITRATE,
    AUDIO_CHANNELS,
    AUDIO_SAMPLE_RATE,
    DEFAULT_RESOLUTION,
    RESOLUTIONS,
    check_tools,
    clean_stem,
    collect_audio_inputs,
    convert,
    estimate_size,
    fmt_bytes,
    fmt_duration,
    main,
    probe,
)

__all__ = [
    "AUDIO_BITRATE",
    "AUDIO_CHANNELS",
    "AUDIO_SAMPLE_RATE",
    "DEFAULT_RESOLUTION",
    "RESOLUTIONS",
    "check_tools",
    "clean_stem",
    "collect_audio_inputs",
    "convert",
    "estimate_size",
    "fmt_bytes",
    "fmt_duration",
    "main",
    "probe",
]

