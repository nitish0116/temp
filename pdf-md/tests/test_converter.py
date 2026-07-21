"""Regression tests for PDF/EPUB discovery, conversion, and normalization."""

from pathlib import Path
import zipfile
from types import SimpleNamespace

import pytest

from pdf_to_markdown.converter import (
    ConversionOptions,
    PDFToMarkdownConverter,
    normalize_discretionary_hyphens,
    parse_page_spec,
    readable_output_name,
)
from pdf_to_markdown import cli
from pdf_to_markdown.cli import _document_files, _unique_name


def test_parse_page_spec():
    """Page specifications are converted from one-based to zero-based indexes."""
    assert parse_page_spec("1-3,5") == [0, 1, 2, 4]
    assert parse_page_spec(None) is None


def test_discretionary_soft_hyphens_are_rejoined():
    """Soft-hyphen line wraps are joined without changing visible compounds."""
    text = "bodi\u00ad \nly and de\u00ad\nhydrated, but well-known"
    assert normalize_discretionary_hyphens(text) == (
        "bodily and dehydrated, but well-known"
    )


def test_readable_output_name_drops_release_tags():
    """Generated names omit release tags while retaining the book title."""
    assert (
        readable_output_name("The_Saga_of_Tanya_-_Volume_13_[Kobo].pdf")
        == "The Saga of Tanya - Volume 13.md"
    )
    assert (
        readable_output_name("Overlord v01 [Yen Press] [LuCaZ] {r3}.pdf")
        == "Overlord v01.md"
    )


def test_batch_names_are_collision_safe():
    """Duplicate readable names receive deterministic numeric suffixes."""
    used: set[str] = set()
    assert _unique_name("Book.md", used) == "Book.md"
    assert _unique_name("Book.md", used) == "Book (2).md"


def test_batch_discovery_includes_pdf_and_epub(tmp_path):
    """Batch discovery includes supported formats and ignores other files."""
    (tmp_path / "book.pdf").touch()
    (tmp_path / "novel.EPUB").touch()
    (tmp_path / "notes.txt").touch()

    assert [path.name for path in _document_files(tmp_path, False)] == [
        "book.pdf",
        "novel.EPUB",
    ]


def test_real_epub_is_converted_to_markdown(tmp_path):
    """A standards-shaped EPUB converts into readable Markdown and a report."""
    epub = tmp_path / "Example_Novel_[Test].epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr(
            "mimetype",
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container version="1.0"
 xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
 <rootfiles><rootfile full-path="OEBPS/content.opf"
  media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="UTF-8"?>
<package version="2.0" xmlns="http://www.idpf.org/2007/opf"
 unique-identifier="id">
 <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>Example Novel</dc:title>
  <dc:identifier id="id">test</dc:identifier><dc:language>en</dc:language>
 </metadata>
 <manifest><item id="c1" href="chapter1.xhtml"
  media-type="application/xhtml+xml"/></manifest>
 <spine><itemref idref="c1"/></spine>
</package>""",
        )
        archive.writestr(
            "OEBPS/chapter1.xhtml",
            """<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter 1</title></head><body>
<h1>Chapter 1: Arrival</h1>
<p>This is EPUB narrative text.</p>
<p>The second paragraph remains readable.</p>
</body></html>""",
        )

    output = tmp_path / "Example Novel.md"
    result = PDFToMarkdownConverter(ConversionOptions(show_progress=False)).convert(
        epub, output
    )
    markdown = output.read_text(encoding="utf-8")

    assert "Chapter 1" in markdown
    assert "EPUB narrative text" in markdown
    assert Path(result.report).exists()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"layout_mode": "invalid"},
        {"ocr_mode": "invalid"},
        {"image_mode": "invalid"},
    ],
)
def test_conversion_options_reject_invalid_values(kwargs):
    with pytest.raises(ValueError):
        ConversionOptions(**kwargs)


@pytest.mark.parametrize("spec", ["0", "3-2"])
def test_parse_page_spec_rejects_invalid_ranges(spec):
    with pytest.raises(ValueError):
        parse_page_spec(spec)


def test_converter_rejects_unsupported_file(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("notes", encoding="utf-8")
    with pytest.raises(ValueError, match="supported document"):
        PDFToMarkdownConverter().convert(source, tmp_path / "out.md")


def test_cli_converts_recursive_batch_and_forwards_options(monkeypatch, tmp_path):
    source = tmp_path / "inputs"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (source / "same_[one].pdf").touch()
    (source / "same_[two].epub").touch()
    (nested / "book.pdf").touch()
    output = tmp_path / "output"
    calls = []

    class FakeConverter:
        def __init__(self, options):
            assert options.pages == [0, 2]
            assert options.image_mode == "embed"

        def convert(self, document, target):
            calls.append((document, target))
            return SimpleNamespace(markdown=target, pages=2, characters=123)

    monkeypatch.setattr(cli, "PDFToMarkdownConverter", FakeConverter)
    result = cli.main(
        [str(source), "-o", str(output), "-r", "--pages", "1,3", "--images", "embed", "--quiet"]
    )
    assert result == 0
    assert len(calls) == 3
    assert any(target.parent == output / "nested" for _, target in calls)


def test_cli_empty_directory_and_continue_on_error(monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main([str(empty)]) == 1

    first = tmp_path / "one.pdf"
    second = tmp_path / "two.pdf"
    first.touch()
    second.touch()

    class FailingConverter:
        def __init__(self, options):
            pass

        def convert(self, document, target):
            raise RuntimeError("conversion failed")

    monkeypatch.setattr(cli, "PDFToMarkdownConverter", FailingConverter)
    assert cli.main([str(tmp_path), "--continue-on-error"]) == 2
    assert cli.main([str(first)]) == 2


def test_cli_parser_errors_for_missing_input_and_bad_pages(tmp_path):
    with pytest.raises(SystemExit) as missing:
        cli.main([str(tmp_path / "missing.pdf")])
    assert missing.value.code == 2

    source = tmp_path / "book.pdf"
    source.touch()
    with pytest.raises(SystemExit) as invalid:
        cli.main([str(source), "--pages", "0"])
    assert invalid.value.code == 2
