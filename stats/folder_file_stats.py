#!/usr/bin/env python3
"""Recursively compute exhaustive folder statistics for a directory tree."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Suppress console windows for subprocess calls on Windows.
_SUBPROCESS_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Persistent cache of video durations, keyed by absolute path. Each entry stores
# (size, mtime, duration) so a file is only re-probed when it actually changes.
_DURATION_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".duration_cache.json"
)


# Script-level arguments are checked first. Set any value to None to allow
# fallback to the same argument from terminal input.
SCRIPT_ARGS = {
    "path": r"D:\wd stuff\WD Software Offline Installers\For Windows\WD Backup\redist\sorts",
    "output": None,
    #"skip_below_master_avg": "--skip-below-master-avg",
    "skip_below_master_avg": None,
}


@dataclass
class FolderStats:
    """Aggregated folder statistics."""

    direct_files: int = 0
    direct_size: int = 0
    subfolders: int = 0
    total_files: int = 0
    total_size: int = 0
    direct_video_files: int = 0
    direct_video_size: int = 0
    direct_video_duration_seconds: float = 0.0
    total_video_files: int = 0
    total_video_size: int = 0
    total_video_duration_seconds: float = 0.0


def _is_video_file(filename: str) -> bool:
    """Return True for common video file extensions."""
    video_extensions = {
        ".3gp",
        ".avi",
        ".flv",
        ".m2ts",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".mts",
        ".ts",
        ".webm",
        ".wmv",
    }
    return Path(filename).suffix.lower() in video_extensions


def _get_video_duration_seconds(file_path: str) -> float:
    """Return video duration in seconds when available; otherwise 0.0.

    Tries a fast pure-Python container reader first (no subprocess), then
    ffprobe/ffmpeg, and only falls back to the much slower moviepy loader
    when no ffmpeg tooling is available.
    """
    duration = _duration_via_native(file_path)
    if duration is not None:
        return duration

    ffprobe = _find_ffprobe()
    if ffprobe:
        duration = _duration_via_ffprobe(ffprobe, file_path)
        if duration is not None:
            return duration

    ffmpeg = _find_ffmpeg()
    if ffmpeg:
        duration = _duration_via_ffmpeg(ffmpeg, file_path)
        if duration is not None:
            return duration

    return _duration_via_moviepy(file_path)


def _duration_via_native(file_path: str) -> Optional[float]:
    """Read duration using a pure-Python container parser (no subprocess).

    Supports the two dominant families:
      * ISO base media / MP4 (.mp4, .m4v, .mov, .3gp) via the 'moov/mvhd' atom.
      * Matroska / WebM (.mkv, .webm) via the EBML 'Info' element.
    Returns None when the format is unsupported or parsing fails, so callers
    can fall back to ffprobe.
    """
    suffix = Path(file_path).suffix.lower()
    try:
        if suffix in {".mp4", ".m4v", ".mov", ".3gp", ".m4a"}:
            return _duration_mp4(file_path)
        if suffix in {".mkv", ".webm"}:
            return _duration_matroska(file_path)
    except Exception:
        return None
    return None


def _duration_mp4(file_path: str) -> Optional[float]:
    """Parse an ISO base media (MP4/MOV) file for its duration in seconds."""
    with open(file_path, "rb", buffering=0) as handle:
        moov_range = _mp4_find_atom(handle, b"moov", 0, os.path.getsize(file_path))
        if moov_range is None:
            return None
        moov_start, moov_end = moov_range
        mvhd_range = _mp4_find_atom(handle, b"mvhd", moov_start, moov_end)
        if mvhd_range is None:
            return None

        mvhd_start, _ = mvhd_range
        handle.seek(mvhd_start)
        version = handle.read(1)
        if not version:
            return None
        handle.read(3)  # flags
        if version[0] == 1:
            handle.read(16)  # 64-bit creation/modification time
            timescale = struct.unpack(">I", handle.read(4))[0]
            duration = struct.unpack(">Q", handle.read(8))[0]
        else:
            handle.read(8)  # 32-bit creation/modification time
            timescale = struct.unpack(">I", handle.read(4))[0]
            duration = struct.unpack(">I", handle.read(4))[0]

        if timescale:
            return duration / timescale
    return None


def _mp4_find_atom(handle, wanted: bytes, start: int, end: int) -> Optional[Tuple[int, int]]:
    """Return (payload_start, payload_end) of the first `wanted` box in range.

    Scans sibling boxes between `start` and `end`; recurses into `moov` when
    searching for a nested box such as `mvhd`.
    """
    offset = start
    while offset + 8 <= end:
        handle.seek(offset)
        header = handle.read(8)
        if len(header) < 8:
            return None
        size = struct.unpack(">I", header[:4])[0]
        atom_type = header[4:8]
        header_size = 8
        if size == 1:
            ext = handle.read(8)
            if len(ext) < 8:
                return None
            size = struct.unpack(">Q", ext)[0]
            header_size = 16
        elif size == 0:
            size = end - offset  # extends to end of range

        if size < header_size:
            return None

        payload_start = offset + header_size
        payload_end = offset + size
        if atom_type == wanted:
            return (payload_start, payload_end)
        offset = payload_end
    return None


def _duration_matroska(file_path: str) -> Optional[float]:
    """Parse a Matroska/WebM file for its duration in seconds."""
    with open(file_path, "rb", buffering=0) as handle:
        file_size = os.path.getsize(file_path)
        # Locate the Segment element (ID 0x18538067), then its Info child.
        segment = _ebml_find(handle, 0x18538067, 0, min(file_size, 1 << 20))
        if segment is None:
            return None
        seg_start, seg_end = segment
        info = _ebml_find(handle, 0x1549A966, seg_start, min(seg_end, seg_start + (1 << 20)))
        if info is None:
            return None

        info_start, info_end = info
        timecode_scale = 1_000_000  # Matroska default (nanoseconds).
        duration_ticks: Optional[float] = None

        offset = info_start
        while offset < info_end:
            element = _ebml_read_element(handle, offset, info_end)
            if element is None:
                break
            elem_id, data_start, data_size, next_offset = element
            if elem_id == 0x2AD7B1:  # TimecodeScale (uint)
                timecode_scale = _ebml_read_uint(handle, data_start, data_size) or timecode_scale
            elif elem_id == 0x4489:  # Duration (float)
                duration_ticks = _ebml_read_float(handle, data_start, data_size)
            offset = next_offset

        if duration_ticks is not None:
            return duration_ticks * timecode_scale / 1_000_000_000.0
    return None


def _ebml_read_vint(handle, offset: int, strip_marker: bool) -> Optional[Tuple[int, int]]:
    """Read an EBML variable-length integer; return (value, byte_length)."""
    handle.seek(offset)
    first = handle.read(1)
    if not first:
        return None
    first_byte = first[0]
    if first_byte == 0:
        return None
    length = 1
    mask = 0x80
    while not (first_byte & mask):
        length += 1
        mask >>= 1
        if length > 8:
            return None

    rest = handle.read(length - 1)
    if len(rest) < length - 1:
        return None
    value = first_byte
    if strip_marker:
        value = first_byte & (mask - 1)
    for byte in rest:
        value = (value << 8) | byte
    return (value, length)


def _ebml_read_element(handle, offset: int, end: int) -> Optional[Tuple[int, int, int, int]]:
    """Return (element_id, data_start, data_size, next_offset) at `offset`."""
    if offset >= end:
        return None
    id_result = _ebml_read_vint(handle, offset, strip_marker=False)
    if id_result is None:
        return None
    elem_id, id_len = id_result
    size_result = _ebml_read_vint(handle, offset + id_len, strip_marker=True)
    if size_result is None:
        return None
    data_size, size_len = size_result
    data_start = offset + id_len + size_len
    return (elem_id, data_start, data_size, data_start + data_size)


def _ebml_find(handle, wanted_id: int, start: int, end: int) -> Optional[Tuple[int, int]]:
    """Find a top-level EBML element by ID within [start, end); return its data range."""
    offset = start
    while offset < end:
        element = _ebml_read_element(handle, offset, end)
        if element is None:
            return None
        elem_id, data_start, data_size, next_offset = element
        if elem_id == wanted_id:
            return (data_start, data_start + data_size)
        offset = next_offset
    return None


def _ebml_read_uint(handle, offset: int, size: int) -> Optional[int]:
    """Read a big-endian unsigned integer of `size` bytes."""
    if size <= 0 or size > 8:
        return None
    handle.seek(offset)
    data = handle.read(size)
    if len(data) < size:
        return None
    return int.from_bytes(data, "big")


def _ebml_read_float(handle, offset: int, size: int) -> Optional[float]:
    """Read a 4- or 8-byte big-endian IEEE float."""
    handle.seek(offset)
    data = handle.read(size)
    if len(data) < size:
        return None
    if size == 4:
        return struct.unpack(">f", data)[0]
    if size == 8:
        return struct.unpack(">d", data)[0]
    return None


@lru_cache(maxsize=1)
def _find_ffprobe() -> Optional[str]:
    """Locate an ffprobe executable on PATH or next to the bundled ffmpeg."""
    from shutil import which

    found = which("ffprobe")
    if found:
        return found

    ffmpeg = _find_ffmpeg()
    if ffmpeg:
        candidate = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe" if os.name == "nt" else "ffprobe")
        if os.path.isfile(candidate):
            return candidate
    return None


@lru_cache(maxsize=1)
def _find_ffmpeg() -> Optional[str]:
    """Locate an ffmpeg executable on PATH or the one bundled with imageio."""
    from shutil import which

    found = which("ffmpeg")
    if found:
        return found

    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _duration_via_ffprobe(ffprobe: str, file_path: str) -> Optional[float]:
    """Read duration using ffprobe (fast, metadata only)."""
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                # Only read a small slice of the file; the duration lives in
                # the container header, so we avoid scanning deep into the
                # stream, which is the main per-file cost on slow disks.
                "-probesize",
                "5000000",
                "-analyzeduration",
                "0",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                file_path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_SUBPROCESS_FLAGS,
        )
        value = result.stdout.strip()
        return float(value) if value else None
    except Exception:
        return None


_FFMPEG_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _duration_via_ffmpeg(ffmpeg: str, file_path: str) -> Optional[float]:
    """Read duration by parsing ffmpeg's metadata output (no decoding)."""
    try:
        result = subprocess.run(
            [ffmpeg, "-i", file_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_SUBPROCESS_FLAGS,
        )
        match = _FFMPEG_DURATION_RE.search(result.stderr)
        if not match:
            return None
        hours, minutes, seconds = match.groups()
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except Exception:
        return None


def _duration_via_moviepy(file_path: str) -> float:
    """Slow fallback: read duration via moviepy when ffmpeg tooling is absent."""
    try:
        from moviepy import VideoFileClip
    except Exception:
        return 0.0

    try:
        with VideoFileClip(file_path) as clip:
            return float(clip.duration)
    except Exception:
        return 0.0


def _load_duration_cache() -> Dict[str, list]:
    """Load the persistent duration cache from disk; return {} if unavailable."""
    try:
        with open(_DURATION_CACHE_PATH, "r", encoding="utf-8") as cache_file:
            data = json.load(cache_file)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_duration_cache(cache: Dict[str, list]) -> None:
    """Persist the duration cache to disk atomically (best effort).

    Writes to a temporary file first and then replaces the target, so an
    interrupted write can never corrupt an existing cache file.
    """
    try:
        tmp_path = _DURATION_CACHE_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as cache_file:
            json.dump(cache, cache_file, indent=2, sort_keys=True, ensure_ascii=False)
        os.replace(tmp_path, _DURATION_CACHE_PATH)
    except Exception:
        pass


def _compute_video_durations(video_paths: List[str], max_workers: int | None = None) -> Dict[str, float]:
    """Compute durations for all video paths concurrently using a thread pool.

    Uses a persistent on-disk cache keyed by path + size + mtime so that
    unchanged files are never re-probed on subsequent runs.
    """
    durations: Dict[str, float] = {}
    total = len(video_paths)
    if total == 0:
        return durations

    if max_workers is None:
        max_workers = min(32, (os.cpu_count() or 4) * 4)

    cache = _load_duration_cache()
    to_probe: List[str] = []
    probe_signatures: Dict[str, list] = {}
    cache_hits = 0

    # First, satisfy what we can from the cache using current size/mtime.
    for path in video_paths:
        try:
            stat = os.stat(path)
            signature = [stat.st_size, int(stat.st_mtime)]
        except OSError:
            durations[path] = 0.0
            continue

        entry = cache.get(path)
        if entry and entry[0] == signature[0] and entry[1] == signature[1]:
            durations[path] = float(entry[2])
            cache_hits += 1
        else:
            to_probe.append(path)
            # Reuse this stat result when writing the cache entry later,
            # avoiding a second os.stat() call per probed file.
            probe_signatures[path] = signature

    print(f"  cache hits: {cache_hits} / {total} (probing {len(to_probe)} file(s))")

    completed = 0
    probe_total = len(to_probe)
    # Persist the cache periodically so progress survives interruptions and
    # a subsequent run resumes from where this one left off.
    save_interval = max(1, min(500, probe_total // 20 or 1))
    if probe_total:
        from concurrent.futures import as_completed

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {
                executor.submit(_get_video_duration_seconds, path): path
                for path in to_probe
            }
            for future in as_completed(future_to_path):
                path = future_to_path[future]
                duration = future.result()
                durations[path] = duration
                completed += 1
                _print_progress_bar(completed, probe_total, prefix="Reading video durations")

                # Refresh the cache entry, reusing the earlier stat signature.
                signature = probe_signatures.get(path)
                if signature is not None:
                    cache[path] = [signature[0], signature[1], duration]

                # Incrementally flush the cache to disk.
                if completed % save_interval == 0:
                    _save_duration_cache(cache)

        _save_duration_cache(cache)

    return durations


def _collect_folder_summary(root_path: str) -> Dict[str, FolderStats]:
    """Return stats for every folder under root_path, including root itself."""
    summary: Dict[str, FolderStats] = {}
    walk_rows: List[Tuple[str, List[str], List[str]]] = []

    for current_root, dirs, files in os.walk(root_path, topdown=False):
        walk_rows.append((current_root, dirs, files))

    # First pass: gather every video file path so durations can be computed
    # concurrently in a thread pool before aggregating folder statistics.
    video_paths: List[str] = []
    for current_root, _dirs, files in walk_rows:
        for filename in files:
            if _is_video_file(filename):
                video_paths.append(os.path.join(current_root, filename))

    _log_progress(f"reading durations for {len(video_paths)} video(s)", "start")
    video_durations = _compute_video_durations(video_paths)
    _log_progress(f"reading durations for {len(video_paths)} video(s)", "end")

    total_rows = len(walk_rows)
    for index, (current_root, dirs, files) in enumerate(walk_rows):
        _print_progress_bar(index + 1, total_rows, prefix="Collecting folder statistics")
        stats = FolderStats()
        stats.subfolders = len(dirs)

        for filename in files:
            file_path = os.path.join(current_root, filename)
            try:
                file_size = os.path.getsize(file_path)
            except OSError:
                # Ignore files that cannot be read (permissions/links/races).
                continue

            stats.direct_files += 1
            stats.direct_size += file_size

            if _is_video_file(filename):
                stats.direct_video_files += 1
                stats.direct_video_size += file_size
                stats.direct_video_duration_seconds += video_durations.get(file_path, 0.0)

        stats.total_files = stats.direct_files
        stats.total_size = stats.direct_size
        stats.total_video_files = stats.direct_video_files
        stats.total_video_size = stats.direct_video_size
        stats.total_video_duration_seconds = stats.direct_video_duration_seconds

        for subfolder in dirs:
            child_path = os.path.join(current_root, subfolder)
            child_stats = summary.get(child_path)
            if child_stats is None:
                continue
            stats.total_files += child_stats.total_files
            stats.total_size += child_stats.total_size
            stats.total_video_files += child_stats.total_video_files
            stats.total_video_size += child_stats.total_video_size
            stats.total_video_duration_seconds += child_stats.total_video_duration_seconds

        summary[current_root] = stats

    return summary


def _log_progress(message: str, phase: str) -> None:
    """Print a start/end progress tag, reporting elapsed time on completion."""
    if phase.lower() == "start":
        _log_progress._start_times[message] = time.perf_counter()  # type: ignore[attr-defined]
        print(f"[START] {message}")
    else:
        start = _log_progress._start_times.pop(message, None)  # type: ignore[attr-defined]
        if start is not None:
            elapsed = time.perf_counter() - start
            print(f"[END]   {message} (took {_format_duration(elapsed)})")
        else:
            print(f"[END]   {message}")


_log_progress._start_times = {}  # type: ignore[attr-defined]


def _format_duration(seconds: float) -> str:
    """Return a human-readable duration string for an elapsed time in seconds."""
    if seconds < 1.0:
        return f"{seconds * 1000:.0f} ms"
    minutes, secs = divmod(seconds, 60.0)
    hours, minutes = divmod(minutes, 60.0)
    if hours >= 1:
        return f"{int(hours)}h {int(minutes)}m {secs:.1f}s"
    if minutes >= 1:
        return f"{int(minutes)}m {secs:.1f}s"
    return f"{secs:.2f}s"


def _print_progress_bar(current: int, total: int, prefix: str = "Progress", length: int = 40) -> None:
    """Print a console progress bar, throttled to reduce stdout overhead."""
    if total <= 0:
        return

    current = max(0, min(current, total))
    # Only redraw when the filled-bar length changes or on the final tick,
    # so we avoid thousands of stdout flushes for large item counts.
    completed = int((current / total) * length)
    last_completed = _print_progress_bar._last.get(prefix)  # type: ignore[attr-defined]
    if current != total and completed == last_completed:
        return
    _print_progress_bar._last[prefix] = completed  # type: ignore[attr-defined]

    percent = current / total
    bar = "#" * completed + "-" * (length - completed)
    print(f"{prefix}: |{bar}| {current}/{total} ({percent * 100:5.1f}%)", end="\r", flush=True)
    if current == total:
        _print_progress_bar._last.pop(prefix, None)  # type: ignore[attr-defined]
        print()


_print_progress_bar._last = {}  # type: ignore[attr-defined]



def _human_size(size_bytes: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size_bytes)

    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024.0

    return f"{size_bytes:.2f} B"


def _human_avg_size_per_min(size_bytes: float, duration_seconds: float) -> str:
    """Return average size per minute for video content, or 'N/A' when unavailable."""
    if duration_seconds <= 0:
        return "N/A"
    avg_per_min = size_bytes / (duration_seconds / 60.0)
    return _human_size(avg_per_min)


def _to_file_uri(path: str) -> str:
    """Return a clickable file URI for a filesystem path."""
    abs_path = os.path.abspath(path)
    try:
        uri = Path(abs_path).resolve().as_uri()
    except ValueError:
        # Fallback for uncommon path formats where as_uri may fail.
        normalized = abs_path.replace("\\", "/")
        uri = f"file:///{normalized}"

    # Ensure directories use a directory URI, which opens Explorer on Windows.
    if os.path.isdir(abs_path) and not uri.endswith("/"):
        uri += "/"

    return uri


def _to_windows_path(path: str) -> str:
    """Return normalized absolute Windows path for clickable use in Notepad++."""
    return os.path.abspath(path)


def _to_explorer_uri(path: str) -> str:
    """Return a Windows Explorer protocol URI for a folder path."""
    abs_path = os.path.abspath(path).replace("\\", "/")
    if not abs_path.startswith("/"):
        abs_path = "/" + abs_path
    return f"ms-explorer://{abs_path}"


def _default_report_output_path(root_path: str) -> str:
    """Return default HTA output path in current working directory."""
    return os.path.abspath("folder_summary.hta")


def _write_hta_summary(
    root_path: str,
    summary: Dict[str, FolderStats],
    output_report_file: str,
    skip_below_master_avg: bool = False,
) -> None:
    """Write a clickable HTA summary that opens folders in File Explorer."""
    root_stats = summary[root_path]
    root_avg = (root_stats.total_size / root_stats.total_files) if root_stats.total_files else 0.0

    header_cells = [
        "folder",
        "direct_files",
        "subfolders",
        "total_files_recursive",
        "avg_file_size_recursive(human)",
        "total_size_recursive(human)",
        "avg_size/min(video)",
        "folder_path",
    ]

    row_html: List[str] = []
    total_rows = len(summary)
    shown_rows = 0
    filter_threshold = root_avg * 1.05  # 5% tolerance (105%)
    for folder_path in sorted(summary.keys()):
        stats = summary[folder_path]
        avg_recursive = (stats.total_size / stats.total_files) if stats.total_files else 0.0
        avg_video_size_per_min = (
            _human_avg_size_per_min(
                stats.total_video_size,
                stats.total_video_duration_seconds,
            )
            if stats.total_video_duration_seconds > 0
            else "N/A"
        )

        if skip_below_master_avg and avg_recursive < filter_threshold:
            continue

        rel_path = os.path.relpath(folder_path, root_path)
        folder_display = "." if rel_path == "." else rel_path
        folder_path_text = _to_windows_path(folder_path)
        js_path = folder_path_text.replace("\\", "\\\\").replace("'", "\\'")
        shown_rows += 1

        row_html.append(
            "<tr>"
            f"<td>{html.escape(folder_display)}</td>"
            f"<td class='num'>{stats.direct_files}</td>"
            f"<td class='num'>{stats.subfolders}</td>"
            f"<td class='num'>{stats.total_files}</td>"
            f"<td>{html.escape(_human_size(avg_recursive))}</td>"
            f"<td>{html.escape(_human_size(stats.total_size))}</td>"
            f"<td>{html.escape(avg_video_size_per_min)}</td>"
            f"<td><a href='#' onclick=\"openInExplorer('{js_path}'); return false;\">{html.escape(folder_path_text)}</a></td>"
            "</tr>"
        )

    header_html = "".join(
        f"<th class='sortable' onclick='sortTable({idx})'>{html.escape(col)}</th>"
        for idx, col in enumerate(header_cells)
    )

    html_doc = f"""<!doctype html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\">
    <hta:application
        id="folderSummaryApp"
        applicationname="FolderSummary"
        border="thin"
        caption="yes"
        showintaskbar="yes"
        singleinstance="yes"
        windowstate="normal"
    />
    <title>Folder Summary</title>
    <style>
        body {{ font-family: Segoe UI, Arial, sans-serif; margin: 16px; }}
        .summary {{ margin-bottom: 12px; line-height: 1.5; }}
        .controls {{ margin: 10px 0 12px 0; }}
        .controls input {{ width: 380px; max-width: 100%; padding: 6px 8px; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #d0d0d0; padding: 6px 8px; text-align: left; }}
        th {{ background: #f3f3f3; }}
        th.sortable {{ cursor: pointer; user-select: none; }}
        td.num {{ text-align: right; }}
        a {{ color: #0b57d0; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
    </style>
    <script>
        var sortDirections = {{}};

        function safeTrim(value) {{
            return String(value || '').replace(/^\\s+|\\s+$/g, '');
        }}

        function getCellText(cell) {{
            if (!cell) return '';
            if (typeof cell.innerText === 'string') return cell.innerText;
            if (typeof cell.textContent === 'string') return cell.textContent;
            return '';
        }}

        function parseHumanSize(value) {{
            var text = safeTrim(value);
            var parts = text.split(/\\s+/);
            if (parts.length < 2) return parseFloat(text) || 0;
            var num = parseFloat(parts[0]);
            var unit = parts[1].toUpperCase();
            var factor = 1;
            if (unit === 'KB') factor = 1024;
            else if (unit === 'MB') factor = 1024 * 1024;
            else if (unit === 'GB') factor = 1024 * 1024 * 1024;
            else if (unit === 'TB') factor = 1024 * 1024 * 1024 * 1024;
            return (isNaN(num) ? 0 : num * factor);
        }}

        function getCellSortValue(row, colIndex) {{
            var text = safeTrim(getCellText(row.cells[colIndex]));
            // Numeric columns
            if (colIndex === 1 || colIndex === 2 || colIndex === 3) {{
                return parseInt(text, 10) || 0;
            }}
            // Human-size columns
            if (colIndex === 4 || colIndex === 5 || colIndex === 6) {{
                return parseHumanSize(text);
            }}
            return text.toLowerCase();
        }}

        function sortTable(colIndex) {{
            var tbody = document.getElementById('summaryBody');
            var rows = [];
            for (var r = 0; r < tbody.rows.length; r++) {{
                rows.push(tbody.rows[r]);
            }}
            var ascending = !sortDirections[colIndex];
            sortDirections = {{}};
            sortDirections[colIndex] = ascending;

            rows.sort(function(a, b) {{
                var av = getCellSortValue(a, colIndex);
                var bv = getCellSortValue(b, colIndex);
                if (av < bv) return ascending ? -1 : 1;
                if (av > bv) return ascending ? 1 : -1;
                return 0;
            }});

            for (var i = 0; i < rows.length; i++) {{
                tbody.appendChild(rows[i]);
            }}
        }}

        function applyFilter() {{
            var input = document.getElementById('filterInput');
            var query = (input.value || '').toLowerCase();
            var tbody = document.getElementById('summaryBody');
            var rows = tbody.rows;
            var visible = 0;

            for (var i = 0; i < rows.length; i++) {{
                var rowText = getCellText(rows[i]).toLowerCase();
                var show = rowText.indexOf(query) !== -1;
                rows[i].style.display = show ? '' : 'none';
                if (show) visible++;
            }}

            var total = rows.length;
            var stats = document.getElementById('shownRowsStat');
            if (stats) {{
                stats.innerText = 'Rows shown: ' + visible + ' / ' + total;
            }}
        }}

        function openInExplorer(folderPath) {{
            try {{
                var shell = new ActiveXObject("WScript.Shell");
                shell.Run('explorer.exe "' + folderPath + '"', 1, false);
            }} catch (err) {{
                alert('Unable to open File Explorer for: ' + folderPath + '\\n' + err.message);
            }}
        }}
    </script>
</head>
<body>
    <div class=\"summary\">
        <div><strong>Path:</strong> {html.escape(root_path)}</div>
        <div><strong>Total folders:</strong> {len(summary)}</div>
        <div><strong>Total files:</strong> {root_stats.total_files}</div>
        <div><strong>Total size:</strong> {html.escape(_human_size(root_stats.total_size))}</div>
        <div><strong>Average file size:</strong> {html.escape(_human_size(root_avg))}</div>
        <div><strong>Average size/min (videos):</strong> {html.escape(_human_avg_size_per_min(root_stats.total_video_size, root_stats.total_video_duration_seconds))}</div>
        <div><strong>Filter active:</strong> {"Yes (5% tolerance)" if skip_below_master_avg else "No"}</div>
        <div id="shownRowsStat"><strong>Rows shown:</strong> {shown_rows} / {total_rows}</div>
    </div>
    <div class="controls">
        <input id="filterInput" type="text" placeholder="Filter rows..." onkeyup="applyFilter()" />
    </div>
    <table id="summaryTable">
        <thead>
            <tr>{header_html}</tr>
        </thead>
        <tbody id="summaryBody">
            {''.join(row_html)}
        </tbody>
    </table>
</body>
</html>
"""

    with open(output_report_file, "w", encoding="utf-8") as report_html:
        report_html.write(html_doc)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create an exhaustive folder summary HTA report with "
            "clickable folder paths that open in File Explorer."
        )
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Directory path to scan recursively",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output .hta report path (default: folder_summary.hta in current directory)",
    )
    skip_group = parser.add_mutually_exclusive_group()
    skip_group.add_argument(
        "--skip-below-master-avg",
        dest="skip_below_master_avg",
        action="store_true",
        default=None,
        help="Skip rows where folder average file size is below 105% of root folder average (5% tolerance)",
    )
    skip_group.add_argument(
        "--no-skip-below-master-avg",
        dest="skip_below_master_avg",
        action="store_false",
        help="Do not apply average-size skip filter",
    )
    args = parser.parse_args()

    script_path = SCRIPT_ARGS.get("path")
    script_output = SCRIPT_ARGS.get("output")
    script_skip = SCRIPT_ARGS.get("skip_below_master_avg")

    effective_path = script_path if script_path else args.path
    if not effective_path:
        parser.error(
            "Path is required. Set SCRIPT_ARGS['path'] in script or pass path in terminal."
        )

    effective_output = script_output if script_output else args.output
    effective_skip = script_skip if script_skip is not None else args.skip_below_master_avg
    if effective_skip is None:
        effective_skip = False

    target_path = os.path.abspath(effective_path)
    output_report_file = (
        os.path.abspath(effective_output)
        if effective_output
        else _default_report_output_path(target_path)
    )

    if not output_report_file.lower().endswith(".hta"):
        output_report_file += ".hta"

    if not os.path.isdir(target_path):
        print(f"Error: '{target_path}' is not a valid directory.")
        return 1

    overall_start = time.perf_counter()

    _log_progress("collecting folder statistics", "start")
    summary = _collect_folder_summary(target_path)
    _log_progress("collecting folder statistics", "end")

    _log_progress("writing HTA summary report", "start")
    _write_hta_summary(
        target_path,
        summary,
        output_report_file,
        skip_below_master_avg=effective_skip,
    )
    _log_progress("writing HTA summary report", "end")

    print(f"Total time: {_format_duration(time.perf_counter() - overall_start)}")
    print(f"Explorer-clickable HTA report: {output_report_file}")
    print(f"Report link: {_to_file_uri(output_report_file)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
