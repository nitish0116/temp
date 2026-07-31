"""Regression tests for cleanup, Unicode, and reporting package boundaries."""

from __future__ import annotations

import hashlib
import json
import shutil

import pytest

from markdownCleaner.modules.cleanup.document import DocumentCleanupStage
from markdownCleaner.modules.cleanup.stage import NovelCleanupStage
from markdownCleaner.modules.cleanup.tts_validation import TTSValidationStage
from markdownCleaner.modules.core.config import PipelineConfig
from markdownCleaner.modules.core.context import ProcessingContext
from markdownCleaner.modules.markdown.segmenter import MarkdownSegment
from markdownCleaner.modules.report import backup as backup_module
from markdownCleaner.modules.report.backup import BackupManager
from markdownCleaner.modules.report.change_log import ChangeLog
from markdownCleaner.modules.report.exporter import ReportExporter, ReportOptions
from markdownCleaner.modules.report.summary import SummaryReporter
from markdownCleaner.modules.unicode.invisible import InvisibleProcessor
from markdownCleaner.modules.unicode.punctuation import PunctuationProcessor
from markdownCleaner.modules.unicode.stage import UnicodeStage
from markdownCleaner.modules.unicode.whitespace import WhitespaceProcessor


def _context(*, fixes: dict | None = None) -> ProcessingContext:
    return ProcessingContext(
        PipelineConfig({"unicode": {"enabled": True, "fixes": fixes or {}}})
    )


def _segment(text: str) -> MarkdownSegment:
    return MarkdownSegment(
        text=text,
        line_number=7,
        block_index=2,
        segment_index=3,
    )


def _add_change(log: ChangeLog, *, confidence: float, stage: str = "Stage") -> None:
    log.add(
        stage=stage,
        block_index=0,
        segment_index=0,
        line=1,
        before="before",
        after="after",
        confidence=confidence,
        reason="test",
    )


def test_unicode_group_switches_are_honored():
    """Top-level fixes documented in config disable their complete processors."""
    context = _context(
        fixes={
            "invisible_characters": False,
            "whitespace": False,
            "punctuation": False,
        }
    )

    invisible = _segment("A\u200bB")
    whitespace = _segment("A\u00a0  B")
    punctuation = _segment("A—B")

    assert not InvisibleProcessor(context).process(invisible)
    assert not WhitespaceProcessor(context).process(whitespace)
    assert not PunctuationProcessor(context).process(punctuation)
    assert invisible.current_text == "A\u200bB"
    assert whitespace.current_text == "A\u00a0  B"
    assert punctuation.current_text == "A—B"
    assert context.total_changes == 0


def test_invisible_cleanup_is_pure_and_counts_each_removed_character_once():
    """The helper has no statistic side effect and process counts deletions once."""
    context = _context()
    processor = InvisibleProcessor(context)

    assert processor._clean_text("A\u200bB\x07") == "AB"
    assert "zero_width_removed" not in context.statistics

    segment = _segment("A\u200bB\x07")
    assert processor.process(segment)
    assert segment.current_text == "AB"
    assert context.statistics["zero_width_removed"] == 2
    assert context.total_changes == 1


def test_punctuation_normalizes_non_breaking_hyphen():
    """U+2011 is normalized; the old identity mapping accidentally missed it."""
    context = _context()
    segment = _segment("well‑known")

    assert PunctuationProcessor(context).process(segment)
    assert segment.current_text == "well-known"


def test_unicode_stage_collapses_spacing_after_protected_inline_code():
    """A synthetic suffix is mid-line, so its leading spaces are not indentation."""
    context = _context()
    context.segments = [_segment("word`literal  code`  text")]

    UnicodeStage(context.config).process(context)

    assert context.segments[0].current_text == "word`literal  code` text"


def test_legacy_cleanup_processors_share_markup_rules_and_audit_changes():
    """Legacy segment cleanup accepts converter variants and reports both edits."""
    context = _context()
    context.segments = [
        _segment(
            "Before<!-- START OF PICTURE TEXT -->noise"
            "<!-- END OF PICTURE TEXT --><U >Title</U><BR />After"
        )
    ]

    result = NovelCleanupStage(context.config).process(context)

    assert context.segments[0].current_text == "BeforeTitle\nAfter"
    assert result.changes == 2
    assert [record.stage for record in context.tracker.records] == [
        "ImageText",
        "Markdown",
    ]


