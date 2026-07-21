"""Tests for Markdown cleanup, naming, and PDF conversion."""

from pathlib import Path
import sys

import md_to_pdf


def test_clean_markdown_reflows_noise_and_promotes_heading():
    source = "```\nChapter 2 | Dawn\n```\nA wrapped\nline ends here.\n◆◇◆\nNext paragraph."
    cleaned = md_to_pdf._clean_markdown(source)
    assert "# Chapter 2" in cleaned
    assert "A wrapped line ends here." in cleaned
    assert "◆" not in cleaned and "```" not in cleaned


def test_output_names_are_readable_and_collision_safe(tmp_path):
    source = tmp_path / "Tanya_Volume_01_[Kobo].md"
    assert md_to_pdf._crisp_stem_from_markdown(str(source)) == "Tanya Volume 1"
    first = md_to_pdf._default_pdf_output_path(str(source))
    second = md_to_pdf._default_pdf_output_path(str(source), {first})
    assert first.endswith("Tanya Volume 1.pdf")
    assert second.endswith("Tanya Volume 1 (2).pdf")


def test_convert_creates_nonempty_pdf(tmp_path):
    source = tmp_path / "book.md"
    output = tmp_path / "book.pdf"
    source.write_text("# Chapter 1\n\nReadable narrative text.", encoding="utf-8")
    md_to_pdf.convert(str(source), str(output))
    assert output.read_bytes().startswith(b"%PDF")
    assert output.stat().st_size > 500


def test_convert_rejects_empty_markdown(tmp_path):
    source = tmp_path / "empty.md"
    source.write_text("", encoding="utf-8")
    try:
        md_to_pdf.convert(str(source), str(tmp_path / "empty.pdf"))
    except ValueError as exc:
        assert "No readable content" in str(exc)
    else:
        raise AssertionError("empty Markdown should not produce a PDF")


def test_flowable_builder_handles_supported_html():
    parser = md_to_pdf._FlowableBuilder()
    parser.feed(
        "<h1>Title</h1><p>A <strong>bold</strong> &amp; <em>nice</em><br>line</p>"
        "<ul><li>one</li></ul><ol><li>two</li></ol><blockquote>quote</blockquote><hr>"
    )
    assert ("h1", "Title") in parser.blocks
    assert any(tag == "p" and "<b>bold</b>" in text for tag, text in parser.blocks)
    assert ("li", "&bull;&nbsp;one") in parser.blocks
    assert ("li", "1.&nbsp;two") in parser.blocks
    assert ("blockquote", "quote") in parser.blocks
    assert ("hr", "") in parser.blocks


def test_dependency_detection_installs_only_missing_package(monkeypatch):
    real_import = __import__
    installed = []

    def fake_import(name, *args, **kwargs):
        if name == "markdown":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    monkeypatch.setattr(md_to_pdf.subprocess, "check_call", installed.append)
    md_to_pdf._ensure_dependencies()
    assert installed and installed[0][-1] == "markdown"


def test_default_file_discovery_and_fallback_name(monkeypatch):
    monkeypatch.setattr(md_to_pdf.glob, "glob", lambda pattern: ["b.md"])
    assert md_to_pdf._default_input() == "b.md"
    assert md_to_pdf._list_markdown_files() == ["b.md"]
    assert md_to_pdf._crisp_stem_from_markdown("[tag] ___.md") == "converted"


def test_main_converts_single_file(monkeypatch, tmp_path):
    source = tmp_path / "input.md"
    source.write_text("text", encoding="utf-8")
    calls = []
    monkeypatch.setattr(sys, "argv", ["md_to_pdf.py", str(source)])
    monkeypatch.setattr(md_to_pdf, "_ensure_dependencies", lambda: None)
    monkeypatch.setattr(md_to_pdf, "convert", lambda src, dst: calls.append((src, dst)))
    assert md_to_pdf.main() == 0
    assert calls[0][0] == str(source.resolve())
    assert calls[0][1].endswith("input.pdf")


def test_main_all_continues_after_conversion_failure(monkeypatch, tmp_path):
    files = [str(tmp_path / "one.md"), str(tmp_path / "two.md")]
    monkeypatch.setattr(sys, "argv", ["md_to_pdf.py", "--all"])
    monkeypatch.setattr(md_to_pdf, "_list_markdown_files", lambda: files)
    monkeypatch.setattr(md_to_pdf, "_ensure_dependencies", lambda: None)
    monkeypatch.setattr(
        md_to_pdf,
        "convert",
        lambda src, dst: (_ for _ in ()).throw(RuntimeError("bad")) if src.endswith("two.md") else None,
    )
    assert md_to_pdf.main() == 1


def test_main_reports_missing_and_multiple_inputs(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["md_to_pdf.py"])
    monkeypatch.setattr(md_to_pdf, "_default_input", lambda: None)
    monkeypatch.setattr(md_to_pdf, "_list_markdown_files", lambda: ["one.md", "two.md"])
    assert md_to_pdf.main() == 1

    monkeypatch.setattr(sys, "argv", ["md_to_pdf.py", "missing.md"])
    assert md_to_pdf.main() == 1
