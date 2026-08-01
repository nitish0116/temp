#!/usr/bin/env python3
"""Resolve ambiguous hyphens using punctuation evidence from library variants."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
import re

from md_audio import review_records


WORD = r"[^\W\d_]+"
PUNCTUATED_PAIR_RE = re.compile(
    rf"(?P<left>{WORD})\s*(?P<separator>[,—;:])\s*(?P<right>{WORD})",
    re.UNICODE,
)
WORD_RE = re.compile(WORD, re.UNICODE)
SEPARATOR_NAMES = {",": "comma", "—": "em dash", ";": "semicolon", ":": "colon"}
PunctuationKey = tuple[str, str, tuple[str, ...], tuple[str, ...]]
PunctuationIndex = dict[PunctuationKey, Counter[str]]


def context_signature(
    text: str, start: int, end: int, left: str, right: str
) -> PunctuationKey:
    """Identify a word pair using up to two surrounding words on each side."""
    before = [match.group(0).casefold() for match in WORD_RE.finditer(text[:start])][-2:]
    after = [match.group(0).casefold() for match in WORD_RE.finditer(text[end:])][:2]
    return left.casefold(), right.casefold(), tuple(before), tuple(after)


def build_punctuation_index(
    library: Path,
) -> PunctuationIndex:
    """Index punctuated pairs plus surrounding words from every Markdown line."""
    index: PunctuationIndex = defaultdict(Counter)
    for path in sorted(library.rglob("*.md")):
        for line in path.read_text(encoding="utf-8").splitlines():
            for match in PUNCTUATED_PAIR_RE.finditer(line):
                key = context_signature(
                    line,
                    match.start(),
                    match.end(),
                    match.group("left"),
                    match.group("right"),
                )
                index[key][match.group("separator")] += 1
    return index


def classify_with_evidence(
    candidate: dict[str, object],
    punctuation_index: PunctuationIndex,
) -> dict[str, object] | None:
    """Return a replacement decision when another source preserves punctuation."""
    token = candidate.get("token")
    if not isinstance(token, str):
        return None
    parts = token.split("-")
    if len(parts) != 2:
        return None
    combined_evidence: Counter[str] = Counter()
    for context in candidate.get("contexts", []):
        text = context.get("text", "") if isinstance(context, dict) else ""
        match = re.search(re.escape(token), text, re.IGNORECASE)
        if not match:
            continue
        signature = context_signature(text, match.start(), match.end(), parts[0], parts[1])
        combined_evidence.update(punctuation_index.get(signature, {}))
    if not combined_evidence:
        return None
    separator, count = combined_evidence.most_common(1)[0]
    decided = dict(candidate)
    decided.update(
        decision="not_acceptable",
        status="replace",
        replacement=f"{parts[0]}, {parts[1]}",
        confidence=0.99,
        decision_reason=(
            f"cross-library source evidence: same word pair occurs with "
            f"{SEPARATOR_NAMES[separator]} ({count} occurrence(s))"
        ),
        evidence={"separator": separator, "count": count},
    )
    return decided


def classify_record(
    ambiguous: dict[str, object],
    punctuation_index: PunctuationIndex,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Split an ambiguous record into evidence-resolved and unresolved entries."""
    resolved: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []
    for candidate in ambiguous.get("candidates", []):
        decision = classify_with_evidence(candidate, punctuation_index)
        if decision is None:
            unresolved.append(candidate)
        else:
            resolved.append(decision)
    return resolved, unresolved


def merge_decisions(main_record: dict[str, object], resolved: list[dict[str, object]]) -> None:
    """Merge newly resolved tokens into the decided review record without duplicates."""
    candidates = main_record.setdefault("candidates", [])
    existing = {
        item.get("token", "").casefold()
        for item in candidates
        if isinstance(item, dict) and isinstance(item.get("token"), str)
    }
    for item in resolved:
        if item["token"].casefold() not in existing:
            candidates.append(item)
            existing.add(item["token"].casefold())
    main_record["classified_candidates"] = len(candidates)
    main_record["cross_evidence_resolved"] = len(resolved)
    main_record["ambiguous_candidates"] = max(
        0, int(main_record.get("ambiguous_candidates", 0)) - len(resolved)
    )


def parse_args() -> argparse.Namespace:
    """Parse cross-evidence classifier command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ambiguous_json", type=Path)
    parser.add_argument("library_folder", type=Path)
    parser.add_argument("main_review_json", type=Path)
    parser.add_argument(
        "--remaining-output",
        type=Path,
        help="Output for unresolved entries; defaults to overwriting ambiguous_json",
    )
    parser.add_argument(
        "--resolved-output",
        type=Path,
        help="Optional standalone record of evidence-resolved entries",
    )
    return parser.parse_args()


def load_record(path: Path) -> dict[str, object]:
    """Load and validate a hyphen-review record."""
    return review_records.load_review_record(path)


def write_record(path: Path, record: dict[str, object]) -> None:
    """Write a hyphen-review record as formatted UTF-8 JSON."""
    review_records.write_review_record(path, record)


def main() -> int:
    """Resolve candidates with punctuation evidence and update review files."""
    args = parse_args()
    ambiguous = load_record(args.ambiguous_json)
    main_review = load_record(args.main_review_json)
    if not args.library_folder.is_dir():
        raise SystemExit(f"Library folder does not exist: {args.library_folder}")

    punctuation_index = build_punctuation_index(args.library_folder)
    resolved, unresolved = classify_record(ambiguous, punctuation_index)
    merge_decisions(main_review, resolved)

    generated_at = review_records.utc_timestamp()
    remaining = dict(ambiguous)
    remaining["generated_at"] = generated_at
    remaining["ambiguous_candidates"] = len(unresolved)
    remaining["cross_evidence_resolved"] = len(resolved)
    remaining["candidates"] = unresolved

    write_record(args.main_review_json, main_review)
    write_record(args.remaining_output or args.ambiguous_json, remaining)
    if args.resolved_output:
        write_record(
            args.resolved_output,
            {
                "schema_version": 1,
                "generated_at": generated_at,
                "resolved_candidates": len(resolved),
                "candidates": resolved,
            },
        )
    print(
        f"Resolved {len(resolved)} entries from cross-library punctuation evidence; "
        f"{len(unresolved)} remain ambiguous."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
