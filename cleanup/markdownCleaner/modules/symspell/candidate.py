"""Correction candidate used by the local SymSpell lookup engine."""

from __future__ import annotations

from dataclasses import dataclass, field
import math


@dataclass
class CorrectionCandidate:
    """Represent and score one possible dictionary correction.

    Example:
        ``instance = CorrectionCandidate("Teh", "the", 1)``
        Expected behavior: Represent and score one possible dictionary correction.
    """

    original: str
    corrected: str
    distance: int
    frequency: int = 0
    confidence: float = 0.0
    source: str = "default"
    metadata: dict = field(default_factory=dict)

    def calculate_confidence(self, max_distance: int = 2) -> float:
        """Return a deliberately conservative auto-correction score.

        The old formula capped one-edit candidates below 70, making a threshold
        such as 92 impossible to reach. Here a one-edit candidate starts at 86
        and earns up to 12 points from corpus frequency. Two-edit candidates are
        intentionally kept too low for normal automatic application.

        Example:
            ``result = instance.calculate_confidence()``
            Expected behavior: Return a deliberately conservative auto-correction score.
        """
        if self.distance <= 0:
            score = 100.0
        elif self.distance == 1:
            # 0..12 frequency bonus; very common words score highest.
            bonus = min(12.0, max(0.0, math.log10(max(self.frequency, 1))) * 2.0)
            score = 86.0 + bonus
        else:
            bonus = min(8.0, max(0.0, math.log10(max(self.frequency, 1))) * 1.25)
            score = 68.0 + bonus
        self.confidence = round(min(score, 99.0), 2)
        return self.confidence

    def is_safe(self, threshold: float = 92.0) -> bool:
        """Return whether the candidate meets an automatic-correction threshold.

        Example:
            ``result = instance.is_safe()``
            Expected behavior: Return whether the candidate meets an automatic-correction threshold.
        """
        return self.confidence >= threshold

    def to_dict(self) -> dict:
        """Serialize the candidate for structured reporting.

        Example:
            ``result = instance.to_dict()``
            Expected behavior: Serialize the candidate for structured reporting.
        """
        return {
            "original": self.original,
            "corrected": self.corrected,
            "distance": self.distance,
            "frequency": self.frequency,
            "confidence": self.confidence,
            "source": self.source,
        }
