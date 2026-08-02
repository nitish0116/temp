"""Tests for the folder statistics command-line utility."""

from __future__ import annotations

import io
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

import folder_file_stats as stats


def _mp4_box(kind: bytes, payload: bytes) -> bytes:
    """Build a small ISO media box for native-duration tests."""
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def test_help_renders_without_percent_formatting_error(capsys) -> None:
    """Argparse help should render successfully and describe both filters."""
    with pytest.raises(SystemExit) as exc_info:
        stats.main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "--skip-below-master-avg" in output
    assert "5 percent" in output
    assert "tolerance" in output


def test_resolve_report_options_normalizes_paths_and_suffix(tmp_path: Path) -> None:
    """CLI paths become absolute and output names receive an HTA suffix."""
    parser = stats._build_argument_parser()
    args = parser.parse_args([str(tmp_path), "-o", str(tmp_path / "reports" / "summary")])

    options = stats._resolve_report_options(args)

    assert options.root_path == str(tmp_path.resolve())
    assert options.output_path == str((tmp_path / "reports" / "summary.hta").resolve())
    assert options.skip_below_master_avg is False


def test_main_writes_report_and_creates_output_parent(tmp_path: Path) -> None:
    """A small directory tree should produce a readable HTA report."""
    source = tmp_path / "source"
    child = source / "child"
    child.mkdir(parents=True)
    (source / "root.txt").write_text("root", encoding="utf-8")
    (child / "nested.txt").write_text("nested", encoding="utf-8")
    output = tmp_path / "reports" / "folder-summary.hta"

    exit_code = stats.main([str(source), "-o", str(output)])

    assert exit_code == 0
    report = output.read_text(encoding="utf-8")
    assert "Total folders:</strong> 2" in report
    assert "Total files:</strong> 2" in report
    assert str(source.resolve()) in report


def test_main_rejects_non_directory(tmp_path: Path, capsys) -> None:
    """A missing input path should return a failure without writing output."""
    missing = tmp_path / "missing"

    exit_code = stats.main([str(missing)])

    assert exit_code == 1
    assert "is not a valid directory" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("filename", "expected"),
    [("movie.MP4", True), ("clip.webm", True), ("notes.txt", False)],
)
def test_is_video_file_is_case_insensitive(filename: str, expected: bool) -> None:
    assert stats._is_video_file(filename) is expected


@pytest.mark.parametrize("version", [0, 1])
def test_duration_mp4_reads_versioned_movie_headers(
    tmp_path: Path, version: int
) -> None:
    """Both 32-bit and 64-bit mvhd layouts should yield seconds."""
    if version == 0:
        timing = b"\0" * 8 + struct.pack(">II", 1_000, 2_500)
    else:
        timing = b"\0" * 16 + struct.pack(">IQ", 1_000, 2_500)
    mvhd = _mp4_box(b"mvhd", bytes([version]) + b"\0\0\0" + timing)
    media = tmp_path / "sample.mp4"
    media.write_bytes(_mp4_box(b"moov", mvhd))

    assert stats._duration_mp4(str(media)) == pytest.approx(2.5)
    assert stats._duration_via_native(str(media)) == pytest.approx(2.5)


def test_native_duration_returns_none_for_bad_or_unsupported_files(
    tmp_path: Path,
) -> None:
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not-an-mp4")
    unsupported = tmp_path / "audio.wav"
    unsupported.write_bytes(b"data")

    assert stats._duration_via_native(str(broken)) is None
    assert stats._duration_via_native(str(unsupported)) is None


def test_mp4_atom_reader_handles_extended_zero_and_invalid_boxes() -> None:
    extended = struct.pack(">I4sQ", 1, b"wide", 20) + b"data"
    assert stats._mp4_find_atom(io.BytesIO(extended), b"wide", 0, len(extended)) == (
        16,
        20,
    )

    zero_sized = struct.pack(">I4s", 0, b"tail") + b"payload"
    assert stats._mp4_find_atom(
        io.BytesIO(zero_sized), b"tail", 0, len(zero_sized)
    ) == (8, len(zero_sized))

    invalid = struct.pack(">I4s", 4, b"bad!")
    assert stats._mp4_find_atom(io.BytesIO(invalid), b"none", 0, len(invalid)) is None


