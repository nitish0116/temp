"""Evaluate canonical subtitles against optional human-verified semantic references."""

from __future__ import annotations

import json
import re
from pathlib import Path

try:
    from .canonical_timed_text import validate_canonical_timed_text
except ImportError:
    from canonical_timed_text import validate_canonical_timed_text


def normalized(text: str) -> str:
    """Normalize case and spacing while retaining meaningful punctuation content.

    Example:: ``normalized("  New   York ")`` returns ``"new york"``.
    """
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


def cue_at(document: dict, timestamp: float) -> dict | None:
    """Return the closest canonical cue containing a reference timestamp.

    Example:: timestamp ``2.0`` selects a cue spanning ``1.5`` through ``2.5``;
    a timestamp in silence returns ``None``.
    """
    candidates = [
        cue for cue in document.get("segments", [])
        if float(cue["start"]) <= timestamp <= float(cue["end"])
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda cue: abs((float(cue["start"]) + float(cue["end"])) / 2 - timestamp),
    )


def semantic_group_text(document: dict, cue: dict) -> str:
    """Join every display cue belonging to the selected semantic translation unit.

    Example:: two display cues from ``semantic-0001`` are checked as one sentence,
    so a required name in the second cue still satisfies the reference.
    """
    group_id = cue.get("semantic_group_id")
    return " ".join(
        str(item.get("translated_text") or item.get("source_text") or "").strip()
        for item in document.get("segments", [])
        if item.get("semantic_group_id") == group_id
    ).strip()


def references_from_manifest(manifest: dict, output_directory: str | None = None) -> list[dict]:
    """Read references from a sidecar or select one sample from a review manifest.

    Example:: a sidecar uses top-level ``references``; the project regression
    fixture uses ``samples`` and is selected by ``output_directory``.
    """
    if isinstance(manifest.get("references"), list):
        return manifest["references"]
    for sample in manifest.get("samples", []):
        if output_directory is None or sample.get("output_directory") == output_directory:
            return sample.get("manual_review", {}).get("verified_defects", [])
    return []


def evaluate_semantic_references(document: dict, references: list[dict]) -> dict:
    """Return a blocking report for missing cues and required/forbidden terms.

    Example:: requiring ``Seoul`` while forbidding ``Seattle`` rejects a cue
    containing ``Seattle also had such a place?``.
    """
    validate_canonical_timed_text(document)
    checks = []
    for index, reference in enumerate(references, start=1):
        timestamp = float(reference["timestamp_seconds"])
        cue = cue_at(document, timestamp)
        text = "" if cue is None else semantic_group_text(document, cue)
        comparable = normalized(text)
        missing = [
            term for term in reference.get("required_terms", [])
            if normalized(term) not in comparable
        ]
        forbidden = [
            term for term in reference.get("forbidden_terms", [])
            if normalized(term) in comparable
        ]
        issues = []
        if cue is None:
            issues.append("missing_cue_at_reference_timestamp")
        if missing:
            issues.append("missing_required_terms")
        if forbidden:
            issues.append("forbidden_terms_present")
        checks.append({
            "reference": index,
            "timestamp_seconds": timestamp,
            "cue_id": None if cue is None else cue.get("id"),
            "semantic_group_id": None if cue is None else cue.get("semantic_group_id"),
            "generated_text": text,
            "required_terms": reference.get("required_terms", []),
            "forbidden_terms": reference.get("forbidden_terms", []),
            "missing_required_terms": missing,
            "present_forbidden_terms": forbidden,
            "issues": issues,
            "passed": not issues,
        })
    return {
        "schema_version": 1,
        "evaluated": bool(references),
        "passed": all(check["passed"] for check in checks),
        "reference_count": len(checks),
        "failed_reference_count": sum(not check["passed"] for check in checks),
        "checks": checks,
    }


def evaluate_manifest(document: dict, manifest_path: Path, output_directory: str) -> dict:
    """Load a semantic sidecar and evaluate references for one output directory.

    Example:: ``evaluate_manifest(document, Path("review.json"), "episode-01")``
    returns an auditable report suitable for a headless promotion gate.
    """
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    references = references_from_manifest(manifest, output_directory)
    if not references:
        raise ValueError(
            f"No semantic references found for output directory {output_directory!r}"
        )
    return evaluate_semantic_references(document, references)
