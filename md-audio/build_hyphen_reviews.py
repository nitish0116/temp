#!/usr/bin/env python3
"""Run the complete portable hyphen-review pipeline for the library."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path

import audit_hyphens as audit
import classify_ambiguous_hyphens as evidence
from md_audio import review_records


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_LIBRARY = SCRIPT_DIR.parent / "Library"
DEFAULT_MAIN = SCRIPT_DIR / "library-hyphen-review.json"
DEFAULT_AMBIGUOUS = SCRIPT_DIR / "library-hyphen-review-ambiguous.json"
DEFAULT_EVIDENCE = SCRIPT_DIR / "library-hyphen-review-cross-evidence.json"
DEFAULT_CACHE = SCRIPT_DIR / ".hyphen-review-cache.json.gz"
CACHE_SCHEMA_VERSION = 2
CACHE_CONTEXT_LIMIT = 10


def scan_markdown_file(path: Path, raw: bytes | None = None) -> dict[str, object]:
    """Extract all data needed by both classifiers from one Markdown file."""
    raw = path.read_bytes() if raw is None else raw
    text = raw.decode("utf-8")
    occurrences: Counter[str] = Counter()
    variants: dict[str, Counter[str]] = defaultdict(Counter)
    contexts: dict[str, list[dict[str, object]]] = defaultdict(list)
    punctuation: dict[
        tuple[str, str, tuple[str, ...], tuple[str, ...]], Counter[str]
    ] = defaultdict(Counter)

    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in audit.HYPHENATED_RE.finditer(line):
            token = match.group(0)
            key = token.casefold()
            occurrences[key] += 1
            variants[key][token] += 1
            if len(contexts[key]) < CACHE_CONTEXT_LIMIT:
                contexts[key].append(
                    {
                        "line": line_number,
                        "text": audit.context_for_line(line, match.start()),
                    }
                )
        for match in evidence.PUNCTUATED_PAIR_RE.finditer(line):
            signature = evidence.context_signature(
                line,
                match.start(),
                match.end(),
                match.group("left"),
                match.group("right"),
            )
            punctuation[signature][match.group("separator")] += 1

    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "hyphens": [
            {
                "key": key,
                "count": occurrences[key],
                "variants": dict(variants[key]),
                "contexts": contexts[key],
            }
            for key in occurrences
        ],
        "punctuation": [
            {
                "left": signature[0],
                "right": signature[1],
                "before": list(signature[2]),
                "after": list(signature[3]),
                "separators": dict(counts),
            }
            for signature, counts in punctuation.items()
        ],
    }


def load_cache(path: Path) -> dict[str, object]:
    """Load a compatible cache, or return a new empty cache."""
    if not path.is_file():
        return {"schema_version": CACHE_SCHEMA_VERSION, "files": {}}
    try:
        if path.suffix.casefold() == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                value = json.load(handle)
        else:
            value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, EOFError):
        return {"schema_version": CACHE_SCHEMA_VERSION, "files": {}}
    if value.get("schema_version") != CACHE_SCHEMA_VERSION or not isinstance(
        value.get("files"), dict
    ):
        return {"schema_version": CACHE_SCHEMA_VERSION, "files": {}}
    return value


def write_cache(path: Path, cache: dict[str, object]) -> None:
    """Write a portable cache, using deterministic gzip when requested."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.casefold() == ".gz":
        with path.open("wb") as raw_handle:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw_handle, mtime=0
            ) as compressed:
                payload = json.dumps(
                    cache, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                compressed.write(payload)
    else:
        path.write_text(
            json.dumps(cache, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )


def update_cache(
    library: Path, cache_path: Path, rebuild: bool = False
) -> tuple[list[tuple[str, dict[str, object]]], dict[str, int]]:
    """Refresh only new or modified files and remove deleted cache entries."""
    cache = (
        load_cache(cache_path)
        if not rebuild
        else {"schema_version": CACHE_SCHEMA_VERSION, "files": {}}
    )
    old_files = cache["files"]
    new_files: dict[str, object] = {}
    hits = 0
    misses = 0
    for path in sorted(library.rglob("*.md")):
        relative = path.relative_to(library).as_posix()
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        cached = old_files.get(relative)
        if (
            isinstance(cached, dict)
            and cached.get("sha256") == digest
        ):
            entry = cached
            hits += 1
        else:
            entry = scan_markdown_file(path, raw)
            misses += 1
        new_files[relative] = entry
    removed = len(set(old_files) - set(new_files))
    cache = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "library": audit.portable_path(library),
        "files": new_files,
    }
    write_cache(cache_path, cache)
    return list(new_files.items()), {
        "cache_hits": hits,
        "cache_misses": misses,
        "cache_removed": removed,
    }


def records_from_cache(
    library: Path,
    cached_files: list[tuple[str, dict[str, object]]],
    genuine: set[str],
    max_contexts: int,
) -> tuple[dict[str, object], evidence.PunctuationIndex]:
    """Aggregate unique cached files into audit and punctuation records."""
    occurrences: Counter[str] = Counter()
    variants: dict[str, Counter[str]] = defaultdict(Counter)
    contexts: dict[str, list[dict[str, object]]] = defaultdict(list)
    punctuation_index: evidence.PunctuationIndex = defaultdict(Counter)
    seen_digests: set[str] = set()
    duplicates = 0

    for relative, entry in cached_files:
        digest = entry["sha256"]
        if digest in seen_digests:
            duplicates += 1
            continue
        seen_digests.add(digest)
        for item in entry["hyphens"]:
            key = item["key"]
            occurrences[key] += item["count"]
            variants[key].update(item["variants"])
            for context in item["contexts"]:
                if len(contexts[key]) >= max_contexts:
                    break
                contexts[key].append({"file": relative, **context})
        for item in entry["punctuation"]:
            signature = (
                item["left"],
                item["right"],
                tuple(item["before"]),
                tuple(item["after"]),
            )
            punctuation_index[signature].update(item["separators"])

    main_record = audit.assemble_audit(
        library,
        genuine,
        occurrences,
        variants,
        contexts,
        "all unique files (incremental cache)",
        len(cached_files),
        len(seen_digests),
        duplicates,
    )
    return main_record, punctuation_index


def run_pipeline(
    library: Path,
    main_output: Path,
    ambiguous_output: Path,
    evidence_output: Path,
    genuine_file: Path | None = None,
    max_contexts: int = 3,
    cache_path: Path = DEFAULT_CACHE,
    rebuild_cache: bool = False,
) -> dict[str, int]:
    """Audit, classify, cross-check, and write all review records."""
    genuine = audit.load_genuine_hyphens(genuine_file)
    cached_files, cache_stats = update_cache(library, cache_path, rebuild_cache)
    main_record, punctuation_index = records_from_cache(
        library, cached_files, genuine, max_contexts
    )
    audit.auto_classify(main_record)
    ambiguous_record = audit.move_ambiguous_entries(main_record)

    resolved, unresolved = evidence.classify_record(
        ambiguous_record, punctuation_index
    )
    evidence.merge_decisions(main_record, resolved)

    generated_at = review_records.utc_timestamp()
    main_record["generated_at"] = generated_at
    main_record["ambiguous_output"] = audit.portable_path(ambiguous_output)
    main_record["cross_evidence_output"] = audit.portable_path(evidence_output)
    ambiguous_record["generated_at"] = generated_at
    ambiguous_record["source_root"] = audit.portable_path(library)
    ambiguous_record["cross_evidence_resolved"] = len(resolved)
    ambiguous_record["ambiguous_candidates"] = len(unresolved)
    ambiguous_record["candidates"] = unresolved
    resolved_record = {
        "schema_version": 1,
        "generated_at": generated_at,
        "source_root": audit.portable_path(library),
        "resolved_candidates": len(resolved),
        "candidates": resolved,
    }

    evidence.write_record(main_output, main_record)
    evidence.write_record(ambiguous_output, ambiguous_record)
    evidence.write_record(evidence_output, resolved_record)
    return {
        "files_scanned": int(main_record["files_scanned"]),
        "decided": len(main_record["candidates"]),
        "replacements": sum(
            item.get("status") == "replace" for item in main_record["candidates"]
        ),
        "genuine": sum(
            item.get("status") == "genuine" for item in main_record["candidates"]
        ),
        "cross_evidence_resolved": len(resolved),
        "ambiguous": len(unresolved),
        **cache_stats,
    }


def parse_args() -> argparse.Namespace:
    """Parse integrated hyphen-review pipeline arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument("--main-output", type=Path, default=DEFAULT_MAIN)
    parser.add_argument("--ambiguous-output", type=Path, default=DEFAULT_AMBIGUOUS)
    parser.add_argument("--evidence-output", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--genuine-hyphens", type=Path)
    parser.add_argument("--max-contexts", type=int, default=3)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument(
        "--rebuild-cache",
        action="store_true",
        help="Discard cached scans and rescan every Markdown file",
    )
    return parser.parse_args()


def main() -> int:
    """Run the integrated cached review pipeline and print its summary."""
    args = parse_args()
    if not args.library.is_dir():
        raise SystemExit(f"Library folder does not exist: {args.library}")
    if args.max_contexts < 1:
        raise SystemExit("--max-contexts must be at least 1")
    result = run_pipeline(
        args.library,
        args.main_output,
        args.ambiguous_output,
        args.evidence_output,
        args.genuine_hyphens,
        args.max_contexts,
        args.cache,
        args.rebuild_cache,
    )
    print(
        f"Files: {result['cache_hits']} cached, {result['cache_misses']} scanned, "
        f"{result['cache_removed']} removed; "
        f"{result['replacements']} replacements, {result['genuine']} genuine, "
        f"{result['ambiguous']} ambiguous."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
