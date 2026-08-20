"""Draft entity and setting analysis with auditable source evidence."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Protocol

from .source import HEADING, normalize_markdown


NAME = re.compile(
    r"\b(?:Dr\.[ \t]+)?[A-Z][a-z]+(?:[ \t]+(?:of|the|[A-Z][a-z]+)){0,3}\b"
)
NAME_STOPWORDS = {
    "A", "After", "All", "And", "Are", "As", "At", "Before", "Being",
    "But", "By", "Despite", "Even", "For", "From", "He", "Her", "His",
    "Huh", "Humans", "I", "If", "In", "It", "Just", "Naturally", "Not",
    "On", "Or", "She", "Should", "So", "That", "The", "Then", "There",
    "They", "This", "Those", "Thou", "Thus", "To", "Uh", "Unable", "Wait",
    "What", "When", "While", "Why", "With", "Yet", "Yes", "You",
}


class AnalysisProvider(Protocol):
    """Contract for providers that propose entities and settings."""

    name: str

    def analyze(self, text: str, source_sha256: str) -> dict:
        """Return a versioned draft analysis tied to the source digest."""


@dataclass(frozen=True)
class ExtractiveAnalysisProvider:
    """Offline baseline that proposes review candidates without inventing facts."""

    name: str = "local-extractive-v1"
    minimum_mentions: int = 2

    def analyze(self, text: str, source_sha256: str) -> dict:
        """Extract repeated proper-name candidates and heading-based settings."""
        normalized = normalize_markdown(text)
        evidence: dict[str, list[dict]] = defaultdict(list)
        counts: Counter[str] = Counter()
        heading_ranges = [(match.start(), match.end()) for match in HEADING.finditer(normalized)]
        for match in NAME.finditer(normalized):
            if any(start <= match.start() < end for start, end in heading_ranges):
                continue
            value = match.group(0).strip()
            if value.split()[0] in NAME_STOPWORDS or len(value) < 2:
                continue
            counts[value] += 1
            if len(evidence[value]) < 5:
                evidence[value].append({"source_start": match.start(), "source_end": match.end()})
        candidates = []
        selected = sorted(
            (item for item in counts.items() if item[1] >= self.minimum_mentions),
            key=lambda item: (-item[1], item[0].casefold()),
        )
        for index, (name, count) in enumerate(selected, start=1):
            candidates.append({
                "entity_id": f"entity-candidate-{index:04d}",
                "name": name,
                "kind": "unknown",
                "mention_count": count,
                "evidence": evidence[name],
                "review_status": "needs_review",
            })
        settings = []
        for index, match in enumerate(HEADING.finditer(normalized), start=1):
            settings.append({
                "setting_id": f"setting-candidate-{index:04d}",
                "heading": match.group(2),
                "source_start": match.start(),
                "source_end": match.end(),
                "review_status": "needs_review",
            })
        return {
            "schema_version": 1,
            "analysis_id": "analysis-0001",
            "provider": self.name,
            "source_sha256": source_sha256,
            "status": "draft",
            "release_usable": False,
            "entities": candidates,
            "settings": settings,
            "world_rules": [],
            "continuity_facts": [],
        }


def validate_analysis(analysis: dict, source: dict) -> list[str]:
    """Return contract failures for a draft story analysis."""
    issues = []
    if analysis.get("schema_version") != 1:
        issues.append("unsupported analysis schema_version")
    if analysis.get("source_sha256") != source.get("sha256"):
        issues.append("analysis source hash does not match ingested source")
    if analysis.get("status") not in {"draft", "approved", "rejected"}:
        issues.append("analysis status is invalid")
    if analysis.get("status") != "approved" and analysis.get("release_usable"):
        issues.append("unapproved analysis cannot be release usable")
    identifiers = set()
    character_count = int(source.get("character_count", 0))
    for entity in analysis.get("entities", []):
        identifier = entity.get("entity_id")
        if not identifier or identifier in identifiers:
            issues.append("entity IDs must be nonempty and unique")
        identifiers.add(identifier)
        if entity.get("review_status") not in {"needs_review", "approved", "rejected"}:
            issues.append(f"invalid entity review status for {identifier}")
        for item in entity.get("evidence", []):
            start, end = item.get("source_start"), item.get("source_end")
            if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= character_count:
                issues.append(f"invalid source evidence for {identifier}")
    return issues
