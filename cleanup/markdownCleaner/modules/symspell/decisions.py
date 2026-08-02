"""Portable reviewed decisions for OCR-created word boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path


def _key(value: str) -> str:
    """Normalize whitespace and case for stable decision lookup."""

    return " ".join(value.casefold().split())


@dataclass(frozen=True, slots=True)
class AcceptedBoundary:
    """One reviewed replacement with optional neighboring-word blockers."""

    replacement: str
    blocked_previous: frozenset[str] = frozenset()
    blocked_following: frozenset[str] = frozenset()

    def allows(self, previous: str, following: str) -> bool:
        return not (
            previous.casefold() in self.blocked_previous
            or following.casefold() in self.blocked_following
        )


@dataclass(frozen=True, slots=True)
class BrokenWordDecisions:
    """User-reviewed accepted replacements and rejected joins.

    The JSON format is intentionally small and portable::

        {"accepted": {"Ley win": "Leywin"}, "rejected": ["to one"]}
    """

    accepted: dict[str, AcceptedBoundary] = field(default_factory=dict)
    rejected: frozenset[str] = frozenset()

    @classmethod
    def load(cls, path: str | Path | None) -> "BrokenWordDecisions":
        if not path:
            return cls()
        source = Path(path)
        if not source.exists():
            return cls()
        data = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Broken-word decisions must be a JSON object.")
        raw_accepted = data.get("accepted", {})
        raw_rejected = data.get("rejected", [])
        if not isinstance(raw_accepted, dict) or not isinstance(raw_rejected, list):
            raise ValueError(
                "Broken-word decisions require an accepted object and rejected list."
            )
        accepted: dict[str, AcceptedBoundary] = {}
        for broken, value in raw_accepted.items():
            if isinstance(value, str):
                replacement = value.strip()
                blocked_previous: list[str] = []
                blocked_following: list[str] = []
            elif isinstance(value, dict):
                replacement = str(value.get("replacement", "")).strip()
                blocked_previous = value.get("blocked_previous", [])
                blocked_following = value.get("blocked_following", [])
                if not isinstance(blocked_previous, list) or not isinstance(
                    blocked_following, list
                ):
                    raise ValueError(
                        "Decision context blockers must be JSON lists."
                    )
            else:
                raise ValueError(
                    "Accepted broken-word values must be strings or objects."
                )
            if not str(broken).strip() or not replacement:
                continue
            accepted[_key(str(broken))] = AcceptedBoundary(
                replacement=replacement,
                blocked_previous=frozenset(
                    str(word).casefold() for word in blocked_previous
                ),
                blocked_following=frozenset(
                    str(word).casefold() for word in blocked_following
                ),
            )
        rejected = frozenset(
            _key(str(broken)) for broken in raw_rejected if str(broken).strip()
        )
        overlap = set(accepted) & rejected
        if overlap:
            raise ValueError(
                "Broken-word decisions cannot both accept and reject: "
                + ", ".join(sorted(overlap))
            )
        return cls(accepted=accepted, rejected=rejected)

    def accepted_replacement(
        self,
        left: str,
        right: str,
        *,
        previous: str = "",
        following: str = "",
    ) -> str | None:
        decision = self.accepted.get(_key(f"{left} {right}"))
        if decision is None or not decision.allows(previous, following):
            return None
        return decision.replacement

    def is_rejected(self, left: str, right: str) -> bool:
        return _key(f"{left} {right}") in self.rejected

    def is_context_blocked(
        self,
        left: str,
        right: str,
        *,
        previous: str = "",
        following: str = "",
    ) -> bool:
        """Return whether a reviewed entry rejects this local context."""

        decision = self.accepted.get(_key(f"{left} {right}"))
        return decision is not None and not decision.allows(previous, following)
