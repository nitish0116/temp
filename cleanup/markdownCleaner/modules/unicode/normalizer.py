"""
modules/unicode/normalizer.py

Unicode normalization processor.

Uses Python's built-in unicodedata module to normalize
Unicode representations.

Examples:

    ﬁ -> fi          (NFKC)
    ① -> 1           (NFKC)
    ｆｕｌｌ -> full  (NFKC)

"""

from __future__ import annotations

import unicodedata

from ..markdown.segmenter import MarkdownSegment

from .processor import UnicodeProcessor
from .constants import (
    UNICODE_NORMALIZATION_FORMS,
    DEFAULT_NORMALIZATION_FORM,
)


class UnicodeNormalizer(UnicodeProcessor):
    """
    Normalize Unicode characters.

    This should run first in UnicodeStage because later processors
    operate on normalized text.
    """

    name = "UnicodeNormalizer"

    def __init__(self, context):

        super().__init__(context)

        unicode_config = self.config.get("unicode", {})

        form = unicode_config.get(
            "normalize_form",
            DEFAULT_NORMALIZATION_FORM,
        )

        if form not in UNICODE_NORMALIZATION_FORMS:

            raise ValueError(
                f"Invalid Unicode normalization form: {form}. "
                f"Allowed: {UNICODE_NORMALIZATION_FORMS}"
            )

        self.form = form

    # ---------------------------------------------------------

    def process(
        self,
        segment: MarkdownSegment,
    ) -> bool:
        """
        Normalize one text segment.

        Returns
        -------
        bool
            True if text changed.
        """

        before = segment.current_text

        if not before:

            return False

        after = unicodedata.normalize(
            self.form,
            before,
        )

        if before == after:

            return False

        segment.current_text = after

        self.record_change(
            segment=segment,
            before=before,
            after=after,
            reason=(f"Unicode {self.form} normalization"),
            confidence=100.0,
        )

        self.context.increment(
            "normalized",
        )

        self.logger.debug(f"{self.name}: normalized " f"segment {segment.segment_index}")

        return True
