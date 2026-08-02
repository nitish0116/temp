"""Candidate-only OCR word boundaries awaiting contextual validation."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path


def _key(value: str) -> str:
    return " ".join(value.casefold().split())


@dataclass(frozen=True, slots=True)
class OCRBoundaryCandidates:
    """Map suspicious spaced forms to proposed replacements.

    Unlike ``BrokenWordDecisions``, entries in this store are not approvals.
    They only widen candidate recall when the transformer validator is enabled.
    """

    candidates: dict[str, str] = field(default_factory=dict)
    suppressed: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        normalized: dict[str, str] = {}
        for broken, replacement in self.candidates.items():
            key = _key(str(broken))
            if (
                len(key.split()) != 2
                or not isinstance(replacement, str)
                or not replacement.strip()
            ):
                raise ValueError(f"Invalid OCR boundary candidate: {broken!r}")
            normalized[key] = replacement.strip()
        suppressed = frozenset(_key(str(value)) for value in self.suppressed)
        invalid_suppressions = [
            value for value in suppressed if len(value.split()) != 2
        ]
        if invalid_suppressions:
            raise ValueError(
                "Invalid suppressed OCR boundary: "
                + ", ".join(sorted(invalid_suppressions))
            )
        overlap = set(normalized) & suppressed
        if overlap:
            raise ValueError(
                "OCR boundaries cannot be candidates and suppressed: "
                + ", ".join(sorted(overlap))
            )
        object.__setattr__(self, "candidates", normalized)
        object.__setattr__(self, "suppressed", suppressed)

    @classmethod
    def load(cls, path: str | Path | None) -> "OCRBoundaryCandidates":
        if not path:
            return cls()
        source = Path(path)
        if not source.exists():
            return cls()
        value = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("OCR boundary candidates must be a JSON object.")
        raw = value.get("candidates", value)
        raw_suppressed = value.get("suppressed", [])
        if not isinstance(raw, dict):
            raise ValueError("OCR boundary candidates require a candidates object.")
        if not isinstance(raw_suppressed, list):
            raise ValueError("Suppressed OCR boundaries must be a JSON list.")
        candidates: dict[str, str] = {}
        for broken, replacement in raw.items():
            normalized = _key(str(broken))
            if not isinstance(replacement, str):
                raise ValueError(f"Invalid OCR boundary candidate: {broken!r}")
            proposed = replacement.strip()
            if not normalized or len(normalized.split()) != 2 or not proposed:
                raise ValueError(f"Invalid OCR boundary candidate: {broken!r}")
            candidates[normalized] = proposed
        return cls(candidates, frozenset(str(item) for item in raw_suppressed))

    def replacement(self, left: str, right: str) -> str | None:
        """Return a proposal without implying that the merge is safe."""

        return self.candidates.get(_key(f"{left} {right}"))

    def is_suppressed(self, left: str, right: str) -> bool:
        """Return whether strong corpus evidence says to preserve the space."""

        return _key(f"{left} {right}") in self.suppressed


__all__ = ["OCRBoundaryCandidates"]
