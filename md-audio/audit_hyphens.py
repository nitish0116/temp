#!/usr/bin/env python3
"""Build a review record for word-to-word hyphens in Markdown narration."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re


HYPHENATED_RE = re.compile(r"\b[^\W\d_]+(?:-[^\W\d_]+)+\b", re.UNICODE)
STUTTER_RE = re.compile(r"^(?P<lead>[A-Za-z]{1,2})-(?P<word>[A-Za-z]+)$")

# Intentionally conservative. Add a token only after checking it in context.
DEFAULT_GENUINE_HYPHENS = {
    "all-but",
    "as-is",
    "day-to-day",
    "face-to-face",
    "goings-on",
    "half-sigh",
    "hands-on",
    "know-how",
    "matter-of-fact",
    "once-over",
    "passer-by",
    "run-of-the-mill",
    "self-control",
    "well-being",
    "and-or",
    "either-or",
    "has-been",
    "must-have",
    "would-be",
}

BOUNDARY_WORDS = {
    "a", "an", "the",
    "and", "but", "or", "so", "yet", "nor",
    "i", "you", "he", "she", "we", "they", "me", "him", "her", "us", "them",
    "this", "that", "these", "those", "who", "which", "what", "when", "where",
    "why", "how", "because", "although", "however",
    "am", "is", "are", "was", "were", "have", "has", "had", "do", "does", "did",
    "can", "could", "will", "would", "shall", "should", "may", "might", "must", "not",
}
GENUINE_PREFIXES = {"anti", "co", "ex", "non", "pre", "pseudo", "self", "semi"}
GENUINE_SUFFIXES = {
    "class", "classer", "classers", "dimensional", "edged", "eyed", "faced",
    "fashioned", "folk", "handed", "hearted", "level", "like", "minded",
    "pitched", "quality", "range", "ranked", "related", "shaped", "sized", "term",
}
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def portable_path(path: Path, base: Path = REPOSITORY_ROOT) -> str:
    """Return a relative metadata path without embedding a machine-specific root."""
    try:
        return Path(os.path.relpath(path.resolve(), base.resolve())).as_posix()
    except ValueError:
        return path.name


def is_stutter(token: str) -> bool:
    """Return whether a token is an explicit speech stutter such as ``w-what``."""
    match = STUTTER_RE.fullmatch(token)
    if not match:
        return False
    return match.group("word").casefold().startswith(match.group("lead").casefold())


def canonical_markdown_files(root: Path) -> list[Path]:
    """Prefer one full-volume Markdown file per folder to avoid chapter duplicates."""
    files = sorted(root.rglob("*.md"))
    by_parent: dict[Path, list[Path]] = defaultdict(list)
    for path in files:
        by_parent[path.parent].append(path)

    selected: list[Path] = []
    for parent, children in sorted(by_parent.items()):
        volume = next(
            (path for path in children if path.stem.casefold() == parent.name.casefold()),
            None,
        )
        selected.append(volume or children[0])
    return selected


def unique_markdown_files(root: Path) -> tuple[list[Path], int]:
    """Return every recursive Markdown file, excluding byte-identical copies."""
    selected: list[Path] = []
    seen_digests: set[bytes] = set()
    duplicate_count = 0
    for path in sorted(root.rglob("*.md")):
        digest = hashlib.sha256(path.read_bytes()).digest()
        if digest in seen_digests:
            duplicate_count += 1
            continue
        seen_digests.add(digest)
        selected.append(path)
    return selected, duplicate_count


def context_for_line(line: str, start: int, limit: int = 240) -> str:
    """Return compact line context centered around a match."""
    context = " ".join(line.split())
    if len(context) <= limit:
        return context
    left = max(0, start - limit // 2)
    return context[left : left + limit].strip()


def load_genuine_hyphens(path: Path | None) -> set[str]:
    """Load optional reviewed genuine tokens and combine them with defaults."""
    genuine = {token.casefold() for token in DEFAULT_GENUINE_HYPHENS}
    if path is None:
        return genuine
    value = json.loads(path.read_text(encoding="utf-8"))
    tokens = value.get("genuine", value) if isinstance(value, dict) else value
    if not isinstance(tokens, list) or not all(isinstance(token, str) for token in tokens):
        raise ValueError("The genuine-hyphens file must be a JSON list or a 'genuine' list.")
    genuine.update(token.casefold() for token in tokens)
    return genuine


def classify_token(token: str) -> dict[str, object]:
    """Classify a hyphen token using conservative, explainable rules."""
    parts = token.split("-")
    folded = [part.casefold() for part in parts]

    if len(parts) == 2 and folded[0] == folded[1]:
        return {
            "decision": "acceptable",
            "status": "genuine",
            "replacement": None,
            "confidence": 0.99,
            "decision_reason": "repeated-word expression",
        }
    if len(parts) == 2 and folded[0] in GENUINE_PREFIXES:
        return {
            "decision": "acceptable",
            "status": "genuine",
            "replacement": None,
            "confidence": 0.95,
            "decision_reason": "productive hyphenated prefix",
        }
    if len(parts) == 2 and folded[-1] in GENUINE_SUFFIXES:
        return {
            "decision": "acceptable",
            "status": "genuine",
            "replacement": None,
            "confidence": 0.95,
            "decision_reason": "productive compound suffix",
        }
    if len(parts) == 2 and folded[-1] in BOUNDARY_WORDS:
        return {
            "decision": "not_acceptable",
            "status": "replace",
            "replacement": f"{parts[0]}, {parts[1]}",
            "confidence": 0.97,
            "decision_reason": "probable lost punctuation before sentence-bridge word",
        }
    return {
        "decision": "undecided",
        "status": "review",
        "replacement": None,
        "confidence": 0.0,
        "decision_reason": "compound and punctuation boundary are both plausible",
    }


def auto_classify(audit: dict[str, object]) -> dict[str, int]:
    """Annotate every candidate with an automatic decision and return totals."""
    totals = {"acceptable": 0, "not_acceptable": 0, "undecided": 0}
    for candidate in audit["candidates"]:
        result = classify_token(candidate["token"])
        candidate.update(result)
        totals[result["decision"]] += 1
    audit["automatic_classification"] = totals
    audit["instructions"] = (
        "Automatic decisions include a reason and confidence. Only status 'replace' "
        "has replacement text. Status 'review' remains inert and needs human judgment."
    )
    return totals


def move_ambiguous_entries(audit: dict[str, object]) -> dict[str, object]:
    """Remove undecided entries from an audit and return a companion record."""
    ambiguous = [
        candidate
        for candidate in audit["candidates"]
        if candidate.get("decision") == "undecided"
    ]
    audit["candidates"] = [
        candidate
        for candidate in audit["candidates"]
        if candidate.get("decision") != "undecided"
    ]
    audit["classified_candidates"] = len(audit["candidates"])
    audit["ambiguous_candidates"] = len(ambiguous)
    audit["review_candidates"] = 0
    return {
        "schema_version": audit["schema_version"],
        "generated_at": audit["generated_at"],
        "source_root": audit["source_root"],
        "instructions": (
            "These entries were not automatically decided. Review each entry, then "
            "move approved replacements into the main review record with status 'replace'."
        ),
        "ambiguous_candidates": len(ambiguous),
        "candidates": ambiguous,
    }


def build_audit(
    root: Path,
    genuine: set[str],
    max_contexts: int = 3,
    all_files: bool = False,
) -> dict[str, object]:
    """Scan canonical Markdown files and return a deterministic review record."""
    discovered_files = list(root.rglob("*.md"))
    if all_files:
        files, identical_duplicates_skipped = unique_markdown_files(root)
        selection_mode = "all unique files"
    else:
        files = canonical_markdown_files(root)
        identical_duplicates_skipped = 0
        selection_mode = "canonical file per folder"
    occurrences: Counter[str] = Counter()
    variants: dict[str, Counter[str]] = defaultdict(Counter)
    contexts: dict[str, list[dict[str, object]]] = defaultdict(list)

    for path in files:
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for match in HYPHENATED_RE.finditer(line):
                token = match.group(0)
                key = token.casefold()
                occurrences[key] += 1
                variants[key][token] += 1
                if len(contexts[key]) < max_contexts:
                    contexts[key].append(
                        {
                            "file": str(path.relative_to(root)),
                            "line": line_number,
                            "text": context_for_line(line, match.start()),
                        }
                    )

    return assemble_audit(
        root,
        genuine,
        occurrences,
        variants,
        contexts,
        selection_mode,
        len(discovered_files),
        len(files),
        identical_duplicates_skipped,
    )


def assemble_audit(
    root: Path,
    genuine: set[str],
    occurrences: Counter[str],
    variants: dict[str, Counter[str]],
    contexts: dict[str, list[dict[str, object]]],
    selection_mode: str,
    discovered_count: int,
    scanned_count: int,
    duplicate_count: int,
) -> dict[str, object]:
    """Assemble a review record from cached or freshly scanned occurrences."""
    excluded = []
    candidates = []
    for key in sorted(occurrences, key=lambda item: (-occurrences[item], item)):
        common = variants[key].most_common(1)[0][0]
        if key in genuine or is_stutter(common):
            excluded.append(
                {
                    "token": common,
                    "count": occurrences[key],
                    "reason": "reviewed genuine" if key in genuine else "speech stutter",
                }
            )
            continue
        candidates.append(
            {
                "token": common,
                "count": occurrences[key],
                "status": "review",
                "replacement": None,
                "variants": sorted(variants[key]),
                "contexts": contexts[key],
            }
        )

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_root": portable_path(root),
        "selection_mode": selection_mode,
        "markdown_files_discovered": discovered_count,
        "files_scanned": scanned_count,
        "identical_duplicates_skipped": duplicate_count,
        "unique_hyphenated_forms": len(occurrences),
        "review_candidates": len(candidates),
        "excluded_genuine": len(excluded),
        "instructions": (
            "Set status to 'replace' and replacement to reviewed narration text. "
            "Set status to 'genuine' for valid hyphenation. Unreviewed entries are inert."
        ),
        "candidates": candidates,
        "excluded": excluded,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_folder", type=Path, help="Folder containing cleaned Markdown")
    parser.add_argument("output_json", type=Path, help="Review record to create")
    parser.add_argument(
        "--all-files",
        action="store_true",
        help="Scan every recursive Markdown file, skipping only byte-identical copies",
    )
    parser.add_argument(
        "--genuine-hyphens",
        type=Path,
        help="Optional JSON list of additional reviewed genuine tokens",
    )
    parser.add_argument(
        "--auto-classify",
        action="store_true",
        help="Assign explainable genuine/replace/review decisions to candidates",
    )
    parser.add_argument(
        "--ambiguous-output",
        type=Path,
        help="Companion JSON for undecided entries (defaults beside output JSON)",
    )
    parser.add_argument("--max-contexts", type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.input_folder.is_dir():
        raise SystemExit(f"Input folder does not exist: {args.input_folder}")
    if args.max_contexts < 1:
        raise SystemExit("--max-contexts must be at least 1")
    genuine = load_genuine_hyphens(args.genuine_hyphens)
    audit = build_audit(
        args.input_folder,
        genuine,
        args.max_contexts,
        all_files=args.all_files,
    )
    ambiguous_audit = None
    ambiguous_output = None
    if args.auto_classify:
        auto_classify(audit)
        ambiguous_audit = move_ambiguous_entries(audit)
        ambiguous_output = args.ambiguous_output or args.output_json.with_name(
            f"{args.output_json.stem}-ambiguous{args.output_json.suffix}"
        )
        audit["ambiguous_output"] = portable_path(ambiguous_output)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if ambiguous_audit is not None and ambiguous_output is not None:
        ambiguous_output.parent.mkdir(parents=True, exist_ok=True)
        ambiguous_output.write_text(
            json.dumps(ambiguous_audit, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    written_candidates = len(audit["candidates"])
    print(
        f"Scanned {audit['files_scanned']} files ({audit['selection_mode']}); "
        f"wrote {written_candidates} candidates to {args.output_json}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
