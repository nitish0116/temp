"""Markdown source ingestion with stable range lineage."""

from __future__ import annotations

import re
from pathlib import Path

from .artifacts import sha256_text


HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def normalize_markdown(text: str) -> str:
    """Normalize newlines without rewriting manuscript content."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def ingest_markdown(path: Path) -> dict:
    """Create a versioned source artifact from a Markdown manuscript."""
    text = normalize_markdown(path.read_text(encoding="utf-8-sig"))
    if not text.strip():
        raise ValueError("source manuscript is empty")
    matches = list(HEADING.finditer(text))
    sections = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append({
            "section_id": f"sec-{index + 1:04d}",
            "level": len(match.group(1)),
            "title": match.group(2),
            "source_start": match.start(),
            "source_end": end,
            "text_sha256": sha256_text(text[match.start():end]),
        })
    if not sections:
        sections.append({
            "section_id": "sec-0001", "level": 0, "title": path.stem,
            "source_start": 0, "source_end": len(text),
            "text_sha256": sha256_text(text),
        })
    return {
        "schema_version": 1,
        "source_id": "source-0001",
        "filename": path.name,
        "format": "markdown",
        "character_count": len(text),
        "line_count": len(text.splitlines()),
        "sha256": sha256_text(text),
        "sections": sections,
    }


def validate_source(source: dict) -> list[str]:
    """Return contract failures for a source artifact."""
    issues = []
    if source.get("schema_version") != 1:
        issues.append("unsupported source schema_version")
    if not source.get("sha256"):
        issues.append("source sha256 is required")
    sections = source.get("sections")
    if not isinstance(sections, list) or not sections:
        issues.append("source requires at least one section")
        return issues
    previous_end = 0
    identifiers = set()
    for section in sections:
        identifier = section.get("section_id")
        if not identifier or identifier in identifiers:
            issues.append("section IDs must be nonempty and unique")
        identifiers.add(identifier)
        start, end = section.get("source_start"), section.get("source_end")
        if not isinstance(start, int) or not isinstance(end, int) or start < previous_end or end <= start:
            issues.append(f"invalid or overlapping source range for {identifier}")
        elif end > int(source.get("character_count", 0)):
            issues.append(f"source range exceeds content for {identifier}")
        previous_end = end if isinstance(end, int) else previous_end
    return issues
