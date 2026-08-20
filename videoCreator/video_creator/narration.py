"""Source-linked narration planning and contract validation."""

from __future__ import annotations

import re

from .artifacts import sha256_text
from .source import normalize_markdown


PARAGRAPH = re.compile(r"(?ms)^\S.*?(?=\n[ \t]*\n|\Z)")


def _canonical_terms(analysis: dict) -> dict[str, set[str]]:
    """Return approved canonical IDs and observable source names."""
    terms = {}
    for item in analysis.get("entities", []):
        if item.get("review_status") != "approved":
            continue
        values = {str(item.get("name") or ""), str(item.get("canonical_name") or "")}
        values.update(str(value) for value in item.get("aliases", []))
        terms[item["canonical_id"]] = {value.casefold() for value in values if value}
    return terms


def build_narration_plan(
    text: str, source: dict, analysis: dict, *, maximum_source_characters: int = 2400,
) -> dict:
    """Group source paragraphs into bounded blocks awaiting spoken adaptation."""
    if maximum_source_characters < 200:
        raise ValueError("maximum_source_characters must be at least 200")
    normalized = normalize_markdown(text)
    if sha256_text(normalized) != source.get("sha256"):
        raise ValueError("narration manuscript does not match ingested source")
    if not analysis.get("planning_usable"):
        raise ValueError("narration planning requires reviewed canonical entities")
    paragraphs = [
        (match.start(), match.end()) for match in PARAGRAPH.finditer(normalized)
        if not match.group(0).lstrip().startswith("#")
    ]
    groups: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = []
    for paragraph in paragraphs:
        proposed_start = current[0][0] if current else paragraph[0]
        if current and paragraph[1] - proposed_start > maximum_source_characters:
            groups.append(current)
            current = []
        current.append(paragraph)
    if current:
        groups.append(current)
    terms = _canonical_terms(analysis)
    blocks = []
    for index, group in enumerate(groups, start=1):
        start, end = group[0][0], group[-1][1]
        source_slice = normalized[start:end]
        folded = source_slice.casefold()
        entities = sorted(
            canonical_id for canonical_id, aliases in terms.items()
            if any(alias in folded for alias in aliases)
        )
        blocks.append({
            "narration_id": f"narration-{index:04d}",
            "source_start": start,
            "source_end": end,
            "source_sha256": sha256_text(source_slice),
            "source_paragraph_count": len(group),
            "canonical_entity_ids": entities,
            "adapted_text": None,
            "tone": None,
            "status": "pending_adaptation",
        })
    return {
        "schema_version": 1,
        "narration_plan_id": "narration-plan-0001",
        "source_sha256": source["sha256"],
        "analysis_id": analysis["analysis_id"],
        "analysis_status": analysis["status"],
        "status": "planned",
        "release_usable": False,
        "maximum_source_characters": maximum_source_characters,
        "blocks": blocks,
    }


def validate_narration_plan(plan: dict, source: dict, analysis: dict) -> list[str]:
    """Return source-lineage and identity-reference contract failures."""
    issues = []
    if plan.get("schema_version") != 1:
        issues.append("unsupported narration plan schema_version")
    if plan.get("source_sha256") != source.get("sha256"):
        issues.append("narration plan source hash mismatch")
    if plan.get("analysis_id") != analysis.get("analysis_id"):
        issues.append("narration plan analysis ID mismatch")
    if plan.get("status") != "planned" or plan.get("release_usable"):
        issues.append("narration plan must remain non-release planned evidence")
    valid_entities = {
        item["canonical_id"] for item in analysis.get("entities", [])
        if item.get("review_status") == "approved"
    }
    previous_end = 0
    identifiers = set()
    for block in plan.get("blocks", []):
        identifier = block.get("narration_id")
        if not identifier or identifier in identifiers:
            issues.append("narration IDs must be nonempty and unique")
        identifiers.add(identifier)
        start, end = block.get("source_start"), block.get("source_end")
        if not isinstance(start, int) or not isinstance(end, int) or start < previous_end or end <= start:
            issues.append(f"invalid narration source range for {identifier}")
        elif end > int(source.get("character_count", 0)):
            issues.append(f"narration source range exceeds source for {identifier}")
        previous_end = end if isinstance(end, int) else previous_end
        unknown = set(block.get("canonical_entity_ids", [])) - valid_entities
        if unknown:
            issues.append(f"unknown canonical entities for {identifier}: {sorted(unknown)}")
        if block.get("status") != "pending_adaptation" or block.get("adapted_text") is not None:
            issues.append(f"unadapted narration block has invalid state: {identifier}")
    if not plan.get("blocks"):
        issues.append("narration plan requires at least one block")
    return issues
