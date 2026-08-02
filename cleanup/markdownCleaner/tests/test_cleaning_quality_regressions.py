"""Quality gates for conservative, context-aware Markdown cleaning."""

from __future__ import annotations

import json
from pathlib import Path

from markdownCleaner.modules.cleanup.page_artifacts import find_page_artifacts
from markdownCleaner.modules.cleanup.paragraphs import reconstruct_paragraphs
from markdownCleaner.modules.core.config import PipelineConfig
from markdownCleaner.modules.core.context import ProcessingContext
from markdownCleaner.modules.core.stage import PipelineStage, StageResult
from markdownCleaner.modules.markdown.markdown import BlockType
from markdownCleaner.modules.symspell.broken_words import (
    BrokenWordEvaluator,
    BrokenWordMerger,
)
from markdownCleaner.modules.symspell.decisions import BrokenWordDecisions
from markdownCleaner.modules.symspell.dictionary import DictionaryManager
from markdownCleaner.modules.symspell.frequency import WordfreqScorer
from markdownCleaner.modules.symspell.settings import SymSpellSettings
from markdownCleaner.modules.symspell.contextual import ContextualRealWordStage
from markdownCleaner.modules.regex.stage import RegexStage
from markdownCleaner.modules.unicode.constants import PUNCTUATION_TRANSLATION
from markdownCleaner.modules.unicode.mojibake import repair_mojibake


FIXTURES = Path(__file__).parent / "fixtures" / "real_book_regressions.json"


def _merger(scores: dict[str, float]) -> BrokenWordMerger:
    dictionary = DictionaryManager()
    settings = SymSpellSettings(
        broken_word_merge_minimum_frequency=50_000,
        wordfreq_minimum_zipf=2.5,
        dehyphenation_zipf_margin=0.5,
    )
    scorer = WordfreqScorer(
        lookup=lambda word, language, wordlist: scores.get(word.casefold(), 0.0)
    )
    return BrokenWordMerger(
        BrokenWordEvaluator(dictionary, scorer, settings), settings
    )


def test_labeled_real_book_regression_corpus_is_machine_checked() -> None:
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert len(cases) >= 5
    assert all({"id", "source", "label", "input", "expected"} <= case.keys() for case in cases)

    scores = {
        "self-control": 4.5,
        "selfcontrol": 2.0,
        "hand-picked": 4.0,
        "handpicked": 3.0,
        "international": 5.4,
        "inter-national": 0.0,
        "petrification": 3.4,
    }
    merger = _merger(scores)
    for case in cases:
        if case["label"] in {
            "genuine_hyphenated_compound",
            "artificial_line_hyphenation",
        }:
            reconstructed = reconstruct_paragraphs(
                case["input"],
                dehyphenate=False,
                preserve_hyphen_line_breaks=True,
            )
            actual = merger.merge_line_hyphenations(reconstructed).text
        elif case["label"] == "semantic_punctuation":
            actual = case["input"].translate(PUNCTUATION_TRANSLATION)
        elif case["label"] == "broken_word_space":
            actual = merger.merge_inline(case["input"]).text
        else:
            raise AssertionError(f"Unhandled corpus label: {case['label']}")
        assert actual == case["expected"], case["id"]


def test_reviewed_broken_word_decisions_override_heuristics(tmp_path) -> None:
    path = tmp_path / "decisions.json"
    path.write_text(
        json.dumps({"accepted": {"Ley win": "Leywin"}, "rejected": ["to one"]}),
        encoding="utf-8",
    )
    decisions = BrokenWordDecisions.load(path)
    dictionary = DictionaryManager()
    settings = SymSpellSettings()
    evaluator = BrokenWordEvaluator(
        dictionary,
        WordfreqScorer(enabled=False),
        settings,
        decisions,
    )

    assert evaluator.evaluate("Ley", "win").replacement == "Leywin"
    assert evaluator.evaluate("to", "one") is None


class _LowConfidenceStage(PipelineStage):
    name = "LowConfidence"

    def process(self, context) -> StageResult:
        before = context.current_markdown
        context.replace_markdown("changed\n")
        context.tracker.add(
            stage=self.name,
            block_index=0,
            segment_index=0,
            line=1,
            before=before,
            after="changed\n",
            confidence=75,
            reason="test proposal",
        )
        return StageResult(stage=self.name, changes=1)


def test_global_mutation_threshold_suppresses_but_reports(tmp_path) -> None:
    source = tmp_path / "source.md"
    source.write_text("original\n", encoding="utf-8")
    config = PipelineConfig({"mutation": {"minimum_confidence": 90, "report_only": False}})
    context = ProcessingContext(config)
    context.load_markdown(source)

    result = _LowConfidenceStage(config).execute(context)

    assert result.success and result.changes == 0
    assert context.get_markdown() == "original\n"
    assert context.tracker.records[-1].after == "changed\n"
    assert not context.tracker.records[-1].applied


