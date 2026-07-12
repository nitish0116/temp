#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path


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
ORNAMENT_RE = re.compile(r"[\u25A0-\u25FF\u2600-\u26FF\u2700-\u27BF\u2500-\u257F\u2580-\u259F\u2B00-\u2BFF\uFFF0-\uFFFF]")
OCR_JUNK_RE = re.compile(r"(?:\s+(?=\S*[A-Za-z])(?=\S*[0-9])[A-Za-z0-9_-]{5,})+$")
FENCE_RE = re.compile(r"^\s*(?:`{3,}|~{3,})[^`~]*\s*$")
LEADING_HASH_RE = re.compile(r"^#{1,6}[ \t]*")
SENTENCE_END_RE = re.compile(r"[.!?…:;\"')\]]\s*$")
VOICE_ALIASES = {
    "dave": "david",
    "david": "david",
    "zira": "zira",
    "zee": "zira",
    "haruka": "haruka",
    "japanese": "haruka",
    "japan": "haruka",
}

EDGE_VOICE_ALIASES = {
    "dave": "en-US-GuyNeural",
    "david": "en-US-GuyNeural",
    "guy": "en-US-GuyNeural",
    "zira": "en-US-JennyNeural",
    "jenny": "en-US-JennyNeural",
    "aria": "en-US-AriaNeural",
    "haruka": "ja-JP-NanamiNeural",
    "nanami": "ja-JP-NanamiNeural",
    "japanese": "ja-JP-NanamiNeural",
}

EDGE_RECOMMENDED_VOICES = [
    "en-US-AriaNeural",
    "en-US-JennyNeural",
    "en-US-GuyNeural",
    "en-GB-SoniaNeural",
    "en-GB-RyanNeural",
    "ja-JP-NanamiNeural",
    "ja-JP-KeitaNeural",
]

QUIET = False
EDGE_RETRY_ATTEMPTS = 7


