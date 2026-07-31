"""
modules/regex/broken_words.py

Fix OCR-created spaces inside words.

Examples:

    some one     -> someone
    every thing  -> everything

"""

from __future__ import annotations

from dataclasses import dataclass

from ..markdown.segmenter import MarkdownSegment

from .processor import RegexProcessor

from .constants import (
    BROKEN_WORD_RULES,
    BoundaryCorrection,
    BoundaryEvidence,
    BrokenWordRule,
)


@dataclass(frozen=True)
class BrokenWordApplication:
    """A concrete boundary correction located in one source segment."""

    rule: BrokenWordRule
    start: int
    end: int
    broken_word: str
    replacement: str

    @property
    def correction(self) -> BoundaryCorrection:
        """Expose the direction without requiring callers to inspect the rule."""

        return self.rule.correction

    @property
    def evidence(self) -> BoundaryEvidence:
        """Expose the OCR evidence used for this application."""

        return self.rule.evidence


class BrokenWordProcessor(RegexProcessor):
    """Merge words incorrectly separated by OCR.

    Example:
        ``instance = BrokenWordProcessor(context)``
        Expected behavior: Merge words incorrectly separated by OCR.
    """

    name = "BrokenWords"

    # ---------------------------------------------------------

    def process(
        self,
        segment: MarkdownSegment,
    ) -> bool:
        """Fix broken words.

        Returns:
            True if changes occurred.

        Example:
            ``result = instance.process(segment)``
            Expected behavior: Fix broken words.
        """

        before = segment.current_text

        if not before or not self.correction_enabled("broken_words"):

            return False

        applications = self.find_applications(before)

        if not applications:

            return False

        after = self.apply_applications(before, applications)

        segment.current_text = after

        average_confidence = sum(
            application.rule.confidence for application in applications
        ) / len(applications)

        self.record_change(
            segment=segment,
            before=before,
            after=after,
            reason=self._reason_for(applications),
            confidence=average_confidence,
            broken_word=", ".join(
                application.broken_word for application in applications
            ),
        )

        self.context.increment(
            "broken_words_fixed",
            len(applications),
        )

        return True

    # ---------------------------------------------------------

    def find_applications(self, text: str) -> list[BrokenWordApplication]:
        """Locate non-overlapping corrections in their source order."""

        candidates = []

        for rule in BROKEN_WORD_RULES:
            for match in rule.pattern.finditer(text):
                candidates.append(
                    BrokenWordApplication(
                        rule=rule,
                        start=match.start(),
                        end=match.end(),
                        broken_word=match.group(0),
                        replacement=rule.replacement_for(match),
                    )
                )

        applications = []
        previous_end = -1

        for application in sorted(
            candidates,
            key=lambda item: (item.start, item.end),
        ):
            if application.start < previous_end:
                continue

            applications.append(application)
            previous_end = application.end

        return applications

    # ---------------------------------------------------------

    @staticmethod
    def apply_applications(
        text: str,
        applications: list[BrokenWordApplication],
    ) -> str:
        """Render typed applications from the unchanged source string."""

        pieces = []
        cursor = 0

        for application in applications:
            pieces.append(text[cursor : application.start])
            pieces.append(application.replacement)
            cursor = application.end

        pieces.append(text[cursor:])

        return "".join(pieces)

    # ---------------------------------------------------------

    @staticmethod
    def _reason_for(
        applications: list[BrokenWordApplication],
    ) -> str:
        """Describe whether the segment joined, restored, or mixed boundaries."""

        corrections = {
            application.correction for application in applications
        }

        if corrections == {BoundaryCorrection.JOIN}:
            return "OCR broken word merge"

        if corrections == {BoundaryCorrection.SPLIT}:
            return "OCR missing word-boundary repair"

        return "OCR broken word merge and boundary repair"
