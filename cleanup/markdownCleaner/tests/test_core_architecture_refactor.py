"""Regression tests for core pipeline architecture contracts."""

from __future__ import annotations

import logging

import pytest
import yaml

from markdownCleaner.modules.core.config import PipelineConfig
from markdownCleaner.modules.core.context import ProcessingContext
from markdownCleaner.modules.core.logger import get_logger
from markdownCleaner.modules.core.processor import SegmentProcessor
from markdownCleaner.modules.core.stage import (
    PipelineStage,
    SegmentProcessingStage,
    StageResult,
)
from markdownCleaner.modules.markdown.markdown import (
    BlockType,
    MarkdownBlock,
    MarkdownParser,
)
from markdownCleaner.modules.markdown.segmenter import MarkdownSegment
from markdownCleaner.modules.regex.stage import RegexStage
from markdownCleaner.modules.symspell import broken_words as broken_words_module
from markdownCleaner.modules.symspell.stage import SymSpellStage
from markdownCleaner.pipeline import OCRPipeline


class _ExceptionStage(PipelineStage):
    name = "ExceptionStage"

    def process(self, context: ProcessingContext) -> StageResult:
        _mutate_all_stage_state(context)
        raise RuntimeError("contained failure")


class _UnsuccessfulStage(PipelineStage):
    name = "UnsuccessfulStage"

    def process(self, context: ProcessingContext) -> StageResult:
        _mutate_all_stage_state(context)
        return StageResult(
            stage=self.name,
            changes=1,
            success=False,
            error="rejected result",
        )


class _WholeDocumentExceptionStage(PipelineStage):
    name = "WholeDocumentExceptionStage"

    def process(self, context: ProcessingContext) -> StageResult:
        context.replace_markdown("# Partial replacement\n")
        context.metadata["partial_metadata"] = True
        raise RuntimeError("contained whole-document failure")


class _ExistingRecordMutationStage(PipelineStage):
    name = "ExistingRecordMutationStage"

    def process(self, context: ProcessingContext) -> StageResult:
        context.tracker.records[0].after = "leaked mutation"
        raise RuntimeError("contained record mutation")


class _NoopStage(PipelineStage):
    name = "NoopStage"

    def process(self, context: ProcessingContext) -> StageResult:
        return StageResult(stage=self.name)


class _TestProcessor(SegmentProcessor):
    name = "TestProcessor"

    def process(self, segment: MarkdownSegment) -> bool:
        return False


class _EmptyProcessorStage(SegmentProcessingStage):
    name = "EmptyProcessorStage"

    def __init__(self, config: PipelineConfig) -> None:
        super().__init__(config)
        self.build_count = 0

    def build_processors(self, context: ProcessingContext):
        self.build_count += 1
        return []


def _mutate_all_stage_state(context: ProcessingContext) -> None:
    context.segments[0].current_text = "partial text"
    context.tracker.add(
        stage="partial",
        block_index=0,
        segment_index=0,
        line=1,
        before="original text",
        after="partial text",
        confidence=1.0,
        reason="must roll back",
    )
    context.increment("partial_counter")
    context.metadata["partial_metadata"] = {"nested": ["value"]}


@pytest.mark.parametrize(
    "markdown",
    [
        "",
        "text",
        "text\n",
        "text\n\n",
        "\n",
        "# Heading\n\nParagraph.\n",
    ],
)
def test_markdown_round_trip_preserves_final_newline_state(markdown: str) -> None:
    assert MarkdownParser().parse(markdown).to_markdown() == markdown


def test_fenced_code_ignores_marker_with_info_text_as_a_close() -> None:
    markdown = (
        "```\n"
        "literal one\n"
        "```python\n"
        "literal two\n"
        "```\n\n"
        "Narrative.\n"
    )

    document = MarkdownParser().parse(markdown)

    assert document.blocks[0].block_type is BlockType.CODE_FENCE
    assert document.blocks[0].end_line == 5
    assert document.blocks[2].block_type is BlockType.PARAGRAPH
    assert document.to_markdown() == markdown