def test_global_report_only_mode_suppresses_even_high_confidence(tmp_path) -> None:
    source = tmp_path / "source.md"
    source.write_text("original\n", encoding="utf-8")
    config = PipelineConfig(
        {"mutation": {"minimum_confidence": 0, "report_only": True}}
    )
    context = ProcessingContext(config)
    context.load_markdown(source)

    _LowConfidenceStage(config).execute(context)

    assert context.get_markdown() == "original\n"
    assert not context.tracker.records[-1].applied


def test_segment_threshold_suppresses_only_the_low_confidence_processor(
    tmp_path,
) -> None:
    source = tmp_path / "mixed.md"
    source.write_text("l0ve toone\n", encoding="utf-8")
    config = PipelineConfig(
        {
            "mutation": {"minimum_confidence": 90, "report_only": False},
            "regex": {"enabled": True},
        }
    )
    context = ProcessingContext(config)
    context.load_markdown(source)

    RegexStage(config).execute(context)

    assert context.get_markdown() == "l0ve to one\n"
    assert [(record.confidence, record.applied) for record in context.tracker.records] == [
        (85.0, False),
        (99.0, True),
    ]


def test_structured_visible_text_is_exposed_but_code_is_not(tmp_path) -> None:
    source = tmp_path / "structured.md"
    source.write_text(
        "# l0ve\n\n- l0ve this\n\n> l0ve that\n\n"
        "[ref]: https://l0ve.example/path\n\n"
        "```text\nl0ve literal\n```\n",
        encoding="utf-8",
    )
    context = ProcessingContext(PipelineConfig())
    context.load_markdown(source)

    types = {segment.block_type for segment in context.iter_segments()}
    assert {BlockType.HEADING, BlockType.LIST, BlockType.BLOCKQUOTE} <= types
    assert BlockType.CODE_FENCE not in types

    RegexStage(PipelineConfig({"regex": {"enabled": True}})).execute(context)
    cleaned = context.get_markdown()
    assert "# love" in cleaned
    assert "- love this" in cleaned
    assert "> love that" in cleaned
    assert "https://l0ve.example/path" in cleaned
    assert "l0ve literal" in cleaned


def test_mojibake_repair_is_conservative() -> None:
    assert repair_mojibake("cafÃ© and donâ€™t") == "café and don’t"
    assert repair_mojibake("ordinary café") == "ordinary café"
    assert repair_mojibake("日本語 cafÃ©") == "日本語 café"


def test_repeated_page_artifacts_require_spacing_and_repetition() -> None:
    text = (
        "Book Title\n" + "line\n" * 12
        + "Book Title\n" + "line\n" * 12
        + "Book Title\n"
    )
    findings = find_page_artifacts(text)
    assert [(item.text, item.kind) for item in findings] == [
        ("Book Title", "repeated header/footer")
    ]

    numbered = (
        "1\n" + "line\n" * 12
        + "2\n" + "line\n" * 12
        + "3\n"
    )
    assert find_page_artifacts(numbered)[0].kind == "standalone page numbers"


def test_contextual_real_word_detection_is_report_only(tmp_path) -> None:
    source = tmp_path / "context.md"
    source.write_text("He moved away form home.\n", encoding="utf-8")
    rules = tmp_path / "rules.json"
    rules.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "source": "form",
                        "suggestion": "from",
                        "previous": ["away"],
                        "confidence": 65,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    config = PipelineConfig(
        {
            "contextual_real_words": {
                "enabled": True,
                "rules": str(rules),
            }
        }
    )
    context = ProcessingContext(config)
    context.load_markdown(source)

    result = ContextualRealWordStage(config).execute(context)

    assert result.success and context.get_markdown() == "He moved away form home.\n"
    assert context.tracker.records[-1].broken_word == "form"
    assert not context.tracker.records[-1].applied


def test_repository_defaults_enable_configured_content_removal() -> None:
    config = PipelineConfig.load(Path(__file__).parents[1] / "config.yaml")
    assert config.get("cleanup.excluded_sections") == [
        "Character Profiles",
        "Afterword",
    ]
    for key in (
        "cleanup.remove_front_matter",
        "cleanup.remove_promotional_tail",
        "cleanup.remove_publisher_tail",
        "cleanup.remove_glossary_footnotes",
        "cleanup.remove_footnotes",
        "cleanup.strip_markdown_emphasis",
    ):
        assert config.get_bool(key) is True
