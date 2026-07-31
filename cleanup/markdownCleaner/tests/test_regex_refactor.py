"""Regression tests for deterministic OCR regex corrections."""

from __future__ import annotations

import pytest

from markdownCleaner.modules.core.config import PipelineConfig
from markdownCleaner.modules.core.context import ProcessingContext
from markdownCleaner.modules.markdown.segmenter import MarkdownSegment
from markdownCleaner.modules.regex.broken_words import BrokenWordProcessor
from markdownCleaner.modules.regex.constants import (
    BoundaryCorrection,
    BoundaryEvidence,
)
from markdownCleaner.modules.regex.hyphenation import HyphenationProcessor
from markdownCleaner.modules.regex.number_letter import NumberLetterProcessor
from markdownCleaner.modules.regex.ocr_characters import OCRCharacterProcessor
from markdownCleaner.modules.regex.repeated_characters import (
    RepeatedCharacterProcessor,
)
from markdownCleaner.modules.regex.stage import RegexStage


def _context(corrections: dict | None = None) -> ProcessingContext:
    return ProcessingContext(
        PipelineConfig(
            {
                "regex": {
                    "enabled": True,
                    "corrections": corrections or {},
                }
            }
        )
    )


def _segment(text: str) -> MarkdownSegment:
    return MarkdownSegment(
        text=text,
        line_number=7,
        block_index=2,
        segment_index=3,
    )


@pytest.mark.parametrize(
    ("source", "expected", "changed", "count"),
    [
        ("some one", "someone", True, 1),
        ("every thing", "everything", True, 1),
        ("any body", "anybody", True, 1),
        ("no body", "nobody", True, 1),
        ("no one", "no one", False, 0),
        ("to one", "to one", False, 0),
        ("noone", "no one", True, 1),
        ("toone", "to one", True, 1),
        ("what one", "what one", False, 0),
        ("where body", "where body", False, 0),
        ("some\n\none", "some\n\none", False, 0),
    ],
)
def test_broken_word_rules_are_exact_and_paragraph_safe(
    source: str,
    expected: str,
    changed: bool,
    count: int,
) -> None:
    context = _context()
    segment = _segment(source)

    assert BrokenWordProcessor(context).process(segment) is changed
    assert segment.current_text == expected
    assert context.statistics.get("broken_words_fixed", 0) == count


def test_broken_word_applications_distinguish_join_and_split_evidence() -> None:
    context = _context()
    processor = BrokenWordProcessor(context)
    segment = _segment("some one, toone, every\tthing")

    applications = processor.find_applications(segment.current_text)

    assert [item.broken_word for item in applications] == [
        "some one",
        "toone",
        "every\tthing",
    ]
    assert [item.correction for item in applications] == [
        BoundaryCorrection.JOIN,
        BoundaryCorrection.SPLIT,
        BoundaryCorrection.JOIN,
    ]
    assert [item.evidence for item in applications] == [
        BoundaryEvidence.INSERTED_BOUNDARY,
        BoundaryEvidence.MISSING_BOUNDARY,
        BoundaryEvidence.INSERTED_BOUNDARY,
    ]

    assert processor.process(segment)
    assert segment.current_text == "someone, to one, everything"
    assert context.statistics["broken_words_fixed"] == 3
    assert len(context.tracker.records) == 1
    assert context.tracker.records[0].broken_word == (
        "some one, toone, every\tthing"
    )
    assert context.tracker.records[0].reason == (
        "OCR broken word merge and boundary repair"
    )

    assert not processor.process(segment)
    assert context.statistics["broken_words_fixed"] == 3
    assert len(context.tracker.records) == 1


@pytest.mark.parametrize(
    ("source", "expected", "changed"),
    [
        ("l0ve", "love", True),
        ("1ife", "life", True),
        ("5word", "sword", True),
        ("gr8te", "grbte", True),
        ("R2D2", "R2D2", False),
        ("R2D", "R2D", False),
        ("A10", "A10", False),
        ("Chapter1", "Chapter1", False),
        ("10", "10", False),
        ("3.14", "3.14", False),
    ],
)
def test_number_letter_processor_is_conservative(
    source: str,
    expected: str,
    changed: bool,
) -> None:
    context = _context()
    segment = _segment(source)

    assert NumberLetterProcessor(context).process(segment) is changed
    assert segment.current_text == expected


