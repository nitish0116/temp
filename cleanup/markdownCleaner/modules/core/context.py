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
    """Shared processing state.

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

    Example:
        ``instance = ProcessingContext(config)``
        Expected behavior: Shared processing state.
    """

    def __init__(self, config):
        """Create empty shared state for one pipeline execution.

        Example:
            ``instance = ProcessingContext(config)``
            Expected behavior: Create empty shared state for one pipeline execution.
        """

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
        """Return the number of change and review records collected so far.

        Example:
            ``value = instance.total_changes``
            Expected behavior: Return the number of change and review records collected so far.
        """

        return self.tracker.total_changes()

    # -------------------------------------------------------------

    def load_markdown(self, file_path):
        """Read, parse, and segment a UTF-8 Markdown source file.

        Example:
            ``result = instance.load_markdown(Path("input.md"))``
            Expected behavior: Read, parse, and segment a UTF-8 Markdown source file.
        """

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
        """Create editable wrappers for paragraph blocks in the document.

        Example:
            ``result = instance._create_segments()``
            Expected behavior: Create editable wrappers for paragraph blocks in the document.
        """

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
        """Copy segment edits into the document and rebuild Markdown text.

        Example:
            ``result = instance.update_markdown()``
            Expected behavior: Copy segment edits into the document and rebuild Markdown text.
        """

        #
        # Copy edited text back into markdown blocks
        #

        for segment in self.segments:

            self.document.blocks[segment.block_index].content = segment.current_text

        #
        # Rebuild markdown
        #

        self.current_markdown = self.document.to_markdown()

    # -------------------------------------------------------------

    def replace_markdown(self, markdown: str):
        """Replace the complete working document and rebuild editable segments.

                Example:
                    ``result = instance.replace_markdown("# Chapter 1

        Story.")`` demonstrates this behavior: Replace the complete working document and rebuild editable segments.

        Example:
            ``result = instance.replace_markdown("# Chapter 1\n\nStory.")``
            Expected behavior: Replace the complete working document and rebuild editable segments.
        """
        self.current_markdown = markdown
        parser = MarkdownParser()
        self.document = parser.parse(markdown)
        self._create_segments()
        self.update_markdown()

    # -------------------------------------------------------------

    def get_markdown(self):
        """Return current Markdown after synchronizing editable segments.

        Example:
            ``result = instance.get_markdown()``
            Expected behavior: Return current Markdown after synchronizing editable segments.
        """

        self.update_markdown()

        return self.current_markdown

    # -------------------------------------------------------------

    def iter_segments(self):
        """Yield editable Markdown segments in document order.

        Example:
            ``result = instance.iter_segments()``
            Expected behavior: Yield editable Markdown segments in document order.
        """

        yield from self.segments

    # -------------------------------------------------------------

    def add_stat(self, stage, changes):
        """Store a stage's change count in execution statistics.

        Example:
            ``result = instance.add_stat("RegexOCR", changes)``
            Expected behavior: Store a stage's change count in execution statistics.
        """

        self.statistics["stages"][stage] = changes

    # -------------------------------------------------------------

    def increment(self, name, amount=1):
        """Increment a named processing statistic.

        Example:
            ``result = instance.increment("Unicode")``
            Expected behavior: Increment a named processing statistic.
        """

        self.statistics[name] = self.statistics.get(name, 0) + amount

    # -------------------------------------------------------------

    def finish(self):
        """Record the processing completion timestamp.

        Example:
            ``result = instance.finish()``
            Expected behavior: Record the processing completion timestamp.
        """

        self.statistics["finished"] = datetime.now().isoformat()
