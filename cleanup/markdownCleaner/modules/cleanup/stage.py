from __future__ import annotations

from ..core.stage import PipelineStage, StageResult
from .image_text import ImageTextProcessor
from .markdown import MarkdownProcessor


class NovelCleanupStage(PipelineStage):
    """Small segment-level Markdown cleanup after document reconstruction."""

    name = "NovelCleanup"
    config_section = "cleanup"

    def __init__(self, config):
        super().__init__(config)
        self.processors = []

    def initialize(self, context):
        self.processors = [
            ImageTextProcessor(context),
            MarkdownProcessor(context),
        ]

    def process(self, context) -> StageResult:
        if not self.processors:
            self.initialize(context)
        start_changes = context.total_changes
        for segment in context.iter_segments():
            for processor in self.processors:
                processor.process(segment)
        return StageResult(stage=self.name, changes=context.total_changes - start_changes)
