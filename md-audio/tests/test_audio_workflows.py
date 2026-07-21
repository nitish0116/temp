"""Workflow tests with external speech and media processes replaced by fakes."""

import asyncio
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import md_to_audio as app


def make_args(**overrides):
    values = dict(
        input_path=None, output_path=None, backend="sapi", voice=None,
        chunk_size=None, edge_workers=2, keep_intermediate_wav=False,
        list_voices=False, all_voices=False, quiet=True, chapter_markers=False,
        chapter_marker_duration=2.0, cue_file=False,
    )
    values.update(overrides)
    return Namespace(**values)


def test_reporting_logging_and_default_input(monkeypatch, tmp_path, capsys):
    app.QUIET = False
    app.log_step("working")
    book = tmp_path / "book.md"
    book.touch()
    assert app.default_input_path(tmp_path / "tool.py") == book
    (tmp_path / "other.md").touch()
    with pytest.raises(SystemExit):
        app.default_input_path(tmp_path / "tool.py")

    failures = [{"input": "a.md", "output": "a.mp3", "error": "bad", "traceback": "trace"}]
    log = tmp_path / "errors" / "run.log"
    app.write_error_log(log, "edge", failures)
    assert "failed_files: 1" in log.read_text(encoding="utf-8")
    app.print_final_report(
        [{"status": "PASSED", "input": "a.md", "output": "a.mp3"},
         {"status": "FAILED", "input": "b.md", "output": "b.mp3", "error": "bad"}],
        log,
    )
    assert "Final Conversion Report" in capsys.readouterr().out


def test_chunk_tuning_scene_markers_and_cue_files(tmp_path):
    text = "# 1 April 1924\n\nFirst narrative sentence.\n\n## Second Scene\n\nSecond narrative sentence."
    size, chunks = app.choose_chunk_size_and_chunks(text, "edge", 10, 3, True)
    assert size == 400 and chunks
    auto_size, _ = app.choose_chunk_size_and_chunks(text * 20, "sapi", None, 1, True)
    assert 1200 <= auto_size <= 4500
    scene_chunks, scenes = app.narration_paragraphs_with_scene_markers(text, 100)
    assert scene_chunks and set(scenes.values()) == {"1 April 1924", "Second Scene"}
    output = tmp_path / "book.mp3"
    cue, youtube = app.write_cue_file(output, scenes, scene_chunks, 65_000)
    assert 'FILE "book.mp3" MP3' in cue.read_text(encoding="utf-8")
    assert "0:00" in youtube.read_text(encoding="utf-8")
    fallback, _ = app.write_cue_file(output, {}, ["text"], 1000)
    assert "book" in fallback.read_text(encoding="utf-8")


def test_silence_duration_and_tool_helpers(monkeypatch, tmp_path):
    assert app.generate_silence_chunk(1.5) == "[SILENCE_1.5s]"
    monkeypatch.setattr(app.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError):
        app.create_silence_mp3(tmp_path / "silence.mp3", 1)
    assert app.get_audio_duration_ms(tmp_path / "x.mp3") == 0
    with pytest.raises(SystemExit):
        app.ensure_ffmpeg()

    monkeypatch.setattr(app.shutil, "which", lambda name: f"/{name}")
    monkeypatch.setattr(app.subprocess, "run", Mock(return_value=SimpleNamespace(stdout="1.25\n")))
    assert app.get_audio_duration_ms(tmp_path / "x.mp3") == 1250
    assert app.ensure_ffmpeg() == "/ffmpeg"
    assert app.powershell_executable() == "/powershell"
    app.create_silence_mp3(tmp_path / "silence.mp3", 1)