def test_number_letter_processing_preserves_all_non_token_text() -> None:
    context = _context()
    segment = _segment("l0ve\t  1ife\r\n5word  gr8te")

    assert NumberLetterProcessor(context).process(segment)
    assert segment.current_text == "love\t  life\r\nsword  grbte"
    assert context.statistics["number_letter_fixed"] == 4


def test_number_letter_log_counter_and_idempotence_are_exact() -> None:
    context = _context()
    processor = NumberLetterProcessor(context)
    segment = _segment("l0ve and 1ife; keep R2D2 and 10")

    assert processor.process(segment)
    assert context.statistics["number_letter_fixed"] == 2
    assert len(context.tracker.records) == 1
    assert context.tracker.records[0].confidence == pytest.approx(82.5)

    assert not processor.process(segment)
    assert context.statistics["number_letter_fixed"] == 2
    assert len(context.tracker.records) == 1


def test_digit_switches_control_individual_rules() -> None:
    context = _context(
        {
            "zero_to_o": {"enabled": True},
            "one_to_l": {"enabled": False},
            "five_to_s": {"enabled": False},
            "eight_to_b": {"enabled": False},
        }
    )
    segment = _segment("l0ve 1ife 5word gr8te")

    assert NumberLetterProcessor(context).process(segment)
    assert segment.current_text == "love 1ife 5word gr8te"
    assert context.statistics["number_letter_fixed"] == 1


def test_processor_switches_work_for_direct_callers() -> None:
    corrections = {
        "broken_words": {"enabled": False},
        "broken_hyphen_words": {"enabled": False},
        "repeated_characters": {"enabled": False},
    }
    context = _context(corrections)

    broken = _segment("some one")
    hyphenated = _segment("inter-\nnational")
    repeated = _segment("aaaa")

    assert not BrokenWordProcessor(context).process(broken)
    assert not HyphenationProcessor(context).process(hyphenated)
    assert not RepeatedCharacterProcessor(context).process(repeated)
    assert broken.current_text == "some one"
    assert hyphenated.current_text == "inter-\nnational"
    assert repeated.current_text == "aaaa"
    assert not context.tracker.records


def test_repeated_character_processor_preserves_roman_numerals() -> None:
    context = _context()
    segment = _segment("Ivsaar III met Chapter XXX under King VIII.")

    assert not RepeatedCharacterProcessor(context).process(segment)
    assert segment.current_text == "Ivsaar III met Chapter XXX under King VIII."
    assert context.statistics.get("repeated_characters_fixed", 0) == 0
    assert not context.tracker.records


def test_repeated_character_processor_honors_custom_protected_tokens(
    tmp_path,
) -> None:
    glossary = tmp_path / "custom_words.json"
    glossary.write_text(
        '["Ivsaar III", "QQQ"]\n',
        encoding="utf-8",
    )
    context = ProcessingContext(
        PipelineConfig(
            {
                "regex": {
                    "enabled": True,
                    "corrections": {
                        "repeated_characters": {"enabled": True},
                    },
                },
                "symspell": {"glossary": str(glossary)},
            }
        )
    )
    segment = _segment("Ivsaar III uses QQQ, while AAA is OCR noise.")

    assert RepeatedCharacterProcessor(context).process(segment)
    assert segment.current_text == (
        "Ivsaar III uses QQQ, while A is OCR noise."
    )
    assert context.statistics["repeated_characters_fixed"] == 1
    assert len(context.tracker.records) == 1


def test_regex_stage_registers_number_letter_correction_exactly_once() -> None:
    context = _context()
    stage = RegexStage(context.config)

    stage.initialize(context)

    assert sum(
        type(processor) is NumberLetterProcessor
        for processor in stage.processors
    ) == 1
    assert not any(
        type(processor) is OCRCharacterProcessor
        for processor in stage.processors
    )


def test_regex_stage_omits_fully_disabled_processors() -> None:
    disabled = {
        key: {"enabled": False}
        for key in (
            "zero_to_o",
            "one_to_l",
            "five_to_s",
            "eight_to_b",
            "broken_words",
            "broken_hyphen_words",
            "repeated_characters",
        )
    }
    context = _context(disabled)
    stage = RegexStage(context.config)

    stage.initialize(context)

    assert stage.processors == []
