"""Behavior tests for important pipeline paths not covered by regressions."""

from __future__ import annotations

import json
from pathlib import Path

from markdownCleaner.modules.core.config import PipelineConfig
from markdownCleaner.modules.core.context import ProcessingContext
from markdownCleaner.modules.markdown.markdown import BlockType, MarkdownParser
from markdownCleaner.modules.markdown.segmenter import MarkdownSegment
from markdownCleaner.modules.regex.broken_words import BrokenWordProcessor
from markdownCleaner.modules.regex.hyphenation import HyphenationProcessor
from markdownCleaner.modules.regex.number_letter import NumberLetterProcessor
from markdownCleaner.modules.regex.ocr_characters import OCRCharacterProcessor
from markdownCleaner.modules.regex.repeated_characters import RepeatedCharacterProcessor
from markdownCleaner.modules.report.change_log import ChangeLog
from markdownCleaner.modules.report.summary import SummaryReporter
from markdownCleaner.modules.unicode.invisible import InvisibleProcessor
from markdownCleaner.modules.unicode.ligatures import LigatureProcessor
from markdownCleaner.modules.unicode.normalizer import UnicodeNormalizer
from markdownCleaner.modules.unicode.punctuation import PunctuationProcessor
from markdownCleaner.modules.unicode.whitespace import WhitespaceProcessor
from markdownCleaner.pipeline import OCRPipeline


def _context() -> ProcessingContext:
    """Return a fully enabled in-memory processor context."""
    return ProcessingContext(
        PipelineConfig(
            {
                "unicode": {"enabled": True},
                "regex": {"enabled": True},
            }
        )
    )


def _segment(text: str) -> MarkdownSegment:
    """Create a located editable segment for processor tests."""
    return MarkdownSegment(text=text, line_number=3, block_index=1, segment_index=2)


def test_unicode_processors_cover_changes_and_noops():
    """Exercise normalization, invisible, ligature, whitespace, and punctuation paths."""
    context = _context()

    normal = _segment("Cafe\u0301")
    assert UnicodeNormalizer(context).process(normal)
    assert normal.current_text == "Café"
    assert not UnicodeNormalizer(context).process(_segment("Already normalized"))

    invisible = _segment("A\u200bB\x07\nC")
    processor = InvisibleProcessor(context)
    assert processor.process(invisible)
    assert invisible.current_text == "AB\nC"
    assert not processor.process(_segment("Visible"))

    ligature = _segment("The \ufb01re and o\ufb03cer")
    ligatures = LigatureProcessor(context)
    assert ligatures.process(ligature)
    assert ligature.current_text == "The fire and officer"
    assert ligatures._count_changes("\ufb01\ufb02", "fifl") == 2

    spacing = _segment("A\u00a0  line \t \r\nnext\u00ad")
    whitespace = WhitespaceProcessor(context)
    assert whitespace.process(spacing)
    assert "\r" not in spacing.current_text
    assert "\u00ad" not in spacing.current_text
    assert whitespace._normalize_line_endings("a\r\nb\rc") == "a\nb\nc"
    assert whitespace._remove_trailing_spaces("a  \n") == "a\n"
    assert whitespace._collapse_spaces("a   b") == "a b"

    punctuation = _segment("“Hello”—don’t wait…")
    assert PunctuationProcessor(context).process(punctuation)
    assert punctuation.current_text == '"Hello"-don\'t wait...'
    assert not PunctuationProcessor(context).process(_segment("Plain text."))


def test_regex_processors_cover_corrections_and_helpers():
    """Exercise every deterministic OCR processor and its no-change path."""
    context = _context()

    broken = _segment("some one saw every thing")
    assert BrokenWordProcessor(context).process(broken)
    assert "someone" in broken.current_text and "everything" in broken.current_text
    assert not BrokenWordProcessor(context).process(_segment("ordinary prose"))

    hyphenated = _segment("inter-\nnational")
    hyphenation = HyphenationProcessor(context)
    assert hyphenation.process(hyphenated)
    assert hyphenated.current_text == "international"
    assert not hyphenation.process(_segment("well-known"))

    repeated = _segment("aaaa bbbb")
    repeats = RepeatedCharacterProcessor(context)
    assert repeats.process(repeated)
    assert repeated.current_text == "a b"
    assert not repeats.process(_segment("hello book"))

    confused = _segment("l0ve 1ife 5word gr8te")
    characters = OCRCharacterProcessor(context)
    assert characters.process(confused)
    assert "love" in confused.current_text and "grbte" in confused.current_text
    assert not characters.process(_segment("normal words"))

    numbered = _segment("l0ve R2D2 10")
    number_letters = NumberLetterProcessor(context)
    assert number_letters.process(numbered)
    assert numbered.current_text.startswith("love")
    assert number_letters._contains_letters_and_digits("R2D2")
    assert not number_letters._contains_letters_and_digits("word")
    assert number_letters._fix_word("plain") == "plain"


