"""Validate semantic-group translations and apply bounded automatic recovery."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Callable

try:
    from .canonical_timed_text import append_provenance, validate_canonical_timed_text
except ImportError:
    from canonical_timed_text import append_provenance, validate_canonical_timed_text


NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
REPEATED_CLAUSE = re.compile(r"(.{2,}?)(?:\s*[,;.!?]\s*\1){2,}", re.IGNORECASE)
BOUNDARY = re.compile(r"(?<=[.!?\u3002\uff01\uff1f\u061f\u0964\u2026])\s+|\s*(?=[,;:\u060c\u061b\uff0c\uff1b\uff1a])")


def integrity_issues(
    source: str, target: str, *, minimum_length_ratio: float = 0.15,
    maximum_length_ratio: float = 6.0,
) -> list[dict]:
    """Return deterministic completeness, number, density, and repetition faults."""
    source, target = source.strip(), target.strip()
    issues = []
    if not target:
        return [{"type": "empty_translation"}]
    source_numbers, target_numbers = NUMBER.findall(source), NUMBER.findall(target)
    if source_numbers != target_numbers:
        issues.append({"type": "number_mismatch", "source": source_numbers, "target": target_numbers})
    ratio = len(re.sub(r"\s+", "", target)) / max(1, len(re.sub(r"\s+", "", source)))
    if ratio < minimum_length_ratio:
        issues.append({"type": "translation_too_short", "ratio": round(ratio, 4)})
    if ratio > maximum_length_ratio:
        issues.append({"type": "translation_too_long", "ratio": round(ratio, 4)})
    if REPEATED_CLAUSE.search(target):
        issues.append({"type": "repeated_translation_clause"})
    return issues


def semantic_pieces(text: str) -> list[str]:
    """Split a failed group at linguistic boundaries for a final recovery route."""
    pieces = [piece.strip(" ,;:") for piece in BOUNDARY.split(text) if piece.strip(" ,;:")]
    return pieces if len(pieces) > 1 else [text.strip()]


def enforce_translation_integrity(
    document: dict,
    retry_translate: Callable[[str, dict], str],
    *,
    maximum_retries: int = 1,
) -> tuple[dict, dict]:
    """Retry invalid groups, then translate subgroups, without silent promotion."""
    validate_canonical_timed_text(document)
    if document["stage"] != "translated":
        raise ValueError("Translation integrity requires a translated artifact")
    output = deepcopy(document)
    results = []
    for segment in output["segments"]:
        source = segment["source_text"] or ""
        target = segment["translated_text"] or ""
        issues = integrity_issues(source, target)
        attempts = []
        for attempt in range(1, maximum_retries + 1):
            if not issues:
                break
            target = retry_translate(source, {
                "route": "retry", "attempt": attempt, "issues": deepcopy(issues),
                "source_language": output["source_language"],
                "target_language": output["output_language"],
            }).strip()
            issues = integrity_issues(source, target)
            attempts.append({"route": "retry", "attempt": attempt, "issues": deepcopy(issues)})
        if issues:
            pieces = semantic_pieces(source)
            if len(pieces) > 1:
                translated_pieces = [
                    retry_translate(piece, {
                        "route": "semantic-resegmentation", "piece": index,
                        "piece_count": len(pieces), "issues": deepcopy(issues),
                        "source_language": output["source_language"],
                        "target_language": output["output_language"],
                    }).strip()
                    for index, piece in enumerate(pieces, start=1)
                ]
                candidate = " ".join(piece for piece in translated_pieces if piece)
                candidate_issues = integrity_issues(source, candidate)
                attempts.append({"route": "semantic-resegmentation", "pieces": len(pieces), "issues": deepcopy(candidate_issues)})
                if not candidate_issues:
                    target, issues = candidate, candidate_issues
        passed = not issues
        if passed:
            segment["translated_text"] = target
        segment["provenance"] = append_provenance(
            segment, "translation-integrity", "bounded-retry-and-resegmentation",
            passed=passed, attempts=len(attempts), issues=deepcopy(issues),
        )
        results.append({
            "semantic_group_id": segment["semantic_group_id"],
            "passed": passed, "issues": issues, "attempts": attempts,
        })
    report = {
        "schema_version": 1,
        "passed": all(item["passed"] for item in results),
        "group_count": len(results),
        "failed_group_count": sum(not item["passed"] for item in results),
        "results": results,
    }
    output["metadata"] = {**output.get("metadata", {}), "translation_integrity": report}
    validate_canonical_timed_text(output)
    return output, report
