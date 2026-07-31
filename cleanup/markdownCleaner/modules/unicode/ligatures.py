"""
modules/unicode/ligatures.py

Normalize typographic ligatures commonly introduced by
PDF extraction and OCR.

Examples:

    ﬁre      -> fire
    oﬃcer    -> officer
    ﬂame     -> flame
"""

from __future__ import annotations

from ..markdown.segmenter import MarkdownSegment

from .processor import UnicodeProcessor
from .constants import (
    LIGATURE_TRANSLATION,
    LIGATURES,
)


class LigatureProcessor(UnicodeProcessor):
    """Replace Unicode ligature characters with their ASCII
    character equivalents.

    Example:
        ``instance = LigatureProcessor(context)``
        Expected behavior: Replace Unicode ligature characters with their ASCII.
    """

    name = "Ligatures"

    # ---------------------------------------------------------

    def process(
        self,
        segment: MarkdownSegment,
    ) -> bool:
        """Replace ligatures in one segment.

        Returns
        -------
        bool
            True if any replacement occurred.

        Example:
            ``result = instance.process(segment)``
            Expected behavior: Replace ligatures in one segment.
        """

        if not self.enabled(
            "ligatures",
            True,
        ):
            return False

        before = segment.current_text

        if not before:

            return False

        after = before.translate(LIGATURE_TRANSLATION)

        replacements = self._count_changes(
            before,
            after,
        )

        return self.apply_change(
            segment=segment,
            before=before,
            after=after,
            reason="Ligature normalization",
            statistic="ligatures_fixed",
            statistic_amount=replacements,
            confidence=100.0,
        )

    # ---------------------------------------------------------

    def _count_changes(
        self,
        before: str,
        after: str,
    ) -> int:
        """Count number of ligature characters replaced.

        Used only for statistics.

        Example:
            ``result = instance._count_changes("teh", "the")``
            Expected behavior: Count number of ligature characters replaced.
        """

        # ``after`` remains part of this helper's long-standing signature for
        # compatibility with focused callers and tests.
        del after
        return sum(before.count(char) for char in LIGATURES)
