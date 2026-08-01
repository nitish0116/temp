"""Convert Markdown text into bounded, speakable narration chunks."""

from __future__ import annotations

from pathlib import Path
import re


HEADING_RE = re.compile(
    r"^(?:"
    r"(?:prologue|epilogue|afterword|foreword|preface|introduction|conclusion"
    r"|appendix|appendices|glossary|index|notes|footnotes|bibliography"
    r"|acknowledg(?:e)?ments|about(?:\s+the\s+author)?|newsletter"
    r"|contents|table\s+of\s+contents|copyright|dedication|illustrations?)"
    r"|(?:chapter|section|part|book|volume|act|scene|episode|interlude"
    r"|intermission|side\s+story|story|arc|appendix)\b(?:[\s:.-].*)?"
    r"|(?:[ivxlcdm]+|\d+)[\s:.-]+.+"
    r")\s*$",
    re.IGNORECASE,
)
ORNAMENT_RE = re.compile(
    r"[\u25A0-\u25FF\u2600-\u26FF\u2700-\u27BF\u2500-\u257F"
    r"\u2580-\u259F\u2B00-\u2BFF\uFFF0-\uFFFF]"
)
OCR_JUNK_RE = re.compile(r"(?:\s+(?=\S*[A-Za-z])(?=\S*[0-9])[A-Za-z0-9_-]{5,})+$")
FENCE_RE = re.compile(r"^\s*(?:`{3,}|~{3,})[^`~]*\s*$")
LEADING_HASH_RE = re.compile(r"^#{1,6}[ \t]*")
MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
SENTENCE_END_RE = re.compile(r"[.!?…:;\"')\]]\s*$")
WORD_RE = re.compile(r"\b[^\W_]+(?:['’][^\W_]+)*\b", re.UNICODE)

MIN_SPEAKABLE_ALPHA = 4
DEFAULT_NARRATION_WORDS_PER_MINUTE = 150.0
CHAPTER_END_MARKER = "[CHAPTER_END]"


def is_speakable_chunk(text: str, minimum_alpha: int = MIN_SPEAKABLE_ALPHA) -> bool:
    """Return whether *text* contains enough letters for a TTS request."""
    return sum(character.isalpha() for character in text) >= minimum_alpha


def is_decorative_separator(text: str) -> bool:
    """Return whether a line contains only ornaments and Markdown wrappers."""
    without_ornaments = ORNAMENT_RE.sub("", text)
    without_wrappers = re.sub(r"[\s*_~`.,;:!?=+\-]+", "", without_ornaments)
    return not without_wrappers


def split_speech_chunk(text: str, max_length: int) -> list[str]:
    """Split text at natural boundaries without exceeding *max_length*.

    Sentence and phrase punctuation is preferred.  A word boundary is used as
    the fallback, and a hard character boundary is used only for an individual
    token longer than the requested limit.
    """
    if max_length < 1:
        raise ValueError("max_length must be at least 1")

    remaining = re.sub(r"\s+", " ", text).strip()
    parts: list[str] = []
    while len(remaining) > max_length:
        candidate = remaining[:max_length]
        boundary = max(candidate.rfind(mark) for mark in ".!?;,:")
        if boundary < max_length // 2:
            boundary = candidate.rfind(" ")
        if boundary < 0:
            boundary = max_length - 1

        chunk = remaining[: boundary + 1].strip()
        if chunk:
            parts.append(chunk)
        remaining = remaining[boundary + 1 :].strip()

    if remaining:
        parts.append(remaining)
    return parts


def _strip_ocr_suffix(line: str) -> str:
    """Remove repeated mixed alphanumeric OCR junk from a line ending."""
    previous = None
    while line and line != previous:
        previous = line
        line = OCR_JUNK_RE.sub("", line).rstrip()
    return line


def _append_filtered_chunks(chunks: list[str]) -> list[str]:
    """Merge undersized fragments into neighboring speakable chunks."""
    filtered: list[str] = []
    for chunk in chunks:
        if chunk == CHAPTER_END_MARKER or is_speakable_chunk(chunk):
            filtered.append(chunk)
        elif filtered:
            filtered[-1] = f"{filtered[-1].rstrip()} {chunk.strip()}"
    return filtered