def test_silence_process_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(app.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(app.subprocess, "run", Mock(side_effect=app.subprocess.TimeoutExpired("ffmpeg", 30)))
    with pytest.raises(RuntimeError, match="timed out"):
        app.create_silence_mp3(tmp_path / "x.mp3", 1)
    error = app.subprocess.CalledProcessError(1, "ffmpeg", stderr=b"bad")
    monkeypatch.setattr(app.subprocess, "run", Mock(side_effect=error))
    with pytest.raises(RuntimeError, match="bad"):
        app.create_silence_mp3(tmp_path / "x.mp3", 1)


def test_edge_voice_resolution_and_listing(monkeypatch, capsys):
    voices = ["en-US-AriaNeural", "en-US-GuyNeural", "custom-TestNeural"]
    monkeypatch.setattr(app, "edge_voice_names", lambda: voices)
    assert app.resolve_edge_voice("aria") == "en-US-AriaNeural"
    assert app.resolve_edge_voice("custom") == "custom-TestNeural"
    with pytest.raises(SystemExit):
        app.resolve_edge_voice("missing")
    assert app.list_edge_voices(True) == 0
    assert "All exact Edge voice names" in capsys.readouterr().out


def test_edge_synthesis_single_and_batch(monkeypatch, tmp_path):
    class Communicate:
        def __init__(self, text, voice):
            self.text = text

        async def save(self, path):
            Path(path).write_bytes(b"audio")

    monkeypatch.setattr(app, "_require_edge_tts", lambda: SimpleNamespace(Communicate=Communicate))
    out = tmp_path / "one.mp3"
    asyncio.run(app._edge_synthesize_chunk("Readable &amp; text", "voice", out))
    assert out.read_bytes() == b"audio"
    empty = tmp_path / "empty.mp3"
    asyncio.run(app._edge_synthesize_chunk("...", "voice", empty))
    assert empty.exists() and empty.stat().st_size == 0
    paths, markers = asyncio.run(
        app._edge_synthesize_chunks_async(
            ["First readable text", "[CHAPTER_END]", "Second readable text"],
            "voice", tmp_path, 2, True, 1.5,
        )
    )
    assert len(paths) == 2 and markers == {1: 1.5}


def test_edge_concat_variants(monkeypatch, tmp_path):
    chunks = [tmp_path / "one.mp3", tmp_path / "empty.mp3"]
    chunks[0].write_bytes(b"audio")
    chunks[1].touch()
    commands = []
    monkeypatch.setattr(app.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(app.subprocess, "run", lambda command, **kwargs: commands.append(command))
    app._edge_concat_mp3(chunks, tmp_path / "out.mp3")
    monkeypatch.setattr(app, "create_silence_mp3", lambda path, duration: path.write_bytes(b"silence"))
    app._edge_concat_mp3_with_chapters([chunks[0]], {1: 2.0}, tmp_path / "chapters.mp3", tmp_path)
    assert len(commands) == 2
    with pytest.raises(RuntimeError):
        app._edge_concat_mp3([chunks[1]], tmp_path / "bad.mp3")


def test_target_resolution_for_edge_and_sapi(tmp_path):
    folder = tmp_path / "books"
    folder.mkdir()
    first, second = folder / "one.md", folder / "two.md"
    first.touch(); second.touch()
    edge = app.resolve_edge_targets(make_args(input_path=str(folder), output_path="audio"))
    assert len(edge) == 2 and edge[0][1].parent == folder / "audio"
    with pytest.raises(SystemExit):
        app.resolve_edge_targets(make_args(input_path=str(folder), output_path="bad.mp3"))
    single = app.resolve_edge_targets(make_args(input_path=str(first), output_path="named.mp3"))
    assert single[0][1].name == "named.mp3"
    with pytest.raises(SystemExit):
        app.resolve_edge_targets(make_args(input_path=str(first), output_path="named.wav"))

    sapi = app.resolve_targets(make_args(input_path=str(folder), output_path="audio"))
    assert len(sapi) == 2 and sapi[0][2] == ".mp3"
    single_sapi = app.resolve_targets(make_args(input_path=str(first), output_path="named.wav"))
    assert single_sapi[0][2] == ".wav"
    with pytest.raises(SystemExit):
        app.resolve_targets(make_args(input_path=str(first), output_path="named.txt"))


def test_sapi_voice_helpers(monkeypatch, capsys):
    completed = SimpleNamespace(stdout="Microsoft David Desktop\nMicrosoft Zira Desktop\n")
    monkeypatch.setattr(app.subprocess, "run", Mock(return_value=completed))
    assert app.installed_voice_names() == ["Microsoft David Desktop", "Microsoft Zira Desktop"]
    assert app.resolve_voice_name(None) is None
    assert app.resolve_voice_name("Dave") == "Microsoft David Desktop"
    assert app.resolve_voice_name("zira") == "Microsoft Zira Desktop"
    with pytest.raises(SystemExit):
        app.resolve_voice_name("unknown")
    assert app.list_sapi_voices() == 0
    assert "Exact installed voice names" in capsys.readouterr().out


def test_sapi_synthesis_and_encoding_commands(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(app.subprocess, "run", lambda command, **kwargs: commands.append(command))
    monkeypatch.setattr(app, "powershell_executable", lambda: "powershell")
    app.synthesize_wav(["Readable narration", "..."], tmp_path / "out.wav", "David", True)
    with pytest.raises(RuntimeError):
        app.synthesize_wav(["..."], tmp_path / "out.wav")
    monkeypatch.setattr(app, "ensure_ffmpeg", lambda: "ffmpeg")
    app.convert_wav_to_mp3(tmp_path / "in.wav", tmp_path / "out.mp3")
    temporary = tmp_path / "chapter_tmp.mp3"
    temporary.write_bytes(b"mp3")
    app.convert_wav_to_mp3_with_chapters(tmp_path / "in.wav", tmp_path / "chapter.mp3", [1])
    assert (tmp_path / "chapter.mp3").exists()
    assert commands


def test_convert_one_sapi_and_edge_workflows(monkeypatch, tmp_path):
    source = tmp_path / "book.md"
    source.write_text("# Scene\nReadable narration text.", encoding="utf-8")
    sapi_out = tmp_path / "book.mp3"

    def fake_synthesize(chunks, path, voice, quiet=False):
        path.write_bytes(b"wav")

    monkeypatch.setattr(app, "synthesize_wav", fake_synthesize)
    monkeypatch.setattr(app, "convert_wav_to_mp3", lambda wav, out: out.write_bytes(b"mp3"))
    result = app.convert_one(source, sapi_out, ".mp3", False, 500, None, True, cue_file=True)
    assert result[0] == 500 and sapi_out.exists()

    edge_out = tmp_path / "edge.mp3"
    monkeypatch.setattr(app, "_require_edge_tts", lambda: object())
    async def fake_chunks(chunks, voice, tmp, workers, quiet, duration):
        path = tmp / "chunk.mp3"; path.write_bytes(b"audio")
        return [path], {}
    monkeypatch.setattr(app, "_edge_synthesize_chunks_async", fake_chunks)
    monkeypatch.setattr(app, "_edge_concat_mp3", lambda paths, out: out.write_bytes(b"edge"))
    edge_result = app.convert_one_edge(source, edge_out, 500, "Aria", 2, True, cue_file=True)
    assert edge_result[0] == 500 and edge_out.exists()


@pytest.mark.parametrize("backend", ["sapi", "edge"])
def test_main_success_and_failure_reports(monkeypatch, tmp_path, backend):
    source = tmp_path / "book.md"; source.touch()
    output = tmp_path / "book.mp3"
    args = make_args(backend=backend, input_path=str(source))
    monkeypatch.setattr(app, "parse_args", lambda: args)
    monkeypatch.setattr(app, "print_final_report", Mock())
    if backend == "edge":
        monkeypatch.setattr(app, "resolve_edge_voice", lambda voice: "Aria")
        monkeypatch.setattr(app, "resolve_edge_targets", lambda args: [(source, output)])
        monkeypatch.setattr(app, "convert_one_edge", lambda *a, **k: (500, 1, 10))
    else:
        monkeypatch.setattr(app, "resolve_targets", lambda args: [(source, output, ".mp3")])
        monkeypatch.setattr(app, "resolve_voice_name", lambda voice: None)
        monkeypatch.setattr(app, "convert_one", lambda *a, **k: (500, 1, 10))
    assert app.main() == 0

    failure = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("failed"))
    monkeypatch.setattr(app, "write_error_log", Mock())
    monkeypatch.setattr(app, "convert_one_edge" if backend == "edge" else "convert_one", failure)
    assert app.main() == 1


def test_main_voice_listing_and_edge_worker_validation(monkeypatch):
    monkeypatch.setattr(app, "parse_args", lambda: make_args(backend="sapi", list_voices=True))
    monkeypatch.setattr(app, "list_sapi_voices", lambda: 0)
    assert app.main() == 0
    monkeypatch.setattr(app, "parse_args", lambda: make_args(backend="edge", list_voices=True))
    monkeypatch.setattr(app, "list_edge_voices", lambda show_all=False: 0)
    assert app.main() == 0
    monkeypatch.setattr(app, "parse_args", lambda: make_args(backend="edge", edge_workers=0))
    with pytest.raises(SystemExit):
        app.main()
