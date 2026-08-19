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
REPEATED_SOURCE_CLAUSE = re.compile(r"(.{1,}?)(?:\s*[,;.!?、，]\s*\1){2,}", re.IGNORECASE)
BOUNDARY = re.compile(r"(?<=[.!?\u3002\uff01\uff1f\u061f\u0964\u2026])\s+|\s*(?=[,;:\u060c\u061b\uff0c\uff1b\uff1a])")
CLAUSE_SEPARATOR = re.compile(r"[.!?\u3002\uff01\uff1f\u061f\u0964\u2026,;:\u060c\u061b\uff0c\uff1b\uff1a]+")
LATIN_IDENTIFIER = re.compile(r"(?<![\w])(?:[A-Za-z][A-Za-z0-9]*(?:[-'][A-Za-z0-9]+)*)(?![\w])")
SMALL_NUMBERS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
)
TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
ORDINALS = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth",
    7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth", 11: "eleventh",
    12: "twelfth", 13: "thirteenth", 14: "fourteenth", 15: "fifteenth",
    16: "sixteenth", 17: "seventeenth", 18: "eighteenth", 19: "nineteenth",
    20: "twentieth",
}
ROMAN = {1: "i", 2: "ii", 3: "iii", 4: "iv", 5: "v", 6: "vi", 7: "vii", 8: "viii", 9: "ix", 10: "x"}


def english_number(value: int) -> str | None:
    """Return a normalized English rendering for subtitle-scale integers."""
    if 0 <= value < 20:
        return SMALL_NUMBERS[value]
    if value < 100:
        return TENS[value // 10] + (f" {SMALL_NUMBERS[value % 10]}" if value % 10 else "")
    if value < 1000:
        remainder = value % 100
        suffix = english_number(remainder) if remainder else ""
        return f"{SMALL_NUMBERS[value // 100]} hundred" + (f" {suffix}" if suffix else "")
    return None


def number_is_preserved(number: str, target_numbers: list[str], source: str, target: str) -> bool:
    """Accept a numeral when its digit or supported English form is retained.

    Example:: ``number_is_preserved("2", [], "2 people", "two people")`` is
    true, while replacing it with ``three`` is false.
    """
    if number in target_numbers:
        return True
    if not number.isdigit():
        return False
    words = english_number(int(number))
    normalized_target = re.sub(r"[^a-z]+", " ", target.casefold()).strip()
    value = int(number)
    equivalents = [words, ORDINALS.get(value), ROMAN.get(value)]
    if re.search(rf"{re.escape(number)}\s*[만万]", source) and value * 10_000 == 1_000_000:
        equivalents.append("million")
    return any(
        equivalent and re.search(rf"\b{re.escape(equivalent)}\b", normalized_target)
        for equivalent in equivalents
    )


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
    if any(not number_is_preserved(number, target_numbers, source, target) for number in source_numbers):
        issues.append({"type": "number_mismatch", "source": source_numbers, "target": target_numbers})
    source_length = len(re.sub(r"\s+", "", source))
    ratio = len(re.sub(r"\s+", "", target)) / max(1, source_length)
    # A single CJK glyph can legitimately expand into a multiword English subtitle.
    contains_cjk = bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", source))
    effective_maximum = (
        30.0 if source_length <= 4 else
        15.0 if source_length <= 8 else
        12.0 if contains_cjk and source_length <= 12 else
        maximum_length_ratio
    )
    if ratio < minimum_length_ratio:
        issues.append({"type": "translation_too_short", "ratio": round(ratio, 4)})
    if ratio > effective_maximum:
        issues.append({"type": "translation_too_long", "ratio": round(ratio, 4)})
    if REPEATED_CLAUSE.search(target) and not REPEATED_SOURCE_CLAUSE.search(source):
        issues.append({"type": "repeated_translation_clause"})
    return issues


def adjudication_coverage_issues(source: str, target: str) -> list[dict]:
    """Return deterministic clause and source-identifier omissions.

    This deliberately does not attempt cross-language named-entity recognition.
    It enforces observable structure and Latin-script identifiers, leaving
    semantic entity verification to independent evidence and reviewed references.
    """
    source_clauses = [part.strip() for part in CLAUSE_SEPARATOR.split(source) if part.strip()]
    target_clauses = [part.strip() for part in CLAUSE_SEPARATOR.split(target) if part.strip()]
    issues = []
    if len(source_clauses) > 1 and len(target_clauses) < len(source_clauses):
        issues.append({
            "type": "source_clause_omission",
            "source_clause_count": len(source_clauses),
            "target_clause_count": len(target_clauses),
        })
    source_identifiers = sorted({item.casefold() for item in LATIN_IDENTIFIER.findall(source)})
    target_folded = target.casefold()
    missing = [
        item for item in source_identifiers
        if not re.search(rf"(?<![a-z0-9]){re.escape(item)}(?![a-z0-9])", target_folded)
    ]
    if missing:
        issues.append({"type": "source_identifier_omission", "missing": missing})
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
