"""
modules/core/context.py

Shared processing state for OCR cleanup pipeline.
"""

from __future__ import annotations


from pathlib import Path

from datetime import datetime


from ..report.change_log import (
    ChangeLog,
)

import logging

class ProcessingContext:
    """
    Shared state passed through all pipeline stages.
    """

    def __init__(
        self,
        config,
    ):

        self.config = config

        #
        # Input/output information
        #

        self.source_file = None

        self.output_file = None

        #
        # Markdown data
        #

        self.original_markdown = ""

        self.current_markdown = ""

        #
        # Segments
        #

        self.segments = []

        #
        # Change tracking
        #
        self.logger = logging.getLogger("ocr_cleanup")
        
        self.tracker = ChangeLog()

        #
        # Statistics
        #

        self.statistics = {
            "started": datetime.now().isoformat(),
            "stages": {},
        }

        #
        # Runtime metadata
        #

        self.metadata = {

    "version": "1.0",

    "source": None,

}

    def increment(self, name, amount=1):

        self.statistics[name] = (
        self.statistics.get(name, 0) + amount
    )
    # ---------------------------------------------------------

    @property
    def total_changes(self):

        return self.tracker.total_changes()
    
    def load_markdown(
        self,
        file_path,
    ):
        """
        Load markdown input.
        """

        path = Path(file_path)

        if not path.exists():

            raise FileNotFoundError(path)

        self.source_file = str(path)

        content = path.read_text(encoding="utf-8")

        self.original_markdown = content

        self.current_markdown = content

        #
        # Segment creation
        #

        self._create_segments()

    # ---------------------------------------------------------

    def _create_segments(
        self,
    ):
        """
        Split markdown into processing blocks.

        Keeps processing lightweight.
        """

        from ..markdown.segmenter import (
            MarkdownSegment,
        )

        self.segments = []

        lines = self.current_markdown.splitlines(keepends=True)

        for index, line in enumerate(lines):

            self.segments.append(
                MarkdownSegment(
    text=line,
    current_text=line,
    line_number=index + 1,
    start_line=index + 1,
    end_line=index + 1,
    block_index=index,
    segment_index=0,
)
            )

    # ---------------------------------------------------------

    def update_markdown(self):
        """
        Rebuild markdown from processed segments.
        """
    
        ordered_segments = sorted(
            self.segments,
            key=lambda s: (
                getattr(s, "block_index", 0),
                getattr(s, "segment_index", 0),
            ),
        )
    
        self.current_markdown = "".join(
            segment.current_text
            for segment in ordered_segments
        )

    # ---------------------------------------------------------

    def get_markdown(
        self,
    ):
        """
        Return current processed markdown.
        """

        self.update_markdown()

        return self.current_markdown

    # ---------------------------------------------------------

    def add_stat(
        self,
        stage,
        changes,
    ):
        """
        Store stage statistics.
        """

        self.statistics["stages"][stage] = changes

    # ---------------------------------------------------------

    def finish(
        self,
    ):
        """
        Mark pipeline completion.
        """

        self.statistics["finished"] = datetime.now().isoformat()

    def iter_segments(self):

        yield from self.segments