def test_picture_ocr_and_section_exclusion_share_label_normalization():
    """A prefixed misspelled profile marker survives noise filtering for removal."""
    noise = "\n".join(["-", ".", "x", "=", "/", "a", ":", "z", "~", "|"])
    source = f"""Story ending.

<!-- Start of picture text -->
OVERLORD Character Profles
{noise}
<!-- End of picture text -->

Profile prose must be removed.

# Bonus Short Stories

Keep this.
"""
    filtered, removed, preserved = DocumentCleanupStage._filter_picture_ocr(
        source,
        mode="safe",
        excluded_sections=["Character Profiles"],
    )
    cleaned, removed_sections = DocumentCleanupStage._remove_named_sections(
        filtered,
        ["Character Profiles"],
    )

    assert removed == 0
    assert preserved == 1
    assert removed_sections == ["Character Profiles"]
    assert "Profile prose must be removed." not in cleaned
    assert "Keep this." in cleaned


def test_document_comments_are_removed_with_an_audit_record():
    """Whole-document comment removal can no longer happen silently."""
    config = PipelineConfig(
        {
            "cleanup": {
                "remove_front_matter": False,
                "remove_footnotes": False,
                "strip_markdown_emphasis": False,
                "report_ocr_noise": False,
            }
        }
    )
    context = ProcessingContext(config)
    context.original_markdown = "Story <!-- converter note --> continues."
    context.replace_markdown(context.original_markdown)

    DocumentCleanupStage(config).process(context)

    assert "converter note" not in context.current_markdown
    assert any(
        record.reason == "Removed residual converter HTML comments"
        for record in context.tracker.records
    )


def test_document_cleanup_preserves_literal_markdown_content():
    """Fences, inline code, and link destinations survive whole-document regexes."""
    source = """# Chapter 1

Narration with `**literal  code**` and [docs](https://example.test/a_b).

```markdown
## not a heading
<!-- Start of picture text -->do not remove<!-- End of picture text -->
**literal emphasis**  and spacing
```
"""
    config = PipelineConfig(
        {
            "cleanup": {
                "remove_front_matter": False,
                "remove_footnotes": False,
                "strip_markdown_emphasis": True,
                "report_ocr_noise": False,
            }
        }
    )
    context = ProcessingContext(config)
    context.original_markdown = source
    context.replace_markdown(source)

    DocumentCleanupStage(config).process(context)

    assert "`**literal  code**`" in context.current_markdown
    assert "(https://example.test/a_b)" in context.current_markdown
    assert (
        "```markdown\n"
        "## not a heading\n"
        "<!-- Start of picture text -->do not remove"
        "<!-- End of picture text -->\n"
        "**literal emphasis**  and spacing\n"
        "```"
    ) in context.current_markdown


def test_document_cleanup_normalizes_setext_chapter_heading():
    """A Setext underline cannot be mistaken for a decorative separator."""
    source = "Chapter 1\n=========\n\nNarration remains.\n"
    config = PipelineConfig(
        {
            "cleanup": {
                "excluded_sections": [],
                "remove_front_matter": False,
                "remove_promotional_tail": False,
                "remove_glossary_footnotes": False,
                "remove_publisher_tail": False,
                "remove_footnotes": False,
                "strip_markdown_emphasis": False,
                "report_ocr_noise": False,
            }
        }
    )
    context = ProcessingContext(config)
    context.original_markdown = source
    context.replace_markdown(source)

    DocumentCleanupStage(config).process(context)

    assert context.current_markdown.startswith("# Chapter 1\n")
    assert "=========" not in context.current_markdown
    assert "Narration remains." in context.current_markdown


@pytest.mark.parametrize(
    "regex_config",
    [
        {"enabled": False},
        {
            "enabled": True,
            "corrections": {
                "broken_hyphen_words": {"enabled": False},
            },
        },
    ],
)
def test_document_cleanup_respects_disabled_dehyphenation(regex_config):
    """Parent and child regex switches control document-level joining."""
    source = "A well-\nformed example.\n"
    config = PipelineConfig(
        {
            "cleanup": {
                "excluded_sections": [],
                "remove_front_matter": False,
                "remove_promotional_tail": False,
                "remove_glossary_footnotes": False,
                "remove_publisher_tail": False,
                "remove_footnotes": False,
                "strip_markdown_emphasis": False,
                "report_ocr_noise": False,
            },
            "regex": regex_config,
        }
    )
    context = ProcessingContext(config)
    context.original_markdown = source
    context.replace_markdown(source)

    DocumentCleanupStage(config).process(context)

    assert "well- formed" in context.current_markdown
    assert "wellformed" not in context.current_markdown


def test_document_cleanup_keeps_adjacent_fence_on_block_boundaries():
    """Restoring a protected block cannot join its fence into nearby prose."""
    source = (
        "Before.\n"
        "```text\n"
        "literal  code\n"
        "```\n"
        "After.\n"
    )
    config = PipelineConfig(
        {
            "cleanup": {
                "excluded_sections": [],
                "remove_front_matter": False,
                "remove_promotional_tail": False,
                "remove_glossary_footnotes": False,
                "remove_publisher_tail": False,
                "remove_footnotes": False,
                "strip_markdown_emphasis": True,
                "report_ocr_noise": False,
            }
        }
    )
    context = ProcessingContext(config)
    context.original_markdown = source
    context.replace_markdown(source)

    DocumentCleanupStage(config).process(context)

    assert (
        "Before.\n```text\nliteral  code\n```\nAfter."
        in context.current_markdown
    )


