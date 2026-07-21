"""Regression tests for Markdown parsing and audio target preparation."""

from argparse import Namespace
from pathlib import Path

from md_to_audio import (
    clean_stem,
    collect_input_paths,
    default_output_path,
    escape_ssml_text,
    estimate_chunk_durations_ms,
    is_speakable_chunk,
    narration_paragraphs,
    source_output_stem,
    split_speech_chunk,
)


def test_audio_boundary_escapes_ssml_once_and_skips_invalid_chunks():
    """Escape SSML once and reject chunks unsuitable for audio generation."""
    assert escape_ssml_text("R&D <test> &amp; done") == (
        "R&amp;D &lt;test&gt; &amp; done"
    )
    assert not is_speakable_chunk("... 1")
    assert is_speakable_chunk("Okay.")


def test_narration_paragraphs_preserve_content_and_bound_chunks():
    text = "# Chapter 1\n\n**Bold** narration & detail.\n\n- list item"
    chunks = narration_paragraphs(text, chunk_size=30)
    assert chunks
    assert all(len(chunk) <= 30 for chunk in chunks)
    assert "Bold" in " ".join(chunks)


def test_chunk_splitting_and_duration_estimation_preserve_totals():
    chunks = split_speech_chunk("One sentence. Another sentence follows.", 20)
    assert len(chunks) >= 2
    durations = estimate_chunk_durations_ms(chunks, 10_000)
    assert sum(durations) == 10_000
    assert estimate_chunk_durations_ms([], 0) == []


def test_audio_input_discovery_and_output_naming(tmp_path):
    first = tmp_path / "Book_[Kobo].md"
    second = tmp_path / "Volume_02.md"
    first.touch()
    second.touch()
    assert collect_input_paths(first) == [first]
    assert collect_input_paths(tmp_path) == [first, second]
    assert clean_stem(first) == "Book"
    assert source_output_stem(first) == "Book_[Kobo]"
    assert default_output_path(first, ".mp3").name == "Book_[Kobo].mp3"
