"""
modules/symspell/candidate.py

Data structure for SymSpell correction candidates.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CorrectionCandidate:
    """
    Represents one possible correction.
    """

    # Original OCR word

    original: str

    # Suggested corrected word

    corrected: str

    # Levenshtein / Damerau distance

    distance: int

    # Frequency in dictionary

    frequency: int = 0

    # Calculated confidence

    confidence: float = 0.0

    # Dictionary source

    source: str = "default"

    # Extra metadata

    metadata: dict = field(default_factory=dict)

    # ---------------------------------------------------------

    def calculate_confidence(
        self,
        max_distance: int = 2,
    ) -> float:
        """
        Calculate correction confidence.

        Factors:

        - lower edit distance = better
        - higher frequency = better

        """

        #
        # Distance score
        #

        distance_score = max(0, 1 - (self.distance / max_distance))

        #
        # Frequency score
        #

        if self.frequency <= 0:

            frequency_score = 0.1

        else:

            import math

            frequency_score = min(1.0, math.log10(self.frequency + 1) / 6)

        #
        # Combined score
        #

        score = distance_score * 0.65 + frequency_score * 0.35

        self.confidence = round(
            score * 100,
            2,
        )

        return self.confidence

    # ---------------------------------------------------------

    def is_safe(
        self,
        threshold: float = 85.0,
    ) -> bool:
        """
        Determine whether correction
        can be applied automatically.
        """

        return self.confidence >= threshold

    # ---------------------------------------------------------

    def to_dict(
        self,
    ) -> dict:
        """
        Convert to report-friendly format.
        """

        return {
            "original": self.original,
            "corrected": self.corrected,
            "distance": self.distance,
            "frequency": self.frequency,
            "confidence": self.confidence,
            "source": self.source,
        }