def test_markdown_parser_covers_protected_and_editable_block_types():
    """Parse representative Markdown syntax and preserve it on reconstruction."""
    markdown = """---
title: Example
---
# Chapter 1

```python
print("safe")
```

<div>
HTML
</div>

| Name | Value |
|---|---|
| A | 1 |

> quotation

- item

---

![cover](cover.jpg)

[site](https://example.com)

[^1]: note

Narrative paragraph.
"""
    document = MarkdownParser().parse(markdown)
    kinds = {block.block_type for block in document}

    assert {
        BlockType.YAML_FRONTMATTER,
        BlockType.HEADING,
        BlockType.CODE_FENCE,
        BlockType.HTML,
        BlockType.TABLE,
        BlockType.BLOCKQUOTE,
        BlockType.LIST,
        BlockType.HORIZONTAL_RULE,
        BlockType.IMAGE,
        BlockType.LINK,
        BlockType.FOOTNOTE,
        BlockType.PARAGRAPH,
    } <= kinds
    assert document.to_markdown() == document.rebuild()
    assert document.statistics()["PARAGRAPH"] >= 1
    assert list(document.editable_blocks())
    assert list(document.protected_blocks())
    copied = document.blocks[0].copy()
    assert copied is not document.blocks[0]


def test_change_log_filters_and_summary_export(tmp_path):
    """Cover confidence filtering plus JSON and Markdown report generation."""
    log = ChangeLog()
    for confidence, before, after in [(99.0, "teh", "the"), (70.0, "name", "name")]:
        log.add(
            stage="RegexOCR",
            block_index=0,
            segment_index=0,
            line=1,
            before=before,
            after=after,
            confidence=confidence,
            reason="test record",
        )

    assert log.total_changes() == 2
    assert len(log.high_confidence(90)) == 1
    assert len(log.needs_review(90)) == 1
    json_path = tmp_path / "changes.json"
    log.export_json(json_path)
    assert len(json.loads(json_path.read_text(encoding="utf-8"))) == 2

    summary = SummaryReporter(log, review_threshold=90).generate(
        tmp_path / "summary.md", "book.md"
    )
    report = summary.read_text(encoding="utf-8")
    assert "Total corrections: 2" in report
    assert "Review Required" in report
    assert "book.md" in report


def test_full_pipeline_creates_backup_clean_output_and_reports(tmp_path):
    """Run the real pipeline through backup, stages, export, and reporting."""
    source = tmp_path / "OCR_Book_[Release].md"
    source.write_text(
        "# Chapter 1\n\nThe \ufb01re\u200b  burned… “Wait”—some one said. helllo l0ve.\n",
        encoding="utf-8",
    )
    dictionary = tmp_path / "dictionary.txt"
    dictionary.write_text(
        "the 100000\nfire 90000\nburned 80000\nwait 70000\nsomeone 60000\nsaid 50000\nhello 40000\nlove 30000\n",
        encoding="utf-8",
    )
    output = tmp_path / "output"
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""paths:
  output_directory: "{output.as_posix()}"
backup:
  enabled: true
  directory: "{(tmp_path / 'backup').as_posix()}"
cleanup:
  enabled: true
unicode:
  enabled: true
regex:
  enabled: true
symspell:
  enabled: false
  dictionary: "{dictionary.as_posix()}"
vocabulary_candidates:
  enabled: false
tts_validation:
  enabled: true
  report_limit: 10
logging:
  directory: "{(tmp_path / 'logs').as_posix()}"
""",
        encoding="utf-8",
    )

    result = OCRPipeline(config).run(source)

    assert result["backup"] is not None
    assert Path(result["backup"]).exists()
    assert all(stage.success for stage in result["stages"])
    output_paths = result["output"]
    assert Path(output_paths["markdown"]).exists()
    assert Path(output_paths["changes"]).exists()
    assert Path(output_paths["summary"]).exists()
    assert Path(output_paths["glossary_candidates"]).exists()
    cleaned = Path(output_paths["markdown"]).read_text(encoding="utf-8")
    assert "ﬁ" not in cleaned and "\u200b" not in cleaned