def narration_paragraphs(
    markdown_text: str,
    chunk_size: int,
    chapter_markers: bool = False,
) -> list[str]:
    """Prepare Markdown as bounded narration chunks.

    Code-fence markers, heading hashes, decorative separators, and trailing OCR
    noise are removed.  Hard-wrapped prose is rejoined into paragraphs.  When
    requested, ``CHAPTER_END_MARKER`` items are inserted before each recognized
    heading after the first and after the final chapter. Audio backends decide
    how to render those markers.
    """
    paragraph: list[str] = []
    prepared: list[str] = []
    pending_prefix = ""
    seen_chapter = False

    def flush() -> None:
        if not paragraph:
            return
        joined = re.sub(r"\s+", " ", " ".join(paragraph)).strip()
        if joined:
            prepared.append(joined)
        paragraph.clear()

    for raw in markdown_text.splitlines():
        if FENCE_RE.match(raw):
            continue
        if is_decorative_separator(raw):
            flush()
            continue

        line = ORNAMENT_RE.sub("", raw).strip()
        if not line:
            flush()
            continue
        line = LEADING_HASH_RE.sub("", line).strip()
        if not line:
            flush()
            continue

        if HEADING_RE.match(line):
            flush()
            if chapter_markers and seen_chapter:
                if prepared[-1] != CHAPTER_END_MARKER:
                    prepared.append(CHAPTER_END_MARKER)
            tail = re.search(r"\s+([IA])$", line)
            if tail:
                pending_prefix = f"{tail.group(1)} "
                line = line[: tail.start()].rstrip()
            prepared.append(line)
            seen_chapter = True
            continue

        line = _strip_ocr_suffix(line)
        if not line:
            flush()
            continue
        if pending_prefix and not paragraph:
            line = pending_prefix + line
            pending_prefix = ""
        paragraph.append(line)
        if SENTENCE_END_RE.search(line):
            flush()

    flush()
    if chapter_markers and seen_chapter and prepared:
        if prepared[-1] != CHAPTER_END_MARKER:
            prepared.append(CHAPTER_END_MARKER)

    chunks = [
        chunk
        for item in prepared
        for chunk in split_speech_chunk(item, chunk_size)
    ]
    return _append_filtered_chunks(chunks)


def narration_paragraphs_with_scene_markers(
    markdown_text: str,
    chunk_size: int,
) -> tuple[list[str], dict[int, str]]:
    """Prepare narration and map each Markdown heading to its chunk index.

    Unlike :func:`narration_paragraphs`, every Markdown heading is a scene
    boundary.  This supports books whose headings are dates or locations rather
    than conventional ``Chapter N`` labels.
    """
    paragraph: list[str] = []
    prepared: list[str | tuple[str, str]] = []
    pending_prefix = ""

    def flush() -> None:
        if not paragraph:
            return
        joined = re.sub(r"\s+", " ", " ".join(paragraph)).strip()
        if joined:
            prepared.append(joined)
        paragraph.clear()

    for raw in markdown_text.splitlines():
        if FENCE_RE.match(raw):
            continue
        if is_decorative_separator(raw):
            flush()
            continue
        line = ORNAMENT_RE.sub("", raw).strip()
        if not line:
            flush()
            continue

        heading = MARKDOWN_HEADING_RE.match(line)
        if heading:
            flush()
            title = _strip_ocr_suffix(heading.group(2).strip()).strip()
            if title:
                prepared.append(("scene", title))
            continue

        line = LEADING_HASH_RE.sub("", line).strip()
        if not line:
            flush()
            continue
        if HEADING_RE.match(line):
            flush()
            tail = re.search(r"\s+([IA])$", line)
            if tail:
                pending_prefix = f"{tail.group(1)} "
                line = line[: tail.start()].rstrip()
            prepared.append(line)
            continue

        line = _strip_ocr_suffix(line)
        if not line:
            flush()
            continue
        if pending_prefix and not paragraph:
            line = pending_prefix + line
            pending_prefix = ""
        paragraph.append(line)
        if SENTENCE_END_RE.search(line):
            flush()

    flush()

    raw_chunks: list[str] = []
    raw_scene_before: dict[int, str] = {}
    pending_scene: str | None = None
    for item in prepared:
        if isinstance(item, tuple):
            pending_scene = item[1]
            continue
        subchunks = split_speech_chunk(item, chunk_size)
        if subchunks and pending_scene is not None:
            raw_scene_before[len(raw_chunks)] = pending_scene
            pending_scene = None
        raw_chunks.extend(subchunks)

    filtered: list[str] = []
    old_to_new: dict[int, int] = {}
    for old_index, chunk in enumerate(raw_chunks):
        if is_speakable_chunk(chunk):
            old_to_new[old_index] = len(filtered)
            filtered.append(chunk)
        elif filtered:
            old_to_new[old_index] = len(filtered) - 1
            filtered[-1] = f"{filtered[-1].rstrip()} {chunk.strip()}"

    scene_map: dict[int, str] = {}
    for old_index, title in raw_scene_before.items():
        for candidate_index in range(old_index, len(raw_chunks)):
            if candidate_index in old_to_new:
                scene_map.setdefault(old_to_new[candidate_index], title)
                break
    return filtered, scene_map


