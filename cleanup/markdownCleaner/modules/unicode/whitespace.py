"""
modules/unicode/whitespace.py

Whitespace normalization processor.

Handles whitespace artifacts created by OCR and PDF extraction.
"""

from __future__ import annotations

import re

from ..markdown.segmenter import MarkdownSegment

from .processor import UnicodeProcessor

from .constants import (
    SPACE_TRANSLATION,
    SOFT_HYPHEN,
)


class WhitespaceProcessor(UnicodeProcessor):
    """
    Normalize whitespace while preserving Markdown structure.
    """

    name = "Whitespace"

    def process(
        self,
        segment: MarkdownSegment,
    ) -> bool:
        """
        Normalize whitespace.

        Returns
        -------
        bool
            True if text changed.
        """

        before = segment.current_text

        if not before:
            return False

        after = before

        #
        # Unicode spaces
        #

        if self.enabled(
            "normalize_spaces",
            True,
        ):

            after = after.translate(SPACE_TRANSLATION)

        #
        # Soft hyphen
        #

        if self.enabled(
            "remove_soft_hyphen",
            True,
        ):

            after = after.replace(
                SOFT_HYPHEN,
                "",
            )

        #
        # Line endings
        #

        if self.enabled(
            "normalize_line_endings",
            True,
        ):

            after = self._normalize_line_endings(after)

        #
        # Remove trailing whitespace
        #

        if self.enabled(
            "remove_trailing_spaces",
            True,
        ):

            after = self._remove_trailing_spaces(after)

        #
        # Collapse internal spaces
        #

        if self.enabled(
            "collapse_spaces",
            True,
        ):

            after = self._collapse_spaces(after)

        if before == after:

            return False

        segment.current_text = after

        self.record_change(
            segment=segment,
            before=before,
            after=after,
            reason="Whitespace normalization",
            confidence=100.0,
        )

        self.context.increment("spaces_normalized")

        return True

    # ---------------------------------------------------------

    def _normalize_line_endings(
        self,
        text: str,
    ) -> str:
        """
        Convert CRLF/CR to LF.
        """

        return text.replace(
            "\r\n",
            "\n",
        ).replace(
            "\r",
            "\n",
        )

    # ---------------------------------------------------------

    def _remove_trailing_spaces(
        self,
        text: str,
    ) -> str:
        """
        Remove spaces before newline.

        Keeps Markdown indentation intact.
        """

        return re.sub(
            r"[ \t]+\n",
            "\n",
            text,
        )

    # ---------------------------------------------------------

    def _collapse_spaces(
        self,
        text: str,
    ) -> str:
        """
        Collapse multiple spaces.

        Does NOT touch newlines.
        """

        lines = text.split("\n")

        cleaned = []

        for line in lines:

            #
            # Preserve Markdown indentation
            #

            leading = len(line) - len(line.lstrip(" "))

            content = line.lstrip(" ")

            content = re.sub(
                r" {2,}",
                " ",
                content,
            )

            cleaned.append(" " * leading + content)

        return "\n".join(cleaned)
