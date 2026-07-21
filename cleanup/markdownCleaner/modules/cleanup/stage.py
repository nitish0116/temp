from __future__ import annotations

from ..core.stage import PipelineStage, StageResult
from .image_text import ImageTextProcessor
from .markdown import MarkdownProcessor


class NovelCleanupStage(PipelineStage):
    """Apply lightweight cleanup independently to editable Markdown segments.

    ``ImageTextProcessor`` handles converter image-text markers and
    ``MarkdownProcessor`` removes presentation markup that should not reach TTS.
    This legacy composite stage is available for callers that need segment-level
    cleanup; the main pipeline currently performs generalized reconstruction in
    :class:`DocumentCleanupStage`.

    Workflow::

        editable segment -> remove marked image text -> normalize inline markup
        -> update segment metrics -> StageResult containing this stage's changes

    Example::

        stage = NovelCleanupStage(config)
        stage.initialize(context)
        result = stage.process(context)

    Each segment is passed first to ``ImageTextProcessor`` and then to
    ``MarkdownProcessor`` so markup left around a removed image block can be
    normalized afterward.
    """

    name = "NovelCleanup"
    config_section = "cleanup"

    def __init__(self, config):
        """Initialize the segment cleanup stage with no active processors.

        Args:
            config: Pipeline configuration used by the inherited stage lifecycle.

        Example::

            stage = NovelCleanupStage(config)
            assert stage.processors == []

        Processor creation is deferred until a document context is available.
        """
        super().__init__(config)
        self.processors = []

    def initialize(self, context):
        """Create ordered image-text and Markdown processors for ``context``.

        Example::

            stage = NovelCleanupStage(config)
            stage.initialize(context)
            assert isinstance(stage.processors[0], ImageTextProcessor)
            assert isinstance(stage.processors[1], MarkdownProcessor)

        Both processors retain the same context so their updates and metrics are
        recorded in the document's shared audit state.
        """
        self.processors = [
            ImageTextProcessor(context),
            MarkdownProcessor(context),
        ]

    def process(self, context) -> StageResult:
        """Run both processors over every editable segment in order.

        The method compares the shared change count before and after traversal,
        so its result reflects only changes made during this stage.

        Example::

            stage = NovelCleanupStage(config)
            result = stage.process(context)
            assert result.stage == "NovelCleanup"
            assert result.changes == context.total_changes - changes_before

        With two editable segments, each processor is invoked twice in processor
        order. If neither processor changes either segment, ``changes`` is zero.
        """
        if not self.processors:
            self.initialize(context)
        start_changes = context.total_changes
        for segment in context.iter_segments():
            for processor in self.processors:
                processor.process(segment)
        return StageResult(
            stage=self.name, changes=context.total_changes - start_changes
        )
