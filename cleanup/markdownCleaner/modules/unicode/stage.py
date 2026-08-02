"""Ordered Unicode normalization stage."""

from __future__ import annotations

from ..core.context import ProcessingContext
from ..core.processor import SegmentProcessor
from ..core.stage import SegmentProcessingStage
from .invisible import InvisibleProcessor
from .ligatures import LigatureProcessor
from .mojibake import MojibakeProcessor
from .normalizer import UnicodeNormalizer
from .punctuation import PunctuationProcessor
from .whitespace import WhitespaceProcessor


class UnicodeStage(SegmentProcessingStage):
    """Normalize Unicode safely across every editable paragraph."""

    name = "Unicode"
    config_section = "unicode"

    def build_processors(
        self,
        context: ProcessingContext,
    ) -> list[SegmentProcessor]:
        """Construct processors in normalization dependency order."""

        return [
            MojibakeProcessor(context),
            UnicodeNormalizer(context),
            InvisibleProcessor(context),
            LigatureProcessor(context),
            WhitespaceProcessor(context),
            PunctuationProcessor(context),
        ]