def test_document_cleanup_preserves_generic_html_blocks_verbatim():
    config = PipelineConfig(
        {
            "cleanup": {
                "excluded_sections": [],
                "remove_front_matter": False,
                "remove_promotional_tail": False,
                "remove_glossary_footnotes": False,
                "remove_publisher_tail": False,
                "remove_footnotes": False,
                "strip_markdown_emphasis": True,
                "report_ocr_noise": False,
            }
        }
    )
    source = "<div>\n__literal__  l0ve\n</div>\n\nStory __bold__ text.\n"
    context = ProcessingContext(config)
    context.original_markdown = source
    context.replace_markdown(source)

    DocumentCleanupStage(config).process(context)

    assert "<div>\n__literal__  l0ve\n</div>" in context.current_markdown
    assert "Story bold text." in context.current_markdown


def test_document_cleanup_removes_emphasized_decorative_heading():
    config = PipelineConfig(
        {
            "cleanup": {
                "excluded_sections": [],
                "remove_front_matter": False,
                "remove_promotional_tail": False,
                "remove_glossary_footnotes": False,
                "remove_publisher_tail": False,
                "remove_footnotes": False,
                "strip_markdown_emphasis": True,
                "report_ocr_noise": False,
            }
        }
    )
    source = "Before.\n\n**## ◆◇◆◇◆?**\n\nAfter.\n"
    context = ProcessingContext(config)
    context.original_markdown = source
    context.replace_markdown(source)

    DocumentCleanupStage(config).process(context)

    assert "◆" not in context.current_markdown
    assert "Before." in context.current_markdown
    assert "After." in context.current_markdown


def test_document_cleanup_removes_inline_and_defined_footnotes():
    config = PipelineConfig(
        {
            "cleanup": {
                "excluded_sections": [],
                "remove_front_matter": False,
                "remove_promotional_tail": False,
                "remove_glossary_footnotes": False,
                "remove_publisher_tail": False,
                "remove_footnotes": True,
                "strip_markdown_emphasis": False,
                "report_ocr_noise": False,
            }
        }
    )
    source = "Story text[^note].\n\n[^note]: Translator note.\n"
    context = ProcessingContext(config)
    context.original_markdown = source
    context.replace_markdown(source)

    DocumentCleanupStage(config).process(context)

    assert "[^note]" not in context.current_markdown
    assert "Translator note" not in context.current_markdown
    assert "Story text." in context.current_markdown


def test_document_cleanup_removes_blockquoted_glossary_note():
    config = PipelineConfig(
        {
            "cleanup": {
                "excluded_sections": [],
                "remove_front_matter": False,
                "remove_promotional_tail": False,
                "remove_glossary_footnotes": True,
                "remove_publisher_tail": False,
                "remove_footnotes": False,
                "strip_markdown_emphasis": False,
                "report_ocr_noise": False,
            }
        }
    )
    source = "Story text.\n\n> 1 **Mage** A practitioner of magic.\n"
    context = ProcessingContext(config)
    context.original_markdown = source
    context.replace_markdown(source)

    DocumentCleanupStage(config).process(context)

    assert "Mage" not in context.current_markdown
    assert "Story text." in context.current_markdown


def test_excluded_section_removes_protected_blocks_until_next_heading():
    config = PipelineConfig(
        {
            "cleanup": {
                "excluded_sections": ["Afterword"],
                "remove_front_matter": False,
                "remove_promotional_tail": False,
                "remove_glossary_footnotes": False,
                "remove_publisher_tail": False,
                "remove_footnotes": False,
                "strip_markdown_emphasis": False,
                "report_ocr_noise": False,
            }
        }
    )
    source = (
        "# Chapter 1\n\nStory.\n\n"
        "# Afterword\n\nRemove this.\n\n"
        "```text\nprotected but excluded\n```\n\n"
        "Remove this too.\n\n"
        "# Appendix\n\nKeep this.\n"
    )
    context = ProcessingContext(config)
    context.original_markdown = source
    context.replace_markdown(source)

    DocumentCleanupStage(config).process(context)

    assert "protected but excluded" not in context.current_markdown
    assert "Remove this" not in context.current_markdown
    assert "# Appendix" in context.current_markdown
    assert "Keep this." in context.current_markdown


