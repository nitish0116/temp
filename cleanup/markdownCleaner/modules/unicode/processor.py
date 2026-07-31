"""
modules/unicode/processor.py

Base class for Unicode cleanup processors.
"""

from __future__ import annotations

from ..core.config import require_bool
from ..core.processor import SegmentProcessor
from ..markdown.segmenter import MarkdownSegment


class UnicodeProcessor(SegmentProcessor):
    """Base class for every Unicode cleanup processor.

    Responsibilities
    ----------------
    * Access shared ProcessingContext
    * Record changes
    * Provide a common processing interface

    Example:
        ``instance = UnicodeProcessor(context)``
        Expected behavior: Base class for every Unicode cleanup processor.
    """

    #: Display name used in logs/reports
    name = "Unicode"

    def enabled(self, key: str, default: bool = True) -> bool:
        """Return whether a named Unicode correction is enabled.

        Example:
            ``result = instance.enabled("section.option")``
            Expected behavior: Return whether a named Unicode correction is enabled.
        """
        unicode_config = self.config.data.get("unicode", {})

        fixes = unicode_config.get("fixes", {})

        return require_bool(
            fixes.get(key, default),
            f"unicode.fixes.{key}",
        )

    def apply_change(
        self,
        *,
        segment: MarkdownSegment,
        before: str,
        after: str,
        reason: str,
        statistic: str,
        statistic_amount: int = 1,
        confidence: float = 100.0,
    ) -> bool:
        """Commit and audit one Unicode transformation.

        All Unicode processors use this method so segment mutation, tracker
        records, and statistics cannot silently drift apart. ``False`` is
        returned for a no-op and no audit/statistic side effect is produced.
        """
        if before == after:
            return False

        segment.current_text = after
        self.record_change(
            segment=segment,
            before=before,
            after=after,
            confidence=confidence,
            reason=reason,
        )
        self.context.increment(statistic, statistic_amount)
        return True
