from __future__ import annotations
from multiprocessing import context

from ..core.stage import PipelineStage, StageResult 

from .image_text import ImageTextProcessor
from .frontmatter import FrontMatterProcessor
from .markdown import MarkdownProcessor


class NovelCleanupStage(PipelineStage):

    name = "NovelCleanup"

    config_section = "unicode"


    def __init__(
        self,
        config,
    ):
        super().__init__(config)

        self.processors = []
        
        
    def initialize(
        self,
        context,
    ):

        self.processors = [
            ImageTextProcessor(context),
            FrontMatterProcessor(context),
            MarkdownProcessor(context),
        ]
    def process(
        self,
        context,
    ) -> StageResult:
         
        if not self.processors:

            self.initialize(context)

        start_changes = context.total_changes
        
        for segment in context.iter_segments():

            for processor in self.processors:

                processor.process(segment)
                


        total_changes = context.total_changes - start_changes

        return StageResult(
            stage=self.name,
            changes=total_changes,
        )