def test_local_metadata_removes_protected_blocks_inside_its_section():
    config = PipelineConfig(
        {
            "cleanup": {
                "excluded_sections": [],
                "remove_front_matter": True,
                "remove_promotional_tail": False,
                "remove_glossary_footnotes": False,
                "remove_publisher_tail": False,
                "remove_footnotes": False,
                "strip_markdown_emphasis": False,
                "report_ocr_noise": False,
            }
        }
    )
    source = (
        "Copyright\n\n"
        "```text\npublication metadata\n```\n\n"
        "# Chapter 1\n\nStory.\n"
    )
    context = ProcessingContext(config)
    context.original_markdown = source
    context.replace_markdown(source)

    DocumentCleanupStage(config).process(context)

    assert "publication metadata" not in context.current_markdown
    assert "# Chapter 1" in context.current_markdown
    assert "Story." in context.current_markdown


@pytest.mark.parametrize("chunk_size", [0, -1])
def test_tts_validation_rejects_nonpositive_chunk_size(chunk_size):
    """Invalid bounds fail clearly instead of stalling the chunking loop."""
    with pytest.raises(ValueError, match="greater than zero"):
        TTSValidationStage.validation_chunks("Narration.", chunk_size)


def test_report_options_filter_records_without_mutating_source_log(tmp_path):
    """Typed report options activate dormant export and confidence settings."""
    log = ChangeLog()
    _add_change(log, confidence=99.0)
    _add_change(log, confidence=40.0)
    options = ReportOptions.from_config(
        PipelineConfig(
            {
                "report": {
                    "enabled": True,
                    "export_json": True,
                    "export_summary": True,
                    "include_low_confidence": False,
                }
            }
        )
    )

    paths = ReportExporter(tmp_path, options=options).export(
        cleaned_markdown="Clean.",
        source_file="book.md",
        change_log=log,
    )

    exported = json.loads(paths["changes"].read_text(encoding="utf-8"))
    assert len(exported) == 1
    assert exported[0]["confidence"] == 99.0
    assert "Total audit records: 1" in paths["summary"].read_text(
        encoding="utf-8"
    )
    assert log.total_changes() == 2
    filtered = log.with_minimum_confidence(85.0)
    filtered.records[0].after = "mutated report view"
    assert log.records[0].after == "after"


def test_report_options_reject_ambiguous_string_booleans():
    with pytest.raises(ValueError, match="report.enabled must be true or false"):
        ReportOptions.from_config({"report": {"enabled": "false"}})


def test_disabled_reports_export_only_markdown(tmp_path):
    """Disabling reports avoids creating an otherwise empty report directory."""
    exporter = ReportExporter(
        tmp_path,
        options=ReportOptions(enabled=False),
    )
    paths = exporter.export(
        cleaned_markdown="Clean.",
        source_file="book.md",
        change_log=ChangeLog(),
    )

    assert set(paths) == {"markdown"}
    assert not (tmp_path / "reports").exists()


def test_exporter_refuses_to_overwrite_source_markdown(tmp_path):
    source = tmp_path / "Book - Cleaned.md"
    source.write_text("Original text.", encoding="utf-8")

    with pytest.raises(ValueError, match="Refusing to overwrite"):
        ReportExporter(tmp_path).export(
            cleaned_markdown="Replacement text.",
            source_file=source,
            change_log=ChangeLog(),
        )

    assert source.read_text(encoding="utf-8") == "Original text."


def test_summary_escapes_stage_tables_and_uses_adaptive_fences():
    """Untrusted OCR snippets cannot terminate their own Markdown code fence."""
    log = ChangeLog()
    log.add(
        stage="OCR|Review",
        block_index=0,
        segment_index=0,
        line=1,
        before="text\n```python\nvalue\n```",
        after="replacement",
        confidence=10.0,
        reason="manual review",
        broken_word="bro ken",
    )

    report = SummaryReporter(log).render(
        "book.md",
        generated_at="2026-01-01T00:00:00",
    )

    assert "| OCR\\|Review | 1 |" in report
    assert "````\ntext\n```python" in report
    assert "Broken word: bro ken" in report


def test_backup_metadata_describes_the_copied_bytes(tmp_path, monkeypatch):
    """Metadata stays valid if the source changes immediately after copying."""
    source = tmp_path / "book.md"
    source.write_text("original", encoding="utf-8")
    real_copy2 = shutil.copy2

    def copy_then_change(source_file, destination):
        result = real_copy2(source_file, destination)
        source.write_text("changed after copy", encoding="utf-8")
        return result

    monkeypatch.setattr(backup_module.shutil, "copy2", copy_then_change)
    backup_dir = BackupManager(tmp_path / "backups").create_backup(source)
    metadata = json.loads(
        (backup_dir / "metadata.json").read_text(encoding="utf-8")
    )
    backup_file = backup_dir / source.name
    copied = backup_file.read_bytes()

    assert metadata["sha256"] == hashlib.sha256(copied).hexdigest()
    assert metadata["size"] == len(copied)
