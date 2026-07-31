"""Regression tests for the Markdown synchronization contract between stages."""

from markdownCleaner.modules.core.config import PipelineConfig
from markdownCleaner.modules.core.context import ProcessingContext
from markdownCleaner.modules.core.stage import PipelineStage, StageResult
from markdownCleaner.modules.regex.stage import RegexStage
from markdownCleaner.modules.symspell.stage import SymSpellStage


class _SegmentEditStage(PipelineStage):
    """Represent a normal stage that edits one Markdown segment."""

    name = "SegmentEdit"

    def process(self, context) -> StageResult:
        context.segments[0].current_text = "Published edit."
        return StageResult(stage=self.name, changes=1)


class _InitializeObserverStage(PipelineStage):
    """Capture the canonical Markdown visible during stage initialization."""

    name = "InitializeObserver"

    def __init__(self, config):
        super().__init__(config)
        self.markdown_at_initialize = None

    def initialize(self, context) -> None:
        self.markdown_at_initialize = context.current_markdown

    def process(self, context) -> StageResult:
        return StageResult(stage=self.name)


def test_successful_stage_publishes_segment_edits_before_next_initialize(
    tmp_path,
):
    """Commit a normal segment edit before the following stage initializes."""
    source = tmp_path / "sample.md"
    source.write_text("Original text.", encoding="utf-8")
    config = PipelineConfig()
    context = ProcessingContext(config)
    context.load_markdown(source)

    edit_result = _SegmentEditStage(config).execute(context)
    observer = _InitializeObserverStage(config)
    observer_result = observer.execute(context)

    assert edit_result.success
    assert observer_result.success
    assert context.current_markdown == "Published edit."
    assert observer.markdown_at_initialize == "Published edit."


def test_regex_correction_survives_symspell_cross_block_merge(tmp_path):
    """Preserve an earlier segment correction during a whole-document merge."""
    dictionary = tmp_path / "frequency.txt"
    dictionary.write_text(
        "\n".join(
            [
                "love 1000000",
                "inner 1000000",
                "energy 50000000",
                "was 1000000",
                "utilized 1000000",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    source = tmp_path / "sample.md"
    source.write_text(
        "l0ve inner ener \n\ngy was utilized.",
        encoding="utf-8",
    )
    config = PipelineConfig(
        {
            "regex": {"enabled": True},
            "symspell": {
                "enabled": True,
                "dictionary": str(dictionary),
                "wordfreq_enabled": False,
                "broken_word_merge_minimum_frequency": 50000,
                "auto_protect_proper_nouns": False,
            },
        }
    )
    context = ProcessingContext(config)
    context.load_markdown(source)

    regex_result = RegexStage(config).execute(context)

    assert regex_result.success
    assert context.current_markdown.startswith("love inner ener")

    symspell_result = SymSpellStage(config).execute(context)

    assert symspell_result.success
    assert context.current_markdown == "love inner energy was utilized."
    assert "l0ve" not in context.current_markdown
    assert "\n\ngy" not in context.current_markdown

