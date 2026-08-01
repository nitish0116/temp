"""Reusable building blocks for the :mod:`md_to_audio` command-line tools.

The top-level scripts remain the supported command-line entry points.  This
package contains backend-independent code that can also be imported by tests or
other repository tools without initializing SAPI, Edge TTS, or ffmpeg.
"""

from .narration import (
    CHAPTER_END_MARKER,
    DEFAULT_NARRATION_WORDS_PER_MINUTE,
    choose_chunk_size_and_chunks,
    estimate_mp3_duration,
    format_duration,
    is_speakable_chunk,
    narration_paragraphs,
    narration_paragraphs_with_scene_markers,
    split_speech_chunk,
)

__all__ = [
    "CHAPTER_END_MARKER",
    "DEFAULT_NARRATION_WORDS_PER_MINUTE",
    "choose_chunk_size_and_chunks",
    "estimate_mp3_duration",
    "format_duration",
    "is_speakable_chunk",
    "narration_paragraphs",
    "narration_paragraphs_with_scene_markers",
    "split_speech_chunk",
]