def choose_chunk_size_and_chunks(
    markdown_text: str,
    backend: str,
    requested_chunk_size: int | None,
    edge_workers: int,
    quiet: bool,
    chapter_markers: bool = False,
) -> tuple[int, list[str]]:
    """Choose a backend-aware chunk size and return prepared chunks."""
    if requested_chunk_size is not None:
        size = max(400, requested_chunk_size)
        return size, narration_paragraphs(markdown_text, size, chapter_markers)

    if backend == "edge":
        size, min_size, max_size = 2600, 1200, 6000
        target_chunks = max(1200, 1400 + edge_workers * 120)
    elif backend == "sapi":
        size, min_size, max_size, target_chunks = 2200, 1200, 4500, 1000
    else:
        raise ValueError(f"Unsupported narration backend: {backend}")

    chunks = narration_paragraphs(markdown_text, size, chapter_markers)
    for _ in range(6):
        chunk_count = len(chunks)
        if not chunk_count:
            break
        new_size = size
        if chunk_count > int(target_chunks * 1.8) and size < max_size:
            new_size = min(max_size, int(size * 1.45))
        elif chunk_count > target_chunks and size < max_size:
            new_size = min(max_size, int(size * 1.22))
        elif chunk_count < int(target_chunks * 0.35) and size > min_size:
            new_size = max(min_size, int(size * 0.88))
        if new_size == size:
            break
        size = new_size
        chunks = narration_paragraphs(markdown_text, size, chapter_markers)

    if not quiet:
        print(f"[STEP] Auto-selected chunk size: {size} (chunks: {len(chunks)})")
    return size, chunks


def estimate_mp3_duration(
    markdown: str | Path,
    words_per_minute: float = DEFAULT_NARRATION_WORDS_PER_MINUTE,
    chapter_markers: bool = False,
    chapter_marker_duration: float = 2.0,
) -> float:
    """Estimate MP3 playback duration in seconds from Markdown content."""
    if words_per_minute <= 0:
        raise ValueError("words_per_minute must be greater than zero")
    if chapter_marker_duration < 0:
        raise ValueError("chapter_marker_duration cannot be negative")

    markdown_text = (
        markdown.read_text(encoding="utf-8")
        if isinstance(markdown, Path)
        else markdown
    )
    chunks = narration_paragraphs(markdown_text, 6000, chapter_markers)
    spoken_words = sum(
        len(WORD_RE.findall(chunk))
        for chunk in chunks
        if chunk != CHAPTER_END_MARKER
    )
    marker_count = chunks.count(CHAPTER_END_MARKER)
    return spoken_words / words_per_minute * 60 + marker_count * chapter_marker_duration


def format_duration(seconds: float) -> str:
    """Format seconds as ``H:MM:SS`` using nearest-second rounding."""
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{remaining_seconds:02d}"
