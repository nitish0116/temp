"""Draft entity and setting analysis with auditable source evidence."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .artifacts import sha256_text
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
CANONICAL_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


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


def analysis_fingerprint(analysis: dict) -> str:
    """Bind review decisions to the exact ordered draft evidence."""
    import json

    return sha256_text(json.dumps(analysis, ensure_ascii=False, sort_keys=True))


def build_analysis_review_template(analysis: dict) -> dict:
    """Create a non-approving decision template for every draft candidate."""
    return {
        "schema_version": 1,
        "analysis_id": analysis["analysis_id"],
        "analysis_fingerprint": analysis_fingerprint(analysis),
        "source_sha256": analysis["source_sha256"],
        "reviewer": None,
        "reviewer_type": None,
        "reviewed_at": None,
        "entities": [{
            "entity_id": item["entity_id"], "status": "pending",
            "canonical_id": None, "canonical_name": item["name"],
            "kind": None, "aliases": [],
        } for item in analysis["entities"]],
        "settings": [{
            "setting_id": item["setting_id"], "status": "pending",
            "canonical_id": None, "canonical_name": item["heading"],
            "aliases": [],
        } for item in analysis["settings"]],
    }


def _review_timestamp(value: object) -> str:
    """Require an ISO-8601 decision timestamp with an explicit timezone."""
    if not isinstance(value, str):
        raise ValueError("reviewed_at must be a timezone-qualified ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("reviewed_at must be a timezone-qualified ISO-8601 string") from error
    if parsed.tzinfo is None:
        raise ValueError("reviewed_at must include a timezone")
    return value


def _decision_map(values: object, id_field: str, expected: set[str]) -> dict[str, dict]:
    """Validate an exact, duplicate-free decision set."""
    if not isinstance(values, list):
        raise ValueError(f"{id_field} decisions must be a list")
    decisions = {}
    for item in values:
        identifier = str(item.get(id_field) or "")
        if not identifier or identifier in decisions:
            raise ValueError(f"duplicate or missing {id_field} decision")
        decisions[identifier] = item
    if set(decisions) != expected:
        missing = sorted(expected - set(decisions))
        extra = sorted(set(decisions) - expected)
        raise ValueError(f"{id_field} decisions do not match draft; missing={missing}, extra={extra}")
    return decisions


def apply_analysis_decisions(analysis: dict, decisions: dict) -> dict:
    """Apply complete human decisions bound to an immutable draft analysis."""
    if decisions.get("schema_version") != 1:
        raise ValueError("unsupported analysis decision schema_version")
    if decisions.get("analysis_id") != analysis.get("analysis_id"):
        raise ValueError("analysis decision ID does not match draft")
    if decisions.get("source_sha256") != analysis.get("source_sha256"):
        raise ValueError("analysis decision source hash does not match draft")
    if decisions.get("analysis_fingerprint") != analysis_fingerprint(analysis):
        raise ValueError("analysis decisions are stale for the current draft")
    reviewer = str(decisions.get("reviewer") or "").strip()
    if not reviewer:
        raise ValueError("analysis reviewer is required")
    reviewed_at = _review_timestamp(decisions.get("reviewed_at"))
    reviewer_type = decisions.get("reviewer_type")
    if reviewer_type not in {"human", "model_assisted"}:
        raise ValueError("reviewer_type must be human or model_assisted")
    entity_decisions = _decision_map(
        decisions.get("entities"), "entity_id",
        {item["entity_id"] for item in analysis["entities"]},
    )
    setting_decisions = _decision_map(
        decisions.get("settings"), "setting_id",
        {item["setting_id"] for item in analysis["settings"]},
    )
    output = deepcopy(analysis)
    canonical_ids = set()
    for collection, decision_map, id_field in (
        (output["entities"], entity_decisions, "entity_id"),
        (output["settings"], setting_decisions, "setting_id"),
    ):
        for item in collection:
            decision = decision_map[item[id_field]]
            status = decision.get("status")
            if status not in {"approved", "rejected"}:
                raise ValueError(f"decision for {item[id_field]} must be approved or rejected")
            item["review_status"] = status
            if status == "approved":
                canonical_id = str(decision.get("canonical_id") or "").strip()
                canonical_name = str(decision.get("canonical_name") or "").strip()
                if not CANONICAL_ID.fullmatch(canonical_id) or not canonical_name:
                    raise ValueError(f"approved decision for {item[id_field]} requires canonical identity")
                if canonical_id in canonical_ids:
                    raise ValueError(f"duplicate canonical ID: {canonical_id}")
                canonical_ids.add(canonical_id)
                item["canonical_id"] = canonical_id
                item["canonical_name"] = canonical_name
                item["aliases"] = sorted({
                    str(value).strip() for value in decision.get("aliases", [])
                    if str(value).strip()
                })
                if id_field == "entity_id":
                    kind = str(decision.get("kind") or "").strip()
                    if kind not in {"character", "organization", "concept", "event", "other"}:
                        raise ValueError(f"approved entity {item[id_field]} requires a valid kind")
                    item["kind"] = kind
    output["status"] = "approved" if reviewer_type == "human" else "reviewed_draft"
    output["release_usable"] = reviewer_type == "human"
    output["planning_usable"] = True
    output["review"] = {
        "reviewer": reviewer, "reviewer_type": reviewer_type,
        "reviewed_at": reviewed_at,
    }
    return output


def validate_analysis(analysis: dict, source: dict) -> list[str]:
    """Return contract failures for a draft story analysis."""
    issues = []
    if analysis.get("schema_version") != 1:
        issues.append("unsupported analysis schema_version")
    if analysis.get("source_sha256") != source.get("sha256"):
        issues.append("analysis source hash does not match ingested source")
    if analysis.get("status") not in {"draft", "reviewed_draft", "approved", "rejected"}:
        issues.append("analysis status is invalid")
    if analysis.get("status") != "approved" and analysis.get("release_usable"):
        issues.append("unapproved analysis cannot be release usable")
    if analysis.get("status") == "reviewed_draft" and not analysis.get("planning_usable"):
        issues.append("reviewed draft must be planning usable")
    identifiers = set()
    canonical_ids = set()
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
        if entity.get("review_status") == "approved":
            canonical_id = entity.get("canonical_id")
            if not isinstance(canonical_id, str) or not CANONICAL_ID.fullmatch(canonical_id):
                issues.append(f"invalid canonical entity ID for {identifier}")
            elif canonical_id in canonical_ids:
                issues.append(f"duplicate canonical ID: {canonical_id}")
            canonical_ids.add(canonical_id)
    for setting in analysis.get("settings", []):
        identifier = setting.get("setting_id")
        if not identifier or identifier in identifiers:
            issues.append("setting IDs must be nonempty and unique")
        identifiers.add(identifier)
        if setting.get("review_status") not in {"needs_review", "approved", "rejected"}:
            issues.append(f"invalid setting review status for {identifier}")
        if setting.get("review_status") == "approved":
            canonical_id = setting.get("canonical_id")
            if not isinstance(canonical_id, str) or not CANONICAL_ID.fullmatch(canonical_id):
                issues.append(f"invalid canonical setting ID for {identifier}")
            elif canonical_id in canonical_ids:
                issues.append(f"duplicate canonical ID: {canonical_id}")
            canonical_ids.add(canonical_id)
    return issues
