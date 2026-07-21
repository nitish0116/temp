"""Tests for audio discovery, formatting, probing, and command construction."""

from pathlib import Path
from unittest.mock import Mock
from argparse import Namespace
import subprocess

import pytest

import mp3_to_youtube as app


def test_formatting_naming_and_estimates():
    assert app.fmt_duration(3661) == "1h 01m 01s"
    assert app.fmt_bytes(500) == "500 bytes"
    assert "KB" in app.fmt_bytes(2048)
    assert "MB" in app.fmt_bytes(2 * 1024**2)
    assert app.clean_stem(Path("Book_[Release].mp3")) == "Book"
    assert app.estimate_size(60, "720p").startswith("~")


def test_collect_audio_inputs_filters_and_sorts(tmp_path):
    (tmp_path / "b.wav").touch()
    (tmp_path / "a.mp3").touch()
    (tmp_path / "notes.txt").touch()
    assert [p.name for p in app.collect_audio_inputs(tmp_path)] == ["a.mp3", "b.wav"]
    assert app.collect_audio_inputs(tmp_path / "a.mp3") == [tmp_path / "a.mp3"]
    with pytest.raises(SystemExit):
        app.collect_audio_inputs(tmp_path / "missing")


def test_probe_parses_ffprobe_json(monkeypatch, tmp_path):
    completed = Mock(stdout='{"format":{"duration":"12.5","size":"2048"}}')
    monkeypatch.setattr(app.subprocess, "run", Mock(return_value=completed))
    result = app.probe(tmp_path / "book.mp3", "ffprobe")
    assert result["duration_s"] == 12.5
    assert result["size_bytes"] == 2048


def test_check_tools_accepts_discovered_binaries(monkeypatch):
    monkeypatch.setattr(app.shutil, "which", lambda name: f"C:/{name}.exe")
    assert app.check_tools() == ("C:/ffmpeg.exe", "C:/ffprobe.exe")


def test_check_tools_and_probe_failure_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(app.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit):
        app.check_tools()

    monkeypatch.setattr(app.subprocess, "run", Mock(side_effect=OSError("broken")))
    assert app.probe(tmp_path / "bad.mp3", "ffprobe") == {}


def test_convert_builds_black_video_command(monkeypatch, tmp_path):
    source = tmp_path / "book.mp3"
    source.write_bytes(b"audio")
    output = tmp_path / "book.mp4"
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"video")
        return Mock(returncode=0)

    monkeypatch.setattr(app.subprocess, "run", fake_run)
    monkeypatch.setattr(app.time, "perf_counter", Mock(side_effect=[10.0, 12.0]))
    app.convert(source, output, 61, "Title", "Artist", "Album", "480p", None, "ffmpeg")
    assert output.read_bytes() == b"video"
    assert "color=c=black:s=854x480:r=1" in commands[0]
    assert "title=Title" in commands[0]


def test_convert_with_image_and_thumbnail_uses_two_passes(monkeypatch, tmp_path):
    source = tmp_path / "book.mp3"
    image = tmp_path / "cover.jpg"
    thumbnail = tmp_path / "thumb.png"
    output = tmp_path / "book.mp4"
    source.write_bytes(b"audio")
    image.write_bytes(b"image")
    thumbnail.write_bytes(b"thumb")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"video")
        return Mock(returncode=0)

    monkeypatch.setattr(app.subprocess, "run", fake_run)
    app.convert(source, output, 10, "", "", "", "360p", thumbnail, "ffmpeg", image)
    assert len(commands) == 2
    assert "-loop" in commands[0]
    assert "attached_pic" in commands[1]
    assert output.exists() and not output.with_suffix(".tmp.mp4").exists()


def test_convert_reports_ffmpeg_and_empty_output_failures(monkeypatch, tmp_path):
    source = tmp_path / "book.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        app.subprocess,
        "run",
        Mock(side_effect=subprocess.CalledProcessError(3, "ffmpeg")),
    )
    with pytest.raises(SystemExit):
        app.convert(source, tmp_path / "out.mp4", 2, "", "", "", "720p", None, "ffmpeg")

    monkeypatch.setattr(app.subprocess, "run", Mock(return_value=Mock(returncode=0)))
    with pytest.raises(SystemExit):
        app.convert(source, tmp_path / "empty.mp4", 2, "", "", "", "720p", None, "ffmpeg")


def test_main_single_input_uses_metadata_and_existing_image(monkeypatch, tmp_path):
    source = tmp_path / "book.mp3"
    image = tmp_path / "cover.jpg"
    source.touch()
    image.touch()
    converted = []
    args = Namespace(
        input=str(source), output=None, title="", artist="", album="",
        resolution="720p", thumbnail=None, image=str(image),
    )
    monkeypatch.setattr(app, "parse_args", lambda: args)
    monkeypatch.setattr(app, "check_tools", lambda: ("ffmpeg", "ffprobe"))
    monkeypatch.setattr(
        app,
        "probe",
        lambda src, tool: {"duration_s": 7 * 3600, "size_bytes": 10, "sample_rate": 44100,
                           "channels": 2, "bit_rate_kbps": 128, "title": "Tagged",
                           "artist": "Author", "album": "Series"},
    )
    monkeypatch.setattr(app, "convert", lambda **kwargs: converted.append(kwargs))
    assert app.main() == 0
    assert converted[0]["title"] == "Tagged"
    assert converted[0]["thumbnail"] == image.resolve()


def test_main_batch_skips_unknown_duration_and_counts_failure(monkeypatch, tmp_path):
    source = tmp_path / "audio"
    source.mkdir()
    first, second = source / "one.mp3", source / "two.wav"
    first.touch()
    second.touch()
    args = Namespace(
        input=str(source), output=str(tmp_path / "out"), title="Title", artist="Artist",
        album="Album", resolution="480p", thumbnail="missing.png", image="missing.jpg",
    )
    monkeypatch.setattr(app, "parse_args", lambda: args)
    monkeypatch.setattr(app, "check_tools", lambda: ("ffmpeg", "ffprobe"))
    monkeypatch.setattr(app, "probe", lambda src, tool: {} if src == first else {
        "duration_s": 10, "size_bytes": 1, "sample_rate": "?", "channels": "?",
        "bit_rate_kbps": 0,
    })
    monkeypatch.setattr(app, "convert", lambda **kwargs: None)
    assert app.main() == 1