def test_gfm_table_without_outer_pipes_is_protected() -> None:
    markdown = (
        "Name | Value\n"
        "--- | ---\n"
        "l0ve | atten tion\n\n"
        "Narrative.\n"
    )

    document = MarkdownParser().parse(markdown)

    assert document.blocks[0].block_type is BlockType.TABLE
    assert not document.blocks[0].editable
    assert document.blocks[2].block_type is BlockType.PARAGRAPH
    assert document.to_markdown() == markdown


def test_context_strips_utf8_bom_before_markdown_classification(tmp_path) -> None:
    source = tmp_path / "bom.md"
    source.write_bytes(
        b"\xef\xbb\xbf# Chapter 1\n\nNarrative.\n"
    )
    context = ProcessingContext(PipelineConfig())

    context.load_markdown(source)

    assert not context.original_markdown.startswith("\ufeff")
    assert context.document.blocks[0].block_type is BlockType.HEADING
    assert context.segments[0].current_text == "Narrative."


def test_markdown_block_copy_preserves_current_text_independently() -> None:
    block = MarkdownBlock(
        BlockType.PARAGRAPH,
        "original",
        2,
        2,
        metadata={"source": "ocr"},
    )
    block.update("cleaned")

    copied = block.copy()
    copied.update("changed again")
    copied.metadata["source"] = "copy"

    assert block.current_text == "cleaned"
    assert copied.current_text == "changed again"
    assert block.metadata == {"source": "ocr"}


@pytest.mark.parametrize(
    "opening",
    [
        "<br>",
        '<img src="cover.jpg">',
        '<meta name="author" content="Example">',
        "<custom-widget />",
        "<!-- converter note -->",
        "<https://example.com/l0ve>",
    ],
)
def test_single_line_or_unclosed_html_does_not_protect_document_tail(
    opening: str,
) -> None:
    markdown = f"{opening}\nNarrative text remains editable.\n"
    document = MarkdownParser().parse(markdown)

    assert document.blocks[0].block_type is BlockType.HTML
    assert document.blocks[0].end_line == 1
    assert document.blocks[1].block_type is BlockType.PARAGRAPH
    assert document.blocks[1].editable
    assert document.to_markdown() == markdown


def test_closed_multiline_html_and_comments_remain_bounded_blocks() -> None:
    markdown = (
        "<div>\nProtected HTML.\n</div>\n"
        "<!--\nProtected comment.\n-->\n"
        "Narrative text.\n"
    )
    document = MarkdownParser().parse(markdown)

    assert [block.block_type for block in document.blocks] == [
        BlockType.HTML,
        BlockType.HTML,
        BlockType.PARAGRAPH,
    ]
    assert document.blocks[0].end_line == 3
    assert document.blocks[1].end_line == 6
    assert document.to_markdown() == markdown


def test_unclosed_block_html_is_protected_only_to_blank_line_boundary() -> None:
    markdown = (
        "<div>\n"
        "l0ve inside unclosed HTML remains literal.\n\n"
        "Narrative text remains editable.\n"
    )
    document = MarkdownParser().parse(markdown)

    assert [block.block_type for block in document.blocks] == [
        BlockType.HTML,
        BlockType.BLANK,
        BlockType.PARAGRAPH,
    ]
    assert document.blocks[0].end_line == 2
    assert document.blocks[2].start_line == 4
    assert document.to_markdown() == markdown


def test_leading_horizontal_rule_without_yaml_close_keeps_prose_editable() -> None:
    markdown = "---\nNarrative text.\n"
    document = MarkdownParser().parse(markdown)

    assert [block.block_type for block in document.blocks] == [
        BlockType.HORIZONTAL_RULE,
        BlockType.PARAGRAPH,
    ]
    assert document.blocks[1].editable
    assert document.to_markdown() == markdown


def test_horizontal_rules_around_prose_are_not_mistaken_for_frontmatter() -> None:
    markdown = "---\nNarrative text.\n\n---\nMore narrative.\n"
    document = MarkdownParser().parse(markdown)

    assert document.blocks[0].block_type is BlockType.HORIZONTAL_RULE
    assert document.blocks[1].block_type is BlockType.PARAGRAPH
    assert document.blocks[3].block_type is BlockType.HORIZONTAL_RULE
    assert document.to_markdown() == markdown


