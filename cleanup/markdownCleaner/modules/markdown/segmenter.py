"""
modules/markdown/segmenter.py

Markdown text segmentation model.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MarkdownSegment:
    """
    Represents a single markdown processing unit.
    """

    text: str

    line_number: int

    #
    # Location tracking
    #

    block_index: int = 0

    segment_index: int = 0

    #
    # Original location
    #

    start_line: int = 0

    end_line: int = 0

    #
    # Runtime modified content
    #

    current_text: str = ""

    def __post_init__(
        self,
    ):

        if not self.current_text:

            self.current_text = self.text

        if self.start_line == 0:

            self.start_line = self.line_number

        if self.end_line == 0:

            self.end_line = self.line_number

    # ---------------------------------------------------------

    def update(
        self,
        value: str,
    ):
        """
        Update processed text.
        """

        original_has_newline = self.current_text.endswith("\n")

        if original_has_newline:
            value = value.rstrip("\n") + "\n"

        self.current_text = value

    # ---------------------------------------------------------

    def get_text(
        self,
    ):
        """
        Return current processed text.
        """

        return self.current_text


# Backward-compatible name used by older processor modules.
# Keep this alias so mixed/stale project copies do not fail at import time.
TextSegment = MarkdownSegment
