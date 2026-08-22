"""Source-linked narration planning and contract validation."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from .artifacts import sha256_text
from .source import normalize_markdown


PARAGRAPH = re.compile(r"(?ms)^\S.*?(?=\n[ \t]*\n|\Z)")
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


class NarrationProvider(Protocol):
    """Contract for providers that adapt bounded source blocks."""

    name: str

    def adapt(self, narration_id: str, source_text: str, entity_ids: list[str]) -> dict:
        """Return adapted text, tone, and referenced canonical entities."""


@dataclass(frozen=True)
class MappingNarrationProvider:
    """Deterministic provider backed by pre-generated adaptation responses."""

    responses: dict[str, dict]
    name: str = "mapping-provider-v1"

    def adapt(self, narration_id: str, source_text: str, entity_ids: list[str]) -> dict:
        """Return the exact response registered for a narration block."""
        del source_text, entity_ids
        if narration_id not in self.responses:
            raise ValueError(f"missing narration response: {narration_id}")
        return deepcopy(self.responses[narration_id])


def _canonical_terms(analysis: dict) -> dict[str, set[str]]:
    """Return approved canonical IDs and observable source names."""
    terms = {}
    for item in analysis.get("entities", []):
        if item.get("review_status") != "approved" or item.get("kind") not in {None, "character"}:
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
    section_starts = sorted(section["source_start"] for section in source.get("sections", []))

    def section_for(position: int) -> int:
        return sum(start <= position for start in section_starts) - 1

    for paragraph in paragraphs:
        proposed_start = current[0][0] if current else paragraph[0]
        crosses_section = current and section_for(current[0][0]) != section_for(paragraph[0])
        if current and (
            paragraph[1] - proposed_start > maximum_source_characters or crosses_section
        ):
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


def adapt_narration(
    plan: dict, text: str, provider: NarrationProvider,
) -> dict:
    """Adapt every planned block while enforcing bounded factual invariants."""
    normalized = normalize_markdown(text)
    output = deepcopy(plan)
    output["provider"] = provider.name
    output["status"] = "adapted_draft"
    output["release_usable"] = False
    issues = []
    for block in output["blocks"]:
        start, end = block["source_start"], block["source_end"]
        source_text = normalized[start:end]
        if sha256_text(source_text) != block["source_sha256"]:
            raise ValueError(f"source lineage changed for {block['narration_id']}")
        response = provider.adapt(
            block["narration_id"], source_text, list(block["canonical_entity_ids"]),
        )
        adapted = str(response.get("text") or "").strip()
        tone = str(response.get("tone") or "").strip()
        referenced = sorted({str(value) for value in response.get("canonical_entity_ids", [])})
        if not adapted or not tone:
            issues.append(f"empty adapted text or tone for {block['narration_id']}")
        unsupported_numbers = sorted(set(NUMBER.findall(adapted)) - set(NUMBER.findall(source_text)))
        if unsupported_numbers:
            issues.append(
                f"unsupported numbers for {block['narration_id']}: {unsupported_numbers}"
            )
        unsupported_entities = sorted(set(referenced) - set(block["canonical_entity_ids"]))
        if unsupported_entities:
            issues.append(
                f"unsupported entities for {block['narration_id']}: {unsupported_entities}"
            )
        ratio = len(adapted) / max(1, len(source_text.strip()))
        if not 0.08 <= ratio <= 1.20:
            issues.append(f"unsafe adaptation length ratio for {block['narration_id']}: {ratio:.3f}")
        block.update({
            "adapted_text": adapted, "tone": tone,
            "canonical_entity_ids": referenced,
            "status": "adapted_draft", "fidelity": {
                "length_ratio": ratio, "unsupported_numbers": unsupported_numbers,
                "unsupported_entities": unsupported_entities,
            },
        })
    if issues:
        raise ValueError("invalid narration adaptation: " + "; ".join(issues))
    return output


def build_narration_response_template(plan: dict) -> dict:
    """Create a complete non-adapting provider-response template."""
    return {
        "schema_version": 1,
        "narration_plan_id": plan["narration_plan_id"],
        "source_sha256": plan["source_sha256"],
        "provider": "pending-provider",
        "responses": [{
            "narration_id": block["narration_id"],
            "text": None,
            "tone": None,
            "canonical_entity_ids": list(block["canonical_entity_ids"]),
        } for block in plan["blocks"]],
    }


def validate_adapted_narration(narration: dict, plan: dict) -> list[str]:
    """Validate complete adapted output against immutable plan lineage."""
    issues = []
    if narration.get("status") != "adapted_draft" or narration.get("release_usable"):
        issues.append("adapted narration must remain a non-release draft")
    planned = {item["narration_id"]: item for item in plan.get("blocks", [])}
    adapted = {item.get("narration_id"): item for item in narration.get("blocks", [])}
    if set(adapted) != set(planned):
        issues.append("adapted narration IDs do not exactly match the plan")
        return issues
    for identifier, block in adapted.items():
        original = planned[identifier]
        for field in ("source_start", "source_end", "source_sha256"):
            if block.get(field) != original.get(field):
                issues.append(f"adaptation changed {field} for {identifier}")
        if block.get("status") != "adapted_draft" or not str(block.get("adapted_text") or "").strip():
            issues.append(f"invalid adapted narration state for {identifier}")
        if set(block.get("canonical_entity_ids", [])) - set(original.get("canonical_entity_ids", [])):
            issues.append(f"adaptation introduced an unsupported entity for {identifier}")
    return issues