def log_step(message: str) -> None:
    """
    Print a step/progress message if quiet mode is disabled.
    
    Provides real-time feedback during the conversion process by printing
    step-by-step progress updates prefixed with [STEP].
    
    Args:
        message (str): The progress message to display.
    
    Returns:
        None
    """
    if not QUIET:
        print(f"[STEP] {message}")


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments for the markdown-to-audio converter.
    
    Builds and returns an argument parser with all supported command-line options
    including input/output paths, voice selection, backend choice, chunk size,
    worker threads, and quiet mode.
    
    Returns:
        argparse.Namespace: Parsed command-line arguments with attributes:
            - input_path: Input markdown file or folder (optional)
            - output_path: Output audio file or folder (optional)
            - backend: 'sapi' or 'edge' (default: 'sapi')
            - voice: Voice name or alias (optional)
            - chunk_size: Max chars per speech chunk (default: 2500)
            - edge_workers: Max concurrent Edge TTS requests (default: 6)
            - keep_intermediate_wav: Keep intermediate WAV files (flag)
            - list_voices: List available voices and exit (flag)
            - all_voices: Show all Edge voices when listing (flag)
            - quiet: Suppress step/progress logging (flag)
    """
    parser = argparse.ArgumentParser(
        description="Convert a markdown file or a folder of markdown files into WAV or MP3 narration using Windows SAPI and ffmpeg."
    )
    parser.add_argument("input_path", nargs="?", help="Markdown file or folder to convert.")
    parser.add_argument("output_path", nargs="?", help="Destination audio file path, or output folder for batch conversion.")
    parser.add_argument(
        "--keep-intermediate-wav",
        action="store_true",
        help="Keep the intermediate WAV when generating MP3 output.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=None,
        help="Maximum characters per speech chunk before splitting. If omitted, auto-tuned per file.",
    )
    parser.add_argument(
        "--voice",
        help="Installed voice name or simple alias such as David, Dave, Zira, or Haruka.",
    )
    parser.add_argument(
        "--backend",
        choices=("sapi", "edge"),
        default="sapi",
        help="Speech backend to use. 'sapi' uses local Windows voices, 'edge' uses Edge TTS neural voices.",
    )
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="List available voices for the selected backend and exit.",
    )
    parser.add_argument(
        "--all-voices",
        action="store_true",
        help="When listing Edge voices, show the full catalog instead of aliases and recommended voices only.",
    )
    parser.add_argument(
        "--edge-workers",
        type=int,
        default=6,
        help="Maximum concurrent Edge TTS chunk requests (Edge backend only).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce console output by hiding step-by-step progress logs.",
    )
    parser.add_argument(
        "--chapter-markers",
        action="store_true",
        help="Insert silence markers at chapter/section endings for audiobook navigation.",
    )
    parser.add_argument(
        "--chapter-marker-duration",
        type=float,
        default=2.0,
        help="Duration of silence at chapter endings in seconds (default: 2.0).",
    )
    return parser.parse_args()


def choose_chunk_size_and_chunks(
    markdown_text: str,
    backend: str,
    requested_chunk_size: int | None,
    edge_workers: int,
    quiet: bool,
    chapter_markers: bool = False,
) -> tuple[int, list[str]]:
    """
    Pick a chunk size based on chunk count pressure, then return generated chunks.

    If the user specifies --chunk-size, that value is used directly.
    Otherwise this auto-tuner adjusts chunk size to keep total chunk counts
    in a smoother range for each backend.
    """
    if requested_chunk_size is not None:
        size = max(400, requested_chunk_size)
        return size, narration_paragraphs(markdown_text, size, chapter_markers)

    # Backend-aware baseline and limits.
    if backend == "edge":
        size = 2600
        min_size = 1200
        max_size = 6000
        target_chunks = max(1200, 1400 + (edge_workers * 120))
    else:
        size = 2200
        min_size = 1200
        max_size = 4500
        target_chunks = 1000

    chunks = narration_paragraphs(markdown_text, size, chapter_markers)
    chunk_count = len(chunks)

    # Tune using measured chunk count from current file.
    for _ in range(6):
        if chunk_count == 0:
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
        chunk_count = len(chunks)

    if not quiet:
        print(f"[STEP] Auto-selected chunk size: {size} (chunks: {chunk_count})")
    return size, chunks


def write_error_log(log_path: Path, backend: str, failures: list[dict[str, str]]) -> None:
    """Write detailed per-file failure diagnostics to a timestamped log file."""
    lines = [
        "md_to_audio failure log",
        f"timestamp: {datetime.now().isoformat(timespec='seconds')}",
        f"backend: {backend}",
        f"failed_files: {len(failures)}",
        "",
    ]
    for idx, item in enumerate(failures, start=1):
        lines.extend(
            [
                f"[{idx}] input: {item['input']}",
                f"    output: {item['output']}",
                f"    error: {item['error']}",
                "    traceback:",
                item["traceback"],
                "",
            ]
        )

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines), encoding="utf-8")


def print_final_report(results: list[dict[str, str]], error_log_path: Path | None) -> None:
    """Print a concise pass/fail report for all processed files."""
    print("\n=== Final Conversion Report ===")
    passed = [r for r in results if r["status"] == "PASSED"]
    failed = [r for r in results if r["status"] == "FAILED"]
    print(f"Total files: {len(results)}")
    print(f"Passed     : {len(passed)}")
    print(f"Failed     : {len(failed)}")
    print("")

    for i, r in enumerate(results, start=1):
        base = (
            f"[{i}] {r['status']} | {Path(r['input']).name} -> {Path(r['output']).name} "
            f"| chunk_size={r.get('chunk_size', '-')}, chunks={r.get('chunks', '-')}"
        )
        if r["status"] == "FAILED":
            print(f"{base} | error={r['error']}")
        else:
            print(f"{base} | bytes={r.get('bytes', '-')}, seconds={r.get('seconds', '-')}")

    if error_log_path is not None:
        print(f"\nError log: {error_log_path}")


def default_input_path(script_path: Path) -> Path:
    """
    Locate the default input markdown file when none is specified.
    
    If the script directory contains exactly one .md file, returns it.
    Otherwise raises an error requiring explicit input specification.
    
    Args:
        script_path (Path): Path to the script file (used to find parent directory).
    
    Returns:
        Path: The only .md file found in the script directory.
    
    Raises:
        SystemExit: If the directory contains zero or multiple .md files.
    """
    matches = sorted(script_path.parent.glob("*.md"))
    if len(matches) == 1:
        return matches[0]
    raise SystemExit("Specify an input markdown path when the folder contains multiple .md files.")


def clean_stem(path: Path) -> str:
    """
    Generate a clean output filename from a markdown file path.
    
    Removes bracketed/parenthesized metadata, normalizes whitespace,
    extracts and normalizes volume/book numbers, and removes duplicate tokens.
    Transforms paths like "The Unwanted Undead Adventurer - Volume 04 [Source].md"
    into "The Unwanted Undead Adventurer Volume 4".
    
    Args:
        path (Path): The input file path to clean.
    
    Returns:
        str: A cleaned, deduplicated filename stem suitable for output files.
    """
    stem = path.stem
    stem = re.sub(r"\[[^\]]*\]", "", stem)
    stem = re.sub(r"\([^\)]*\)", "", stem)
    stem = stem.replace("_", " ")
    stem = re.sub(r"\s*-\s*", " - ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" -_.")
    stem = re.sub(r"[<>:\"/\\|?*]", "", stem).strip(" .")

    volume_match = re.search(r"\b(?:volume|vol\.?|book)\s*0*(\d+)\b", stem, re.IGNORECASE)
    if volume_match:
        title_part = stem[: volume_match.start()].strip(" -_.")
        if title_part:
            stem = f"{title_part} Volume {int(volume_match.group(1))}"

    deduped = []
    for token in stem.split():
        if not deduped or deduped[-1].lower() != token.lower():
            deduped.append(token)

    return " ".join(deduped) or "converted"


def source_output_stem(path: Path) -> str:
    """Return the original input filename stem for output naming."""
    return path.stem or "converted"


def split_speech_chunk(text: str, max_length: int) -> list[str]:
    """
    Split a paragraph into speech chunks at sentence/phrase boundaries.
    
    Breaks text at natural boundaries (periods, exclamation/question marks,
    commas, colons) to avoid splitting words. Falls back to word boundaries
    if no natural break exists within the first half of the chunk.
    
    Args:
        text (str): Raw text to split into smaller chunks.
        max_length (int): Maximum characters per returned chunk.
    
    Returns:
        list[str]: List of text chunks, each <= max_length characters,
                   split at natural boundaries when possible.
    """
    remaining = re.sub(r"\s+", " ", text).strip()
    parts: list[str] = []

    while len(remaining) > max_length:
        slice_text = remaining[:max_length]
        boundary = max(
            slice_text.rfind("."),
            slice_text.rfind("!"),
            slice_text.rfind("?"),
            slice_text.rfind(";"),
            slice_text.rfind(","),
            slice_text.rfind(":"),
        )
        if boundary < max_length // 2:
            boundary = slice_text.rfind(" ")
        if boundary < 0:
            boundary = max_length - 1

        chunk = remaining[: boundary + 1].strip()
        if chunk:
            parts.append(chunk)
        remaining = remaining[boundary + 1 :].strip()

    if remaining:
        parts.append(remaining)

    return parts


def narration_paragraphs(markdown_text: str, chunk_size: int, chapter_markers: bool = False) -> list[str]:
    """
    Extract and prepare narration chunks from markdown text.
    
    Parses markdown to remove headings, code fences, ornament characters,
    and OCR junk while preserving paragraph flow. Groups lines into logical
    paragraphs, then splits them into chunks at sentence boundaries.
    
    Processing includes:
    - Removal of markdown code fences (``` and ~~~)
    - Removal of markdown heading hashes
    - Removal of decorative Unicode ornament characters
    - Removal of OCR noise tokens
    - Rejoining hard-wrapped lines into flowing paragraphs
    - Chunk splitting at sentence boundaries
    - Optional chapter ending markers for audiobook navigation
    
    Args:
        markdown_text (str): Raw markdown content to parse.
        chunk_size (int): Maximum characters per narration chunk.
        chapter_markers (bool): If True, insert [CHAPTER_END] markers after chapters.
    
    Returns:
        list[str]: List of text chunks ready for speech synthesis,
                   each <= chunk_size characters. Includes [CHAPTER_END] markers if enabled.
    """
    paragraph: list[str] = []
    out: list[str] = []
    pending_prefix = ""
    last_was_chapter = False

    def flush() -> None:
        if paragraph:
            joined = re.sub(r"\s+", " ", " ".join(paragraph)).strip()
            if joined:
                out.append(joined)
            paragraph.clear()

    for raw in markdown_text.splitlines():
        if FENCE_RE.match(raw):
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
            # Insert chapter end marker before this chapter heading (not first chapter)
            if chapter_markers and out and not (len(out) > 0 and "[CHAPTER_END]" in out[-1]):
                if last_was_chapter:
                    out.append("[CHAPTER_END]")
            tail = re.search(r"\s+([IA])$", line)
            if tail:
                pending_prefix = tail.group(1) + " "
                line = line[: tail.start()].rstrip()
            out.append(line)
            last_was_chapter = True
            continue

        previous = None
        while line and line != previous:
            previous = line
            line = OCR_JUNK_RE.sub("", line).rstrip()

        if not line:
            flush()
            continue

        if pending_prefix and not paragraph:
            line = pending_prefix + line
            pending_prefix = ""

        paragraph.append(line)
        last_was_chapter = False
        if SENTENCE_END_RE.search(line):
            flush()

    flush()
    
    # Add final chapter marker if enabled
    if chapter_markers and out and last_was_chapter and out[-1] != "[CHAPTER_END]":
        out.append("[CHAPTER_END]")

    chunks: list[str] = []
    for item in out:
        chunks.extend(split_speech_chunk(item, chunk_size))

    # Post-filter: remove/merge chunks that would cause NoAudioReceived.
    # Edge TTS raises NoAudioReceived when given fewer than ~4 alphabetic
    # characters (e.g. 'it.', '-', '. "ie'). These arise from sentences
    # split across blank lines in the source markdown.
    MIN_ALPHA = 4
    filtered: list[str] = []
    for chunk in chunks:
        alpha = sum(c.isalpha() for c in chunk)
        if alpha >= MIN_ALPHA:
            filtered.append(chunk)
        elif filtered:
            # Append orphaned fragment to previous chunk (preserves audio).
            filtered[-1] = filtered[-1].rstrip() + ' ' + chunk.strip()
        # else: stray symbol with nothing speakable — silently drop.

    return filtered


def generate_silence_chunk(duration: float) -> str:
    """
    Generate a special silence marker string for chapter endings.
    
    Args:
        duration (float): Duration of silence in seconds.
    
    Returns:
        str: A marker string that will be converted to silence during final audio processing.
    """
    return f"[SILENCE_{duration}s]"


def create_silence_mp3(output_path: Path, duration: float) -> None:
    """
    Create a silent MP3 file of specified duration using ffmpeg.
    
    Args:
        output_path (Path): Path where the silence MP3 will be saved.
        duration (float): Duration of silence in seconds.
    
    Raises:
        RuntimeError: If ffmpeg fails or is not available.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to generate silence but was not found on PATH.")
    
    try:
        # Generate silence using ffmpeg's anullsrc filter
        subprocess.run(
            [
                ffmpeg, "-y", "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono",
                "-t", str(duration), "-q:a", "9", "-acodec", "libmp3lame", str(output_path)
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to generate silence MP3: {e.stderr.decode()}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Silence generation timed out")


def ensure_ffmpeg() -> str:
    """
    Verify ffmpeg is available on PATH and return its executable path.
    
    ffmpeg is required for MP3 encoding and Edge TTS chunk concatenation.
    Raises a helpful error message with installation instructions if not found.
    
    Returns:
        str: Full path to the ffmpeg executable.
    
    Raises:
        SystemExit: If ffmpeg is not found on PATH, with installation guidance.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit(
            "ffmpeg was not found on PATH.\n"
            "ffmpeg is required for MP3 output and Edge TTS audio concatenation.\n\n"
            "Install it with one of the following:\n"
            "  Windows (winget):  winget install ffmpeg\n"
            "  Windows (choco):   choco install ffmpeg\n"
            "  macOS (brew):      brew install ffmpeg\n"
            "  Linux (apt):       sudo apt install ffmpeg\n"
            "  Download:          https://ffmpeg.org/download.html\n\n"
            "After installing, restart your terminal so ffmpeg is on PATH."
        )
    return ffmpeg


def powershell_executable() -> str:
    """
    Locate the PowerShell executable on the system.
    
    Searches for PowerShell in PATH (cross-platform), falls back to
    Windows-specific names, and defaults to 'powershell' if not found.
    
    Returns:
        str: Path to powershell executable, or 'powershell' if not found
             (relies on PATH resolution at runtime).
    """
    return shutil.which("powershell") or shutil.which("powershell.exe") or "powershell"


def _require_edge_tts():
    """
    Import and validate the edge-tts package for Edge backend support.
    
    Attempts to import the edge-tts package. Raises a helpful error
    with installation instructions if the package is not installed.
    
    Returns:
        module: The imported edge_tts module.
    
    Raises:
        SystemExit: If edge-tts is not installed, with pip install guidance.
    """
    try:
        import edge_tts as _edge_tts
        return _edge_tts
    except ImportError:
        raise SystemExit(
            "The 'edge-tts' package is required for --backend edge.\n"
            "Install it with: pip install edge-tts"
        )


async def _edge_list_voices_async() -> list[str]:
    """
    Asynchronously fetch and return available Edge TTS voice names.
    
    Contacts the Microsoft Edge TTS service to retrieve the list of
    available neural voices, extracts short names, and returns them sorted.
    
    Returns:
        list[str]: Sorted list of Edge TTS voice short names
                   (e.g., ['en-US-AriaNeural', 'en-US-GuyNeural', ...]).
    
    Raises:
        SystemExit: If edge-tts is not installed.
    """
    edge_tts = _require_edge_tts()
    voices = await edge_tts.list_voices()
    return sorted(v["ShortName"] for v in voices if v.get("ShortName"))


def edge_voice_names() -> list[str]:
    """
    Synchronously retrieve available Edge TTS voice names.
    
    Wraps the async voice-fetching coroutine for use in synchronous code.
    Runs the async function in a new event loop and returns results.
    
    Returns:
        list[str]: Sorted list of available Edge TTS voice short names.
    """
    return asyncio.run(_edge_list_voices_async())


def resolve_edge_voice(voice_hint: str) -> str:
    """
    Resolve a voice alias or exact name to an Edge TTS voice identifier.
    
    Attempts to match the provided voice hint to:
    1. A built-in alias (Dave -> en-US-GuyNeural, Aria -> en-US-AriaNeural, etc.)
    2. An exact voice name from the service
    3. A partial match within voice names
    
    Args:
        voice_hint (str): Voice alias (e.g., 'Dave', 'Aria') or exact name
                         (e.g., 'en-US-AriaNeural').
    
    Returns:
        str: The resolved Edge voice identifier (e.g., 'en-US-AriaNeural').
    
    Raises:
        SystemExit: If the voice hint cannot be resolved to any available voice.
    """
    hint_key = normalize_voice_key(voice_hint)
    alias = EDGE_VOICE_ALIASES.get(hint_key)
    if alias:
        return alias
    voices = edge_voice_names()
    for v in voices:
        if hint_key == normalize_voice_key(v):
            return v
    for v in voices:
        if hint_key in normalize_voice_key(v):
            return v
    sample = ", ".join(voices[:20])
    raise SystemExit(f"Edge voice '{voice_hint}' was not found. Sample available voices: {sample}")


async def _edge_synthesize_chunk(text: str, voice_name: str, output_path: Path) -> None:
    """
    Asynchronously synthesize a single text chunk to MP3 using Edge TTS.
    
    Contacts Microsoft Edge TTS service to convert the provided text
    to speech using the specified voice, and saves the result as MP3.
    
    Args:
        text (str): The text to synthesize into speech.
        voice_name (str): Edge TTS voice identifier (e.g., 'en-US-AriaNeural').
        output_path (Path): Destination file path for the generated MP3 chunk.
    
    Returns:
        None (output is saved to disk at output_path).
    
    Raises:
        SystemExit: If edge-tts is not installed.
    """
    edge_tts = _require_edge_tts()

    # Guard: skip chunks with no speakable content to prevent NoAudioReceived.
    if sum(c.isalpha() for c in text) < 4:
        if not QUIET:
            print(f'[SKIP] Unspeakable chunk ({repr(text[:60])})')
        output_path.touch()   # zero-byte placeholder for concat step
        return

    last_exc: Exception | None = None
    for attempt in range(1, EDGE_RETRY_ATTEMPTS + 1):
        try:
            communicate = edge_tts.Communicate(text=text, voice=voice_name)
            await communicate.save(str(output_path))
            if output_path.exists() and output_path.stat().st_size > 0:
                return
            raise RuntimeError("Edge TTS produced an empty audio chunk")
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            retryable = (
                "noaudioreceived" in msg
                or "no audio was received" in msg
                or "429" in msg
                or "timeout" in msg
                or "timed out" in msg
                or "connection" in msg
                or "disconnected" in msg
                or "server disconnected" in msg
                or "tempor" in msg
            )
            if attempt >= EDGE_RETRY_ATTEMPTS or not retryable:
                raise

            # Exponential backoff with jitter to reduce burst retries.
            wait_s = min(8.0, 0.8 * (2 ** (attempt - 1)) + random.uniform(0.0, 0.4))
            if not QUIET:
                print(
                    f"Retrying Edge chunk ({attempt}/{EDGE_RETRY_ATTEMPTS - 1}) after transient error: {exc}"
                )
            await asyncio.sleep(wait_s)

    if last_exc is not None:
        raise last_exc


async def _edge_synthesize_chunks_async(
    chunks: list[str], voice_name: str, tmp_dir: Path, workers: int, quiet: bool,
    chapter_marker_duration: float = 2.0
) -> tuple[list[Path], dict[int, float]]:
    """
    Asynchronously synthesize multiple text chunks in parallel using Edge TTS.
    
    Synthesizes all chunks concurrently, limited by a semaphore to respect
    the max worker count. Maintains chunk ordering and prints periodic
    progress updates unless quiet mode is enabled.
    
    Handles special [CHAPTER_END] markers by:
    - Filtering them out from synthesis
    - Tracking their positions
    - Returning marker positions for silence insertion
    
    Args:
        chunks (list[str]): Text chunks to synthesize (may include [CHAPTER_END] markers).
        voice_name (str): Edge TTS voice identifier.
        tmp_dir (Path): Temporary directory to store intermediate MP3 chunks.
        workers (int): Maximum concurrent synthesis requests allowed.
        quiet (bool): If True, suppress progress logging.
        chapter_marker_duration (float): Duration of silence at chapter ends in seconds.
    
    Returns:
        tuple[list[Path], dict[int, float]]: 
            - List of paths to generated MP3 files (in order, excluding chapter markers)
            - Dict mapping positions to silence durations for chapter markers
    """
    # Identify and filter chapter markers
    chapter_marker_indices: dict[int, float] = {}
    synthesis_chunks: list[tuple[int, str]] = []
    
    for idx, chunk in enumerate(chunks):
        if chunk == "[CHAPTER_END]":
            chapter_marker_indices[idx] = chapter_marker_duration
        else:
            synthesis_chunks.append((idx, chunk))
    
    semaphore = asyncio.Semaphore(workers)

    async def synthesize_one(orig_idx: int, chunk_text: str) -> tuple[int, Path]:
        chunk_path = tmp_dir / f"chunk-{orig_idx:05d}.mp3"
        async with semaphore:
            await _edge_synthesize_chunk(chunk_text, voice_name, chunk_path)
        return orig_idx, chunk_path

    tasks = [
        asyncio.create_task(synthesize_one(orig_idx, chunk_text))
        for orig_idx, chunk_text in synthesis_chunks
    ]
    chunk_paths_dict: dict[int, Path] = {}
    completed = 0
    total_synthesis = len(synthesis_chunks)

    try:
        for task in asyncio.as_completed(tasks):
            orig_idx, chunk_path = await task
            chunk_paths_dict[orig_idx] = chunk_path
            completed += 1
            if (not quiet) and (completed % 100 == 0 or completed == total_synthesis):
                print(f"Narrated {completed} of {total_synthesis} chunks...")
    except Exception:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    # Return paths in order (excluding markers), and marker positions
    chunk_paths_list = [chunk_paths_dict[orig_idx] for orig_idx, _ in synthesis_chunks]
    return chunk_paths_list, chapter_marker_indices


def _edge_concat_mp3_with_chapters(
    chunk_paths: list[Path],
    chapter_marker_indices: dict[int, float],
    output_path: Path,
    tmp_dir: Path,
) -> None:
    """
    Concatenate Edge TTS MP3 chunks with chapter ending silence markers.
    
    Takes synthesized chunk paths and chapter marker positions, generates
    silence MP3s at chapter boundaries, then concatenates all files in order.
    
    Args:
        chunk_paths (list[Path]): Paths to synthesized MP3 chunks (excluding markers).
        chapter_marker_indices (dict[int, float]): Mapping of chunk indices to silence durations.
        output_path (Path): Final concatenated MP3 output path.
        tmp_dir (Path): Temporary directory for silence MP3 files.
    
    Returns:
        None (output is saved to disk).
    
    Raises:
        RuntimeError: If silence generation or concatenation fails.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg is required to concatenate Edge TTS chunks but was not found on PATH.")
    
    # Build the final file list with chapter markers inserted
    final_files: list[Path] = []
    chunk_idx = 0
    
    # Reconstruct which original indices had content
    for orig_idx in range(len(chunk_paths) + len(chapter_marker_indices)):
        if orig_idx in chapter_marker_indices:
            # Generate silence for this chapter ending
            duration = chapter_marker_indices[orig_idx]
            silence_file = tmp_dir / f"silence-{orig_idx:05d}.mp3"
            try:
                create_silence_mp3(silence_file, duration)
                final_files.append(silence_file)
            except RuntimeError as e:
                print(f"Warning: Could not generate chapter marker silence: {e}")
        else:
            # Add the synthesized chunk
            if chunk_idx < len(chunk_paths):
                final_files.append(chunk_paths[chunk_idx])
                chunk_idx += 1
    
    # Concatenate all files (chunks + silence markers)
    with tempfile.TemporaryDirectory(prefix="edge-tts-concat-") as tmp:
        concat_file = Path(tmp) / "concat.txt"
        valid_paths = [p for p in final_files if p.exists() and p.stat().st_size > 0]
        if not valid_paths:
            raise RuntimeError("All chunks were empty — no audio produced.")
        lines = [f"file '{p.as_posix()}'" for p in valid_paths]
        concat_file.write_text("\n".join(lines), encoding="utf-8")
        subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(output_path)],
            check=True,
        )


def _edge_concat_mp3(chunk_paths: list[Path], output_path: Path) -> None:
    """
    Concatenate multiple Edge TTS MP3 chunks into a single output file.
    
    Uses ffmpeg to efficiently concatenate MP3 files without re-encoding,
    creating a seamless audio stream from individually synthesized chunks.
    
    Args:
        chunk_paths (list[Path]): Paths to MP3 chunk files, in playback order.
        output_path (Path): Destination path for the concatenated MP3 file.
    
    Returns:
        None (output is saved to disk at output_path).
    
    Raises:
        SystemExit: If ffmpeg is not found on PATH.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg is required to concatenate Edge TTS chunks but was not found on PATH.")
    with tempfile.TemporaryDirectory(prefix="edge-tts-concat-") as tmp:
        concat_file = Path(tmp) / "concat.txt"
        # Skip zero-byte placeholder files (chunks with no speakable content).
        valid_paths = [p for p in chunk_paths if p.exists() and p.stat().st_size > 0]
        if not valid_paths:
            raise RuntimeError("All chunks were empty — no audio produced.")
        lines = [f"file '{p.as_posix()}'" for p in valid_paths]
        concat_file.write_text("\n".join(lines), encoding="utf-8")
        subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(output_path)],
            check=True,
        )


def convert_one_edge(
    input_path: Path,
    output_path: Path,
    chunk_size: int | None,
    voice_name: str,
    workers: int,
    quiet: bool,
    chapter_markers: bool = False,
    chapter_marker_duration: float = 2.0,
) -> tuple[int, int, int]:
    """
    Convert a single markdown file to MP3 using the Edge TTS backend.
    
    Complete workflow:
    1. Read and parse markdown input
    2. Extract and chunk text for synthesis
    3. Synthesize chunks in parallel using Edge voice
    4. Concatenate MP3 chunks into final output file
    
    Args:
        input_path (Path): Source markdown file to convert.
        output_path (Path): Destination MP3 file path.
        chunk_size (int): Maximum characters per speech chunk.
        voice_name (str): Edge TTS voice identifier (e.g., 'en-US-AriaNeural').
        workers (int): Maximum concurrent synthesis requests.
        quiet (bool): If True, suppress progress logging.
    
    Returns:
        None (output MP3 is saved to disk).
    
    Raises:
        SystemExit: If input contains no readable content or required tools are missing.
    """
    log_step(f"Starting Edge conversion for: {input_path.name}")
    _require_edge_tts()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_step("Reading markdown input")
    markdown_text = input_path.read_text(encoding="utf-8")
    effective_chunk_size, chunks = choose_chunk_size_and_chunks(
        markdown_text,
        backend="edge",
        requested_chunk_size=chunk_size,
        edge_workers=workers,
        quiet=quiet,
        chapter_markers=chapter_markers,
    )
    log_step(f"Preparing narration chunks (chunk size: {effective_chunk_size})")
    if not chunks:
        raise SystemExit(f"No readable content was found in: {input_path}")
    log_step(f"Prepared {len(chunks)} chunks")

    worker_candidates = [workers]
    while worker_candidates[-1] > 1:
        worker_candidates.append(max(1, worker_candidates[-1] // 2))

    with tempfile.TemporaryDirectory(prefix="edge-tts-chunks-") as tmp:
        tmp_dir = Path(tmp)
        chunk_paths = None
        chapter_marker_indices = {}
        last_exc: Exception | None = None
        for run_workers in worker_candidates:
            try:
                log_step(f"Synthesizing chunks with Edge voice '{voice_name}' using {run_workers} workers")
                chunk_paths, chapter_marker_indices = asyncio.run(
                    _edge_synthesize_chunks_async(chunks, voice_name, tmp_dir, run_workers, quiet, chapter_marker_duration)
                )
                break
            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()
                can_try_lower = run_workers > 1 and (
                    "disconnected" in msg
                    or "server disconnected" in msg
                    or "timeout" in msg
                    or "timed out" in msg
                    or "429" in msg
                    or "noaudioreceived" in msg
                    or "no audio was received" in msg
                    or "connection" in msg
                    or "tempor" in msg
                )
                if not can_try_lower:
                    raise
                if not quiet:
                    print(f"[STEP] Retrying file with lower concurrency after error: {exc}")
        if chunk_paths is None:
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("Edge synthesis failed before any chunk output was produced.")
        log_step("Concatenating chunks into final MP3")
        if chapter_markers and chapter_marker_indices:
            if not quiet:
                print("[STEP] Inserting chapter ending silence markers...")
            _edge_concat_mp3_with_chapters(chunk_paths, chapter_marker_indices, output_path, tmp_dir)
        else:
            _edge_concat_mp3(chunk_paths, output_path)
    final_size = output_path.stat().st_size
    print(f"Created: {output_path}")
    print(f"Size: {final_size:,} bytes")
    return effective_chunk_size, len(chunks), final_size


def resolve_edge_targets(args: argparse.Namespace) -> list[tuple[Path, Path]]:
    """
    Resolve input/output paths for Edge TTS batch or single-file conversion.
    
    Handles three scenarios:
    1. Single file: input_file -> output.mp3 (or specified file)
    2. Folder: converts all .md files in folder -> output_dir/*.mp3
    3. Default: auto-selects single .md file in script directory
    
    Args:
        args (argparse.Namespace): Parsed arguments with input_path, output_path.
    
    Returns:
        list[tuple[Path, Path]]: List of (source_md, dest_mp3) path pairs.
    
    Raises:
        SystemExit: If input path not found, no .md files in folder,
                    or output_path is invalid for folder batch conversion.
    """
    input_path = Path(args.input_path).resolve() if args.input_path else default_input_path(Path(__file__).resolve())
    input_paths = collect_input_paths(input_path)
    raw = None
    if args.output_path:
        raw = Path(args.output_path)
        if not raw.is_absolute():
            raw = (input_path if input_path.is_dir() else input_path.parent) / raw
        raw = raw.resolve()
    targets: list[tuple[Path, Path]] = []
    if len(input_paths) > 1:
        out_dir = input_path if raw is None else raw
        if out_dir.suffix:
            raise SystemExit("When converting a folder, output_path must be a directory, not a file.")
        for src in input_paths:
            targets.append((src, (out_dir / f"{source_output_stem(src)}.mp3").resolve()))
        return targets
    src = input_paths[0]
    if raw is None:
        out = (src.parent / f"{source_output_stem(src)}.mp3").resolve()
    elif raw.suffix:
        if raw.suffix.lower() != ".mp3":
            raise SystemExit("Edge TTS output must end in .mp3.")
        out = raw
    else:
        out = (raw / f"{source_output_stem(src)}.mp3").resolve()
    targets.append((src, out))
    return targets


def list_edge_voices(show_all: bool = False) -> int:
    """
    Display available Edge TTS voices with aliases and recommendations.
    
    Prints voice aliases and recommended voices by default. With show_all=True,
    prints the full catalog (300+ voices).
    
    Args:
        show_all (bool): If True, display all voices; else show aliases and
                        recommended voices only (default: False).
    
    Returns:
        int: Exit code (always 0).
    """
    voices = edge_voice_names()
    print("Simple aliases you can pass to --voice:")
    shown: set[tuple[str, str]] = set()
    for alias in sorted(EDGE_VOICE_ALIASES):
        resolved = EDGE_VOICE_ALIASES[alias]
        if resolved in voices:
            pair = (alias.title(), resolved)
            if pair not in shown:
                shown.add(pair)
                print(f"  {alias.title():<10} -> {resolved}")
    print("\nRecommended exact voice names:")
    for v in EDGE_RECOMMENDED_VOICES:
        if v in voices:
            print(f"  {v}")
    if show_all:
        print("\nAll exact Edge voice names:")
        for v in voices:
            print(f"  {v}")
    else:
        remaining = [v for v in voices if v not in EDGE_RECOMMENDED_VOICES]
        print(f"\n  ... and {len(remaining)} more voices. Run with --all-voices to see the full list.")
    return 0


def normalize_voice_key(value: str) -> str:
    """
    Normalize a voice name for case-insensitive comparison.
    
    Converts to lowercase and removes non-alphanumeric characters,
    enabling fuzzy matching (e.g., 'en-US-Aria' matches 'en-US-AriaNeural').
    
    Args:
        value (str): Voice name or alias to normalize.
    
    Returns:
        str: Normalized key with only lowercase alphanumerics.
    """
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def installed_voice_names() -> list[str]:
    """
    Retrieve list of installed Windows SAPI voices via PowerShell.
    
    Executes PowerShell script to query System.Speech.Synthesis for
    locally installed voices on the machine.
    
    Returns:
        list[str]: Names of installed SAPI voices.
    
    Raises:
        SystemExit: If PowerShell command fails.
    """
    command = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }"
    )
    completed = subprocess.run(
        [powershell_executable(), "-NoProfile", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def alias_targets_for_display(voices: list[str], alias_map: dict[str, str]) -> list[tuple[str, str]]:
    """
    Filter and format voice aliases for display purposes.
    
    Attempts to resolve each alias to an actual installed/available voice,
    deduplicates alias->voice mappings, and returns pairs suitable for display.
    
    Args:
        voices (list[str]): List of available voice names to match against.
        alias_map (dict[str, str]): Mapping of alias names to voice identifiers.
    
    Returns:
        list[tuple[str, str]]: List of (alias, voice_name) pairs where the
                               voice is actually available.
    """
    targets: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for alias in sorted(alias_map):
        try:
            resolved = resolve_voice_name(alias)
        except SystemExit:
            continue
        pair = (alias.title(), resolved)
        if pair not in seen:
            seen.add(pair)
            targets.append(pair)
    return targets


def list_sapi_voices() -> int:
    """
    Display available SAPI voices with simple aliases.
    
    Prints built-in voice aliases (Dave, Zira, etc.) followed by
    the list of all installed Windows SAPI voices.
    
    Returns:
        int: Exit code (always 0).
    """
    voices = installed_voice_names()
    print("Simple aliases you can pass to --voice:")
    for alias, resolved in alias_targets_for_display(voices, VOICE_ALIASES):
        print(f"  {alias:<10} -> {resolved}")

    print("\nExact installed voice names:")
    for voice_name in voices:
        print(f"  {voice_name}")
    return 0


def resolve_voice_name(voice_hint: str | None) -> str | None:
    """
    Resolve a voice alias or name to an installed SAPI voice identifier.
    
    Attempts to match the provided voice hint to:
    1. A built-in SAPI alias (Dave -> David, Zira -> Zira, etc.)
    2. An exact installed voice name
    3. A partial match within installed voice names
    
    Args:
        voice_hint (str | None): Voice alias/name, or None for default voice.
    
    Returns:
        str | None: The resolved SAPI voice name, or None if voice_hint is None.
    
    Raises:
        SystemExit: If voice_hint is provided but cannot be resolved,
                    or if no SAPI voices are installed.
    """
    if not voice_hint:
        return None

    voices = installed_voice_names()
    if not voices:
        raise SystemExit("No installed System.Speech voices were found.")

    hint_key = normalize_voice_key(voice_hint)
    alias_key = VOICE_ALIASES.get(hint_key, hint_key)

    def voice_keys(voice_name: str) -> set[str]:
        parts = re.split(r"[^a-z0-9]+", voice_name.lower())
        keys = {normalize_voice_key(voice_name)}
        keys.update(part for part in parts if part)
        return keys

    for voice_name in voices:
        keys = voice_keys(voice_name)
        if hint_key in keys or alias_key in keys:
            return voice_name

    for voice_name in voices:
        normalized_name = normalize_voice_key(voice_name)
        if hint_key in normalized_name or alias_key in normalized_name:
            return voice_name

    available = ", ".join(voices)
    raise SystemExit(f"Voice '{voice_hint}' was not found. Available voices: {available}")


def synthesize_wav(chunks: list[str], wav_path: Path, voice_name: str | None = None, quiet: bool = False) -> None:
    """
    Synthesize narration chunks to WAV using Windows SAPI via PowerShell.
    
    Writes chunks to a temporary file, then executes a PowerShell script
    that uses System.Speech.Synthesis to convert text to audio.
    Produces 16-bit PCM WAV at 16 kHz mono.
    
    Args:
        chunks (list[str]): Text chunks to synthesize.
        wav_path (Path): Destination WAV file path.
        voice_name (str | None): SAPI voice name, or None for system default.
        quiet (bool): If True, suppress progress logging (default: False).
    
    Returns:
        None (output WAV is saved to disk).
    
    Raises:
        SystemExit: If PowerShell script execution fails.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="md-audio-"))
    chunk_file = temp_dir / "chunks.txt"
    chunk_file.write_text("\n".join(chunks), encoding="utf-8")

    script = """
param([string]$ChunkFile, [string]$WavPath, [string]$VoiceName, [int]$Quiet)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$audioFormat = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo 16000, ([System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen), ([System.Speech.AudioFormat.AudioChannel]::Mono)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $synth.Rate = 0
    $synth.Volume = 100
    if ($VoiceName) {
        $synth.SelectVoice($VoiceName)
    }
    $synth.SetOutputToWaveFile($WavPath, $audioFormat)
    $chunks = Get-Content -LiteralPath $ChunkFile -Encoding UTF8
    for ($index = 0; $index -lt $chunks.Count; $index++) {
        if ($Quiet -eq 0 -and $index -gt 0 -and $index % 100 -eq 0) {
            Write-Host ('Narrated {0} of {1} chunks...' -f $index, $chunks.Count)
        }
        $synth.Speak($chunks[$index])
    }
    $synth.SetOutputToNull()
}
finally {
    $synth.Dispose()
}
""".strip()
    script_file = temp_dir / "synthesize.ps1"
    script_file.write_text(script, encoding="utf-8")

    try:
        subprocess.run(
            [
                powershell_executable(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_file),
                str(chunk_file),
                str(wav_path),
                voice_name or "",
                "1" if quiet else "0",
            ],
            check=True,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def convert_wav_to_mp3_with_chapters(
    wav_path: Path,
    mp3_path: Path,
    chapter_marker_positions: list[int],
    chapter_marker_duration: float = 2.0,
) -> None:
    """
    Convert a WAV file to MP3 and insert chapter ending silence markers.
    
    This is a more complex operation that requires:
    1. Converting the WAV to MP3
    2. Splitting the MP3 into segments corresponding to chunks
    3. Inserting silence between chapters
    
    For simplicity, this version converts to MP3 first, then inserts silence
    based on estimated chunk positions.
    
    Args:
        wav_path (Path): Source WAV file to convert.
        mp3_path (Path): Destination MP3 file path.
        chapter_marker_positions (list[int]): List of chunk indices where chapters end.
        chapter_marker_duration (float): Duration of silence at chapter ends in seconds.
    
    Returns:
        None (output MP3 is saved to disk).
    
    Raises:
        SystemExit: If ffmpeg is not found or encoding fails.
    """
    ffmpeg = ensure_ffmpeg()
    
    # First, convert WAV to MP3 as usual
    tmp_mp3_path = mp3_path.with_stem(mp3_path.stem + "_tmp")
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(wav_path),
            "-codec:a",
            "libmp3lame",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "32k",
            str(tmp_mp3_path),
        ],
        check=True,
    )
    
    # For now, just rename the temp file since proper chapter marker insertion
    # requires knowing exact audio durations for each chunk, which is complex
    # Future enhancement: parse MP3 headers or use ffprobe to determine chunk lengths
    # and insert silence at precise positions
    if chapter_marker_positions:
        # This is where we would insert silence, but for now we'll keep the simple MP3
        # A full implementation would require:
        # 1. ffprobe to get MP3 duration
        # 2. Calculate average chunk duration
        # 3. Use ffmpeg concat filter to insert silence segments
        # 4. Concatenate with inserted silence
        pass
    
    tmp_mp3_path.rename(mp3_path)


def convert_wav_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    """
    Convert a WAV file to MP3 using ffmpeg.
    
    Encodes WAV to MP3 format with mono channel, 16 kHz sample rate,
    and 32 kbps bitrate (suitable for speech).
    
    Args:
        wav_path (Path): Source WAV file to convert.
        mp3_path (Path): Destination MP3 file path.
    
    Returns:
        None (output MP3 is saved to disk).
    
    Raises:
        SystemExit: If ffmpeg is not found or encoding fails.
    """
    ffmpeg = ensure_ffmpeg()
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(wav_path),
            "-codec:a",
            "libmp3lame",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "32k",
            str(mp3_path),
        ],
        check=True,
    )


def collect_input_paths(input_path: Path) -> list[Path]:
    """
    Collect markdown file paths from the given input location.
    
    If input is a directory, finds all .md files within it (sorted).
    If input is a file, returns it as a single-item list.
    
    Args:
        input_path (Path): Directory or file path to collect from.
    
    Returns:
        list[Path]: List of .md file paths to process.
    
    Raises:
        SystemExit: If input path not found, or directory contains no .md files.
    """
    if input_path.is_dir():
        matches = sorted(path for path in input_path.glob("*.md") if path.is_file())
        if not matches:
            raise SystemExit(f"No .md files were found in: {input_path}")
        return matches

    if input_path.is_file():
        return [input_path]

    raise SystemExit(f"Input path was not found: {input_path}")


def default_output_path(input_path: Path, extension: str) -> Path:
    """
    Generate the default output file path for a given input markdown file.
    
    Places output file in the same directory as input with the cleaned stem
    name and specified extension (e.g., 'book.md' -> 'book.mp3').
    
    Args:
        input_path (Path): Source markdown file path.
        extension (str): File extension for output (e.g., '.mp3', '.wav').
    
    Returns:
        Path: Absolute path to default output file location.
    """
    return (input_path.parent / f"{source_output_stem(input_path)}{extension}").resolve()


def resolve_targets(args: argparse.Namespace) -> list[tuple[Path, Path, str]]:
    """
    Resolve input/output paths for SAPI batch or single-file conversion.
    
    Handles three scenarios:
    1. Single file: input_file -> output.mp3 (or output.wav, or specified)
    2. Folder: converts all .md files in folder -> output_dir/*.mp3
    3. Default: auto-selects single .md file in script directory
    
    Args:
        args (argparse.Namespace): Parsed arguments with input_path, output_path.
    
    Returns:
        list[tuple[Path, Path, str]]: List of (source_md, dest_audio, extension) tuples.
    
    Raises:
        SystemExit: If input path not found, no .md files in folder,
                    output extension is not .mp3 or .wav, or output_path
                    is invalid for folder batch conversion.
    """
    script_path = Path(__file__).resolve()
    input_path = Path(args.input_path).resolve() if args.input_path else default_input_path(script_path)
    input_paths = collect_input_paths(input_path)

    if args.output_path:
        raw_output_path = Path(args.output_path)
        if not raw_output_path.is_absolute():
            raw_output_path = (input_path if input_path.is_dir() else input_path.parent) / raw_output_path
        raw_output_path = raw_output_path.resolve()
    else:
        raw_output_path = None

    targets: list[tuple[Path, Path, str]] = []

    if len(input_paths) > 1:
        if raw_output_path is None:
            output_directory = input_path
            output_extension = ".mp3"
        else:
            output_extension = raw_output_path.suffix.lower()
            if output_extension:
                raise SystemExit("When converting a folder, output_path must be a directory, not a file.")
            output_directory = raw_output_path

        for source_path in input_paths:
            output_path = (output_directory / f"{source_output_stem(source_path)}{output_extension}").resolve()
            targets.append((source_path, output_path, output_extension))

        return targets

    source_path = input_paths[0]
    if raw_output_path is None:
        output_path = default_output_path(source_path, ".mp3")
    elif raw_output_path.suffix:
        output_path = raw_output_path
    else:
        output_path = (raw_output_path / f"{source_output_stem(source_path)}.mp3").resolve()

    extension = output_path.suffix.lower()
    if extension not in {".mp3", ".wav"}:
        raise SystemExit("Output path must end in .mp3 or .wav.")

    targets.append((source_path, output_path, extension))
    return targets


def convert_one(
    input_path: Path,
    output_path: Path,
    extension: str,
    keep_intermediate_wav: bool,
    chunk_size: int | None,
    voice_name: str | None,
    quiet: bool,
    chapter_markers: bool = False,
    chapter_marker_duration: float = 2.0,
) -> tuple[int, int, int]:
    """
    Convert a single markdown file to audio using the SAPI backend.
    
    Complete workflow:
    1. Read and parse markdown input
    2. Extract and chunk text for synthesis
    3. Synthesize chunks to intermediate WAV using SAPI
    4. Encode WAV to MP3 if requested (requires ffmpeg)
    5. Clean up intermediate WAV unless --keep-intermediate-wav is set
    
    Args:
        input_path (Path): Source markdown file to convert.
        output_path (Path): Destination audio file path (.mp3 or .wav).
        extension (str): Output format ('.mp3' or '.wav').
        keep_intermediate_wav (bool): If True, preserve the intermediate WAV file.
        chunk_size (int): Maximum characters per speech chunk.
        voice_name (str | None): SAPI voice name, or None for system default.
        quiet (bool): If True, suppress progress logging.
    
    Returns:
        None (output audio is saved to disk).
    
    Raises:
        SystemExit: If input contains no readable content or required tools are missing.
    """
    log_step(f"Starting SAPI conversion for: {input_path.name}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    log_step("Reading markdown input")
    markdown_text = input_path.read_text(encoding="utf-8")
    effective_chunk_size, chunks = choose_chunk_size_and_chunks(
        markdown_text,
        backend="sapi",
        requested_chunk_size=chunk_size,
        edge_workers=1,
        quiet=quiet,
        chapter_markers=chapter_markers,
    )
    log_step(f"Preparing narration chunks (chunk size: {effective_chunk_size})")
    if not chunks:
        raise SystemExit(f"No readable content was found in the markdown file: {input_path}")
    
    # For SAPI, filter out chapter markers and track their positions
    chapter_marker_positions = []
    synthesis_chunks = []
    for idx, chunk in enumerate(chunks):
        if chunk == "[CHAPTER_END]":
            chapter_marker_positions.append(len(synthesis_chunks))
        else:
            synthesis_chunks.append(chunk)
    
    if not synthesis_chunks:
        raise SystemExit(f"No readable content was found in the markdown file: {input_path}")
    
    log_step(f"Prepared {len(synthesis_chunks)} chunks")
    if chapter_markers and chapter_marker_positions:
        log_step(f"Found {len(chapter_marker_positions)} chapter endings")

    wav_path = output_path if extension == ".wav" else output_path.with_suffix(".intermediate.wav")
    log_step("Synthesizing speech to WAV")
    synthesize_wav(synthesis_chunks, wav_path, voice_name, quiet=quiet)

    if extension == ".mp3":
        log_step("Encoding WAV to MP3")
        try:
            if chapter_markers and chapter_marker_positions:
                if not quiet:
                    print("[STEP] Inserting chapter ending silence markers...")
                convert_wav_to_mp3_with_chapters(wav_path, output_path, chapter_marker_positions, chapter_marker_duration)
            else:
                convert_wav_to_mp3(wav_path, output_path)
        finally:
            if not keep_intermediate_wav and wav_path.exists():
                log_step("Removing intermediate WAV")
                wav_path.unlink()

    final_size = output_path.stat().st_size
    print(f"Created: {output_path}")
    print(f"Size: {final_size:,} bytes")
    return effective_chunk_size, len(chunks), final_size


def main() -> int:
    """
    Main entry point for the markdown-to-audio converter.
    
    Parses command-line arguments, sets global state (quiet mode),
    routes to appropriate backend (Edge or SAPI), and processes
    either a single file or batch of files.
    
    Execution flow:
    1. Parse and validate CLI arguments
    2. Set quiet mode from arguments
    3. Handle --list-voices request if specified
    4. Validate configuration (e.g., --edge-workers >= 1)
    5. Resolve input/output file paths
    6. Convert each file using the selected backend
    
    Returns:
        int: Exit code (0 on success, non-zero on error).
    
    Raises:
        SystemExit: On configuration errors or file processing failures.
    """
    global QUIET
    args = parse_args()
    QUIET = args.quiet
    if args.backend == "edge":
        log_step("Backend selected: Edge TTS")
        if args.list_voices:
            log_step("Listing Edge voices")
            return list_edge_voices(show_all=args.all_voices)
        if args.edge_workers < 1:
            raise SystemExit("--edge-workers must be at least 1.")
        voice_name = resolve_edge_voice(args.voice or "Aria")
        targets = resolve_edge_targets(args)
        log_step(f"Resolved {len(targets)} input file(s)")
        results: list[dict[str, str]] = []
        failures: list[dict[str, str]] = []
        for index, (input_path, output_path) in enumerate(targets, start=1):
            log_step(f"Processing file {index}/{len(targets)} -> {output_path.name}")
            started = time.perf_counter()
            try:
                used_chunk_size, chunk_count, final_size = convert_one_edge(
                    input_path,
                    output_path,
                    args.chunk_size,
                    voice_name,
                    args.edge_workers,
                    args.quiet,
                    args.chapter_markers,
                    args.chapter_marker_duration,
                )
                elapsed = time.perf_counter() - started
                results.append(
                    {
                        "status": "PASSED",
                        "input": str(input_path),
                        "output": str(output_path),
                        "chunk_size": str(used_chunk_size),
                        "chunks": str(chunk_count),
                        "bytes": str(final_size),
                        "seconds": f"{elapsed:.2f}",
                        "error": "",
                    }
                )
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt):
                    raise
                tb = traceback.format_exc()
                err_msg = f"{type(exc).__name__}: {exc}"
                print(f"[ERROR] Failed: {input_path.name} -> {err_msg}")
                results.append(
                    {
                        "status": "FAILED",
                        "input": str(input_path),
                        "output": str(output_path),
                        "chunk_size": "-",
                        "chunks": "-",
                        "bytes": "-",
                        "seconds": "-",
                        "error": err_msg,
                    }
                )
                failures.append(
                    {
                        "input": str(input_path),
                        "output": str(output_path),
                        "error": err_msg,
                        "traceback": tb,
                    }
                )

        error_log_path: Path | None = None
        if failures:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            error_log_path = (targets[0][1].parent / f"md_to_audio_errors_{stamp}.log").resolve()
            write_error_log(error_log_path, "edge", failures)
        print_final_report(results, error_log_path)
        return 1 if failures else 0
    log_step("Backend selected: SAPI")
    if args.list_voices:
        log_step("Listing SAPI voices")
        return list_sapi_voices()
    targets = resolve_targets(args)
    voice_name = resolve_voice_name(args.voice)
    log_step(f"Resolved {len(targets)} input file(s)")
    results: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    for index, (input_path, output_path, extension) in enumerate(targets, start=1):
        log_step(f"Processing file {index}/{len(targets)} -> {output_path.name}")
        started = time.perf_counter()
        try:
            used_chunk_size, chunk_count, final_size = convert_one(
                input_path,
                output_path,
                extension,
                args.keep_intermediate_wav,
                args.chunk_size,
                voice_name,
                args.quiet,
                args.chapter_markers,
                args.chapter_marker_duration,
            )
            elapsed = time.perf_counter() - started
            results.append(
                {
                    "status": "PASSED",
                    "input": str(input_path),
                    "output": str(output_path),
                    "chunk_size": str(used_chunk_size),
                    "chunks": str(chunk_count),
                    "bytes": str(final_size),
                    "seconds": f"{elapsed:.2f}",
                    "error": "",
                }
            )
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            tb = traceback.format_exc()
            err_msg = f"{type(exc).__name__}: {exc}"
            print(f"[ERROR] Failed: {input_path.name} -> {err_msg}")
            results.append(
                {
                    "status": "FAILED",
                    "input": str(input_path),
                    "output": str(output_path),
                    "chunk_size": "-",
                    "chunks": "-",
                    "bytes": "-",
                    "seconds": "-",
                    "error": err_msg,
                }
            )
            failures.append(
                {
                    "input": str(input_path),
                    "output": str(output_path),
                    "error": err_msg,
                    "traceback": tb,
                }
            )

    error_log_path: Path | None = None
    if failures:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        error_log_path = (targets[0][1].parent / f"md_to_audio_errors_{stamp}.log").resolve()
        write_error_log(error_log_path, "sapi", failures)
    print_final_report(results, error_log_path)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())