def test_markdown_block_copy_preserves_an_empty_edit() -> None:
    block = MarkdownBlock(BlockType.PARAGRAPH, "delete me", 1, 1)
    block.update("")

    copied = block.copy()

    assert copied.current_text == ""
    assert copied.content == ""


@pytest.mark.parametrize(
    "stage_type",
    [_ExceptionStage, _UnsuccessfulStage, _WholeDocumentExceptionStage],
)
def test_unsuccessful_stage_rolls_back_all_shared_mutations(
    tmp_path,
    stage_type: type[PipelineStage],
) -> None:
    source = tmp_path / "source.md"
    source.write_text("original text\n", encoding="utf-8")
    context = ProcessingContext(PipelineConfig())
    context.load_markdown(source)

    result = stage_type(context.config).execute(context)

    assert not result.success
    assert result.changes == 0
    assert context.get_markdown() == "original text\n"
    assert context.tracker.records == []
    assert "partial_counter" not in context.statistics
    assert "partial_metadata" not in context.metadata
    assert context.statistics["stages"][stage_type.name] == 0

    # A later success must not publish an earlier failed stage's segment edit.
    assert _NoopStage(context.config).execute(context).success
    assert context.get_markdown() == "original text\n"


def test_failed_stage_rolls_back_mutation_of_an_existing_change_record(
    tmp_path,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("original text\n", encoding="utf-8")
    context = ProcessingContext(PipelineConfig())
    context.load_markdown(source)
    context.tracker.add(
        stage="EarlierStage",
        block_index=0,
        segment_index=0,
        line=1,
        before="original",
        after="cleaned",
        confidence=100.0,
        reason="existing record",
    )

    result = _ExistingRecordMutationStage(context.config).execute(context)

    assert not result.success
    assert context.tracker.records[0].after == "cleaned"


def test_shared_segment_processor_records_optional_broken_word() -> None:
    context = ProcessingContext(PipelineConfig())
    processor = _TestProcessor(context)
    segment = MarkdownSegment(
        text="atten tion",
        line_number=7,
        block_index=2,
        segment_index=3,
    )

    processor.record_change(
        segment=segment,
        before="atten tion",
        after="attention",
        reason="dictionary evidence",
        broken_word="atten tion",
    )
    processor.record_change(
        segment=segment,
        before="same",
        after="same",
        reason="no-op",
    )

    assert len(context.tracker.records) == 1
    record = context.tracker.records[0]
    assert record.stage == "TestProcessor"
    assert record.line == 7
    assert record.confidence == 100.0
    assert record.broken_word == "atten tion"


def test_segment_stage_initializes_a_legitimate_empty_processor_list_once(
    tmp_path,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("Text.\n", encoding="utf-8")
    context = ProcessingContext(PipelineConfig())
    context.load_markdown(source)
    stage = _EmptyProcessorStage(context.config)

    assert stage.execute(context).success
    assert stage.build_count == 1


def test_regex_stage_preserves_inline_code_and_link_destinations(tmp_path) -> None:
    source = tmp_path / "source.md"
    source.write_text(
        "l0ve `l0ve` [l0ve](https://example.com/l0ve) "
        "[nested](https://example.com/l0ve(foo)/l0ve) "
        "<https://example.com/l0ve>\n",
        encoding="utf-8",
    )
    config = PipelineConfig(
        {
            "regex": {
                "enabled": True,
                "corrections": {
                    "zero_to_o": {"enabled": True},
                    "one_to_l": {"enabled": False},
                    "five_to_s": {"enabled": False},
                    "eight_to_b": {"enabled": False},
                    "broken_words": {"enabled": False},
                    "broken_hyphen_words": {"enabled": False},
                    "repeated_characters": {"enabled": False},
                },
            }
        }
    )
    context = ProcessingContext(config)
    context.load_markdown(source)

    result = RegexStage(config).execute(context)

    assert result.success
    assert context.get_markdown() == (
        "love `l0ve` [love](https://example.com/l0ve) "
        "[nested](https://example.com/l0ve(foo)/l0ve) "
        "<https://example.com/l0ve>\n"
    )


def test_regex_stage_preserves_reference_link_identifiers(tmp_path) -> None:
    source = tmp_path / "source.md"
    source.write_text(
        "[l0ve label][l0ve-id], [l0ve], ![c0ver] and [l0ve][] "
        "plus [^n0te] and l0ve.\n\n"
        "[l0ve-id]: https://example.com\n"
        "[l0ve]: https://example.com/collapsed\n",
        encoding="utf-8",
    )
    config = PipelineConfig(
        {
            "regex": {
                "enabled": True,
                "corrections": {
                    "zero_to_o": {"enabled": True},
                    "one_to_l": {"enabled": False},
                    "five_to_s": {"enabled": False},
                    "eight_to_b": {"enabled": False},
                    "broken_words": {"enabled": False},
                    "broken_hyphen_words": {"enabled": False},
                    "repeated_characters": {"enabled": False},
                },
            }
        }
    )
    context = ProcessingContext(config)
    context.load_markdown(source)

    assert RegexStage(config).execute(context).success
    assert context.get_markdown() == (
        "[love label][l0ve-id], [l0ve], ![c0ver] and [l0ve][] "
        "plus [^n0te] and love.\n\n"
        "[l0ve-id]: https://example.com\n"
        "[l0ve]: https://example.com/collapsed\n"
    )


def test_symspell_stage_only_corrects_editable_markdown_spans(tmp_path) -> None:
    dictionary = tmp_path / "frequency.txt"
    dictionary.write_text("because 10000000\n", encoding="utf-8")
    source = tmp_path / "source.md"
    source.write_text(
        "becuse `becuse` [becuse](https://example.com/becuse) "
        '<span data-word="becuse">becuse</span>\n',
        encoding="utf-8",
    )
    config = PipelineConfig(
        {
            "symspell": {
                "enabled": True,
                "dictionary": str(dictionary),
                "max_edit_distance": 1,
                "max_auto_edit_distance": 1,
                "confidence_threshold": 92,
                "minimum_word_length": 4,
                "minimum_candidate_frequency": 1000,
                "auto_protect_proper_nouns": False,
                "wordfreq_enabled": False,
            }
        }
    )
    context = ProcessingContext(config)
    context.load_markdown(source)

    result = SymSpellStage(config).execute(context)

    assert result.success
    assert context.get_markdown() == (
        "because `becuse` [because](https://example.com/becuse) "
        '<span data-word="becuse">because</span>\n'
    )


def test_cross_block_merge_rejects_shared_protected_edge_ranges(
    tmp_path,
    monkeypatch,
) -> None:
    dictionary = tmp_path / "frequency.txt"
    dictionary.write_text("energy 50000000\n", encoding="utf-8")
    source = tmp_path / "source.md"
    source.write_text("ener \n\ngy remained.\n", encoding="utf-8")
    config = PipelineConfig(
        {
            "symspell": {
                "enabled": True,
                "dictionary": str(dictionary),
                "confidence_threshold": 101,
                "broken_word_merge_minimum_frequency": 50000,
                "auto_protect_proper_nouns": False,
                "wordfreq_enabled": False,
            }
        }
    )
    context = ProcessingContext(config)
    context.load_markdown(source)
    monkeypatch.setattr(
        broken_words_module,
        "protected_span_ranges",
        lambda text: ((0, len(text)),),
    )

    result = SymSpellStage(config).execute(context)

    assert result.success
    assert context.get_markdown() == "ener \n\ngy remained.\n"
    assert not context.tracker.records


def test_config_rejects_malformed_sections_and_repairs_nested_set() -> None:
    malformed = PipelineConfig({"paths": "output", "backup": {}})
    with pytest.raises(ValueError, match="must be mappings"):
        malformed.validate()

    malformed.set("paths.output_directory", "cleaned")
    assert malformed.section("paths") == {"output_directory": "cleaned"}

    malformed_optional = PipelineConfig(
        {
            "paths": {"output_directory": "cleaned"},
            "backup": {"enabled": False},
            "cleanup": "false",
        }
    )
    with pytest.raises(ValueError, match="cleanup"):
        malformed_optional.validate()


def test_config_rejects_string_boolean_values() -> None:
    backup = PipelineConfig(
        {
            "paths": {"output_directory": "cleaned"},
            "backup": {"enabled": "false"},
        }
    )
    with pytest.raises(ValueError, match="backup.enabled must be true or false"):
        backup.validate()

    cleanup = PipelineConfig(
        {
            "paths": {"output_directory": "cleaned"},
            "backup": {"enabled": False},
            "cleanup": {"remove_footnotes": "false"},
        }
    )
    with pytest.raises(
        ValueError,
        match="cleanup.remove_footnotes must be true or false",
    ):
        cleanup.validate()


@pytest.mark.parametrize("output_directory", [None, "", "   ", 42])
def test_config_rejects_invalid_output_directory(output_directory) -> None:
    config = PipelineConfig(
        {
            "paths": {"output_directory": output_directory},
            "backup": {"enabled": False},
        }
    )

    with pytest.raises(ValueError, match="paths.output_directory"):
        config.validate()


def test_config_requires_backup_directory_only_when_enabled() -> None:
    enabled = PipelineConfig(
        {
            "paths": {"output_directory": "output"},
            "backup": {"enabled": True, "directory": None},
        }
    )
    disabled = PipelineConfig(
        {
            "paths": {"output_directory": "output"},
            "backup": {"enabled": False},
        }
    )

    with pytest.raises(ValueError, match="backup.directory"):
        enabled.validate()
    assert disabled.validate()


def test_pipeline_reconfigures_log_file_and_named_level(tmp_path) -> None:
    first_log = tmp_path / "relative-logs" / "first.log"
    second_log = tmp_path / "logs" / "second.log"

    def write_config(path, log_file, level, directory):
        path.write_text(
            yaml.safe_dump(
                {
                    "paths": {"output_directory": str(tmp_path / "output")},
                    "backup": {"enabled": False},
                    "logging": {
                        "directory": directory,
                        "file": str(log_file),
                        "level": level,
                    },
                }
            ),
            encoding="utf-8",
        )

    first_config = tmp_path / "first.yaml"
    second_config = tmp_path / "second.yaml"
    write_config(first_config, "first.log", "WARNING", "relative-logs")
    write_config(
        second_config,
        second_log,
        "DEBUG",
        str(tmp_path / "logs"),
    )

    OCRPipeline(first_config)
    assert get_logger().level == logging.WARNING
    assert first_log.resolve() in {
        type(first_log)(handler.baseFilename).resolve()
        for handler in get_logger().handlers
        if isinstance(handler, logging.FileHandler)
    }

    OCRPipeline(second_config)
    file_targets = {
        type(second_log)(handler.baseFilename).resolve()
        for handler in get_logger().handlers
        if isinstance(handler, logging.FileHandler)
    }
    assert get_logger().level == logging.DEBUG
    assert second_log.resolve() in file_targets
    assert first_log.resolve() not in file_targets


def test_pipeline_resolves_configured_output_and_backup_beside_config(
    tmp_path,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("Narrative.\n", encoding="utf-8")
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "paths": {"output_directory": "relative-output"},
                "backup": {
                    "enabled": True,
                    "directory": "relative-backup",
                },
                "cleanup": {"enabled": False},
                "unicode": {"enabled": False},
                "regex": {"enabled": False},
                "vocabulary_candidates": {"enabled": False},
                "symspell": {"enabled": False},
                "tts_validation": {"enabled": False},
                "logging": {
                    "directory": "relative-logs",
                    "file": "pipeline.log",
                },
            }
        ),
        encoding="utf-8",
    )

    result = OCRPipeline(config_path).run(source)

    assert result["output"]["markdown"].parent == tmp_path / "relative-output"
    assert result["backup"].parent == tmp_path / "relative-backup"