def test_ebml_primitive_readers_cover_valid_and_invalid_values() -> None:
    stream = io.BytesIO(b"\x81\x82\x01\x02" + struct.pack(">f", 1.5))

    assert stats._ebml_read_vint(stream, 0, strip_marker=False) == (0x81, 1)
    assert stats._ebml_read_vint(stream, 1, strip_marker=True) == (2, 1)
    assert stats._ebml_read_element(stream, 0, 4) == (0x81, 2, 2, 4)
    assert stats._ebml_read_uint(stream, 2, 2) == 258
    assert stats._ebml_read_uint(stream, 2, 0) is None
    assert stats._ebml_read_float(stream, 4, 4) == pytest.approx(1.5)
    assert stats._ebml_read_float(stream, 4, 3) is None
    assert stats._ebml_read_element(stream, 4, 4) is None
    assert stats._ebml_read_vint(io.BytesIO(b"\0"), 0, True) is None


def test_ebml_find_locates_requested_element() -> None:
    # IDs 0x81 and 0x82, each followed by a one-byte size and payload.
    stream = io.BytesIO(b"\x81\x81A\x82\x82BC")
    assert stats._ebml_find(stream, 0x82, 0, 7) == (5, 7)
    assert stats._ebml_find(stream, 0x83, 0, 7) is None


def test_duration_fallback_chain_prefers_native_then_probe_then_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stats, "_duration_via_native", lambda _path: 3.0)
    assert stats._get_video_duration_seconds("movie.mp4") == 3.0

    monkeypatch.setattr(stats, "_duration_via_native", lambda _path: None)
    monkeypatch.setattr(stats, "_find_ffprobe", lambda: "ffprobe")
    monkeypatch.setattr(stats, "_duration_via_ffprobe", lambda *_args: 4.0)
    assert stats._get_video_duration_seconds("movie.avi") == 4.0

    monkeypatch.setattr(stats, "_duration_via_ffprobe", lambda *_args: None)
    monkeypatch.setattr(stats, "_find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(stats, "_duration_via_ffmpeg", lambda *_args: 5.0)
    assert stats._get_video_duration_seconds("movie.avi") == 5.0

    monkeypatch.setattr(stats, "_duration_via_ffmpeg", lambda *_args: None)
    monkeypatch.setattr(stats, "_duration_via_moviepy", lambda _path: 6.0)
    assert stats._get_video_duration_seconds("movie.avi") == 6.0


def test_subprocess_duration_readers_parse_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        stats.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="12.75\n", stderr=""),
    )
    assert stats._duration_via_ffprobe("ffprobe", "movie.mp4") == 12.75

    monkeypatch.setattr(
        stats.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="", stderr="Duration: 01:02:03.50, start: 0.0"
        ),
    )
    assert stats._duration_via_ffmpeg("ffmpeg", "movie.mp4") == 3723.5

    monkeypatch.setattr(
        stats.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="", stderr="no duration"),
    )
    assert stats._duration_via_ffmpeg("ffmpeg", "movie.mp4") is None


def test_subprocess_duration_readers_tolerate_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args, **_kwargs):
        raise OSError("tool unavailable")

    monkeypatch.setattr(stats.subprocess, "run", fail)
    assert stats._duration_via_ffprobe("missing", "movie.mp4") is None
    assert stats._duration_via_ffmpeg("missing", "movie.mp4") is None


def test_duration_cache_round_trip_and_invalid_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = tmp_path / "duration-cache.json"
    monkeypatch.setattr(stats, "_DURATION_CACHE_PATH", str(cache_path))

    stats._save_duration_cache({"movie.mp4": [10, 20, 30.0]})
    assert stats._load_duration_cache() == {"movie.mp4": [10, 20, 30.0]}

    cache_path.write_text("[]", encoding="utf-8")
    assert stats._load_duration_cache() == {}
    cache_path.write_text("not json", encoding="utf-8")
    assert stats._load_duration_cache() == {}


