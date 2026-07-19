"""
modules/core/context.py

Shared processing state for OCR cleanup pipeline.
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
import logging

from ..report.change_log import ChangeLog

from ..markdown.markdown import (
    MarkdownParser,
    MarkdownDocument,
    BlockType,
)

from ..markdown.segmenter import MarkdownSegment


class ProcessingContext:
    """
    Shared processing state.

    Markdown flow:

        file
          ↓
      MarkdownParser
          ↓
     MarkdownDocument
          ↓
    editable paragraph blocks
          ↓
       pipeline stages
          ↓
      rebuild markdown
    """

    def __init__(self, config):

        self.config = config

        self.logger = logging.getLogger("ocr_cleanup")

        self.tracker = ChangeLog()

        self.source_file = None
        self.output_file = None

        self.original_markdown = ""
        self.current_markdown = ""

        #
        # New markdown model
        #

        self.document: MarkdownDocument | None = None

        #
        # Processing segments
        #

        self.segments: list[MarkdownSegment] = []

        self.statistics = {
            "started": datetime.now().isoformat(),
            "stages": {},
        }

        self.metadata = {
            "version": "1.0",
            "source": None,
        }

    # -------------------------------------------------------------

    @property
    def total_changes(self):

        return self.tracker.total_changes()

    # -------------------------------------------------------------

    def load_markdown(self, file_path):

        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(path)

        self.source_file = str(path)

        content = path.read_text(encoding="utf-8")

        self.original_markdown = content

        #
        # Parse markdown
        #

        parser = MarkdownParser()

        self.document = parser.parse(content)

        #
        # Create editable segments
        #

        self._create_segments()

        self.update_markdown()

    # -------------------------------------------------------------

    def _create_segments(self):

        self.segments = []

        segment_index = 0

        for block_index, block in enumerate(self.document.blocks):

            #
            # Only paragraphs are editable
            #

            if block.block_type != BlockType.PARAGRAPH:
                continue

            self.segments.append(
                MarkdownSegment(
                    text=block.content,
                    current_text=block.content,
                    block_index=block_index,
                    segment_index=segment_index,
                    line_number=block.start_line,
                    start_line=block.start_line,
                    end_line=block.end_line,
                )
            )

            segment_index += 1

    # -------------------------------------------------------------

    def update_markdown(self):

        #
        # Copy edited text back into markdown blocks
        #

        for segment in self.segments:

            self.document.blocks[segment.block_index].content = (
                segment.current_text
            )

        #
        # Rebuild markdown
        #

        self.current_markdown = self.document.to_markdown()

    # -------------------------------------------------------------

    def get_markdown(self):

        self.update_markdown()

        return self.current_markdown

    # -------------------------------------------------------------

    def iter_segments(self):

        yield from self.segments

    # -------------------------------------------------------------

    def add_stat(self, stage, changes):

        self.statistics["stages"][stage] = changes

    # -------------------------------------------------------------

    def increment(self, name, amount=1):

        self.statistics[name] = (
            self.statistics.get(name, 0) + amount
        )

    # -------------------------------------------------------------

    def finish(self):

        self.statistics["finished"] = datetime.now().isoformat()