def test_compute_video_durations_uses_cache_and_probes_misses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cached = tmp_path / "cached.mp4"
    fresh = tmp_path / "fresh.mp4"
    cached.write_bytes(b"cached")
    fresh.write_bytes(b"fresh")
    cached_stat = cached.stat()
    cache = {
        str(cached): [cached_stat.st_size, int(cached_stat.st_mtime), 7.0]
    }
    saved: list[dict[str, list]] = []
    monkeypatch.setattr(stats, "_load_duration_cache", lambda: cache)
    monkeypatch.setattr(stats, "_save_duration_cache", lambda value: saved.append(dict(value)))
    monkeypatch.setattr(stats, "_get_video_duration_seconds", lambda _path: 9.0)

    durations = stats._compute_video_durations(
        [str(cached), str(fresh), str(tmp_path / "missing.mp4")], max_workers=1
    )

    assert durations[str(cached)] == 7.0
    assert durations[str(fresh)] == 9.0
    assert durations[str(tmp_path / "missing.mp4")] == 0.0
    assert saved[-1][str(fresh)][2] == 9.0
    assert stats._compute_video_durations([]) == {}


def test_collect_folder_summary_aggregates_video_stats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "source"
    child = root / "child"
    child.mkdir(parents=True)
    video = child / "clip.mp4"
    video.write_bytes(b"12345678")
    (root / "notes.txt").write_text("abc", encoding="utf-8")
    monkeypatch.setattr(
        stats,
        "_compute_video_durations",
        lambda paths: {paths[0]: 120.0},
    )

    summary = stats._collect_folder_summary(str(root))

    assert summary[str(child)].direct_video_files == 1
    assert summary[str(root)].total_files == 2
    assert summary[str(root)].total_size == 11
    assert summary[str(root)].total_video_duration_seconds == 120.0


def test_formatting_and_progress_helpers(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert stats._format_duration(0.5) == "500 ms"
    assert stats._format_duration(2.5) == "2.50s"
    assert stats._format_duration(65) == "1m 5.0s"
    assert stats._format_duration(3665) == "1h 1m 5.0s"
    assert stats._human_size(1024) == "1.00 KB"
    assert stats._human_avg_size_per_min(1024, 0) == "N/A"
    assert stats._human_avg_size_per_min(1024, 60) == "1.00 KB"
    assert stats._to_file_uri(str(tmp_path)).endswith("/")
    assert stats._to_windows_path(str(tmp_path)) == str(tmp_path.resolve())

    stats._print_progress_bar(0, 0)
    stats._print_progress_bar(1, 2, prefix="Test", length=2)
    stats._print_progress_bar(1, 2, prefix="Test", length=2)
    stats._print_progress_bar(2, 2, prefix="Test", length=2)
    monkeypatch.setattr(stats.time, "perf_counter", lambda: 10.0)
    stats._log_progress("work", "start")
    monkeypatch.setattr(stats.time, "perf_counter", lambda: 12.0)
    stats._log_progress("work", "end")
    stats._log_progress("unknown", "end")
    output = capsys.readouterr().out
    assert "2/2" in output
    assert "took 2.00s" in output


def test_hta_filter_omits_below_threshold_rows(tmp_path: Path) -> None:
    root = str((tmp_path / "root").resolve())
    child = str((tmp_path / "root" / "small").resolve())
    output = tmp_path / "filtered.hta"
    summary = {
        root: stats.FolderStats(total_files=2, total_size=200),
        child: stats.FolderStats(total_files=1, total_size=50),
    }

    stats._write_hta_summary(root, summary, str(output), skip_below_master_avg=True)

    report = output.read_text(encoding="utf-8")
    assert "Yes (5% tolerance)" in report
    assert "Rows shown:</strong> 0 / 2" in report
    assert "small</td>" not in report
