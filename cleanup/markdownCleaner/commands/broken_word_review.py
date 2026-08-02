"""Cached, evidence-driven review workflow for OCR word boundaries.

Normal cleaning never writes the reviewed decision store.  This module scans a
library separately, classifies only plausible unresolved boundaries, and lets
an explicit promotion command copy accepted/rejected results into the stable
``broken_word_decisions.json`` contract.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
from typing import Any

from markdownCleaner.modules.core.config import PipelineConfig
from markdownCleaner.modules.markdown.segmenter import protected_span_ranges
from markdownCleaner.modules.symspell.broken_words import BrokenWordEvaluator
from markdownCleaner.modules.symspell.decisions import BrokenWordDecisions
from markdownCleaner.modules.symspell.dictionary import DictionaryManager
from markdownCleaner.modules.symspell.frequency import WordfreqScorer
from markdownCleaner.modules.symspell.ocr_candidates import (
    OCRBoundaryCandidates,
)
from markdownCleaner.modules.symspell.settings import SymSpellSettings


REVIEW_SCHEMA_VERSION = 1
CACHE_SCHEMA_VERSION = 1
CACHE_CONTEXT_LIMIT = 10
WORD_RE = re.compile(r"[A-Za-z]{2,}")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
BOUNDARY_RE = re.compile(r"^[A-Za-z]{2,}\s+[A-Za-z]{2,}$")
MINIMUM_CORPUS_OCCURRENCES = 3
CORPUS_DOMINANCE_RATIO = 3


@dataclass(frozen=True, slots=True)
class ReviewResources:
    """Lexical dependencies shared by scanning and classification."""

    dictionary: DictionaryManager
    scorer: WordfreqScorer
    settings: SymSpellSettings
    decisions: BrokenWordDecisions
    fingerprint: str


def utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp for generated records."""

    return datetime.now(timezone.utc).isoformat()


def portable_path(path: Path, base: Path) -> str:
    """Represent a path relative to an artifact directory when possible."""

    try:
        return Path(os.path.relpath(path.resolve(), base.resolve())).as_posix()
    except ValueError:
        return path.name


def _dependency_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "unavailable"


def _lexical_fingerprint(
    dictionary: DictionaryManager,
    settings: SymSpellSettings,
) -> str:
    """Hash lexical content and policy so portable caches invalidate safely."""

    digest = hashlib.sha256()
    policy = {
        "schema": CACHE_SCHEMA_VERSION,
        "minimum_zipf": settings.wordfreq_minimum_zipf,
        "language": settings.wordfreq_language,
        "wordlist": settings.wordfreq_wordlist,
        "wordfreq": _dependency_version("wordfreq"),
    }
    digest.update(json.dumps(policy, sort_keys=True).encode("utf-8"))
    for word, frequency in sorted(dictionary.words.items()):
        digest.update(word.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(frequency).encode("ascii"))
        digest.update(b"\1" if word in dictionary.protected_words else b"\0")
    return digest.hexdigest()


def resources_from_config(config: PipelineConfig) -> ReviewResources:
    """Load the same dictionary, frequency, and decision policy as SymSpell."""

    settings = SymSpellSettings.from_config(config)
    dictionary = DictionaryManager(
        dictionary_path=config.resolve_path(settings.dictionary),
        glossary_path=config.resolve_path(settings.glossary),
        learned_path=config.resolve_path(settings.learned),
    )
    dictionary.load()
    for term in settings.protected_terms:
        dictionary.protect_entry(term)
    scorer = WordfreqScorer(
        enabled=settings.wordfreq_enabled,
        language=settings.wordfreq_language,
        wordlist=settings.wordfreq_wordlist,
    )
    decisions = BrokenWordDecisions.load(
        config.resolve_path(settings.broken_word_decisions)
    )
    return ReviewResources(
        dictionary=dictionary,
        scorer=scorer,
        settings=settings,
        decisions=decisions,
        fingerprint=_lexical_fingerprint(dictionary, settings),
    )


def _overlaps_protected(
    start: int,
    end: int,
    ranges: list[tuple[int, int]],
) -> bool:
    return any(
        start < protected_end and end > protected_start
        for protected_start, protected_end in ranges
    )


def _context(line: str, start: int, limit: int = 240) -> str:
    left = max(0, start - limit // 2)
    return line[left : left + limit].strip()


def _plausible_join(resources: ReviewResources, left: str, right: str) -> bool:
    joined = (left + right).casefold()
    return bool(
        resources.dictionary.contains(joined)
        or resources.dictionary.is_protected(joined)
        or resources.scorer.zipf(joined)
        >= resources.settings.wordfreq_minimum_zipf
    )


def scan_markdown_file(
    path: Path,
    resources: ReviewResources,
    raw: bytes | None = None,
) -> dict[str, object]:
    """Extract plausible spaced joins and word counts from one Markdown file."""

    raw = path.read_bytes() if raw is None else raw
    text = raw.decode("utf-8")
    occurrences: Counter[str] = Counter()
    variants: dict[str, Counter[str]] = defaultdict(Counter)
    contexts: dict[str, list[dict[str, object]]] = defaultdict(list)
    words: Counter[str] = Counter()
    in_fence = False

    for line_number, line in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        matches = list(WORD_RE.finditer(line))
        words.update(match.group(0).casefold() for match in matches)
        protected = protected_span_ranges(line)
        for left_match, right_match in zip(matches, matches[1:]):
            start, end = left_match.start(), right_match.end()
            if _overlaps_protected(start, end, protected):
                continue
            between = line[left_match.end() : right_match.start()]
            if re.fullmatch(r"[ \t]+", between) is None:
                continue
            left, right = left_match.group(0), right_match.group(0)
            if not _plausible_join(resources, left, right):
                continue
            key = f"{left.casefold()} {right.casefold()}"
            broken = line[start:end]
            occurrences[key] += 1
            variants[key][broken] += 1
            if len(contexts[key]) < CACHE_CONTEXT_LIMIT:
                contexts[key].append(
                    {"line": line_number, "text": _context(line, start)}
                )

    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "words": dict(words),
        "pairs": [
            {
                "key": key,
                "count": occurrences[key],
                "variants": dict(variants[key]),
                "contexts": contexts[key],
            }
            for key in sorted(occurrences)
        ],
    }


def _empty_cache(fingerprint: str) -> dict[str, object]:
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "lexical_fingerprint": fingerprint,
        "files": {},
    }


def load_cache(path: Path, fingerprint: str) -> dict[str, object]:
    """Load a compatible cache or return a new machine-independent cache."""

    if not path.is_file():
        return _empty_cache(fingerprint)
    try:
        if path.suffix.casefold() == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                value = json.load(handle)
        else:
            value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, EOFError):
        return _empty_cache(fingerprint)
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != CACHE_SCHEMA_VERSION
        or value.get("lexical_fingerprint") != fingerprint
        or not isinstance(value.get("files"), dict)
    ):
        return _empty_cache(fingerprint)
    return value


def write_json(path: Path, value: dict[str, object], *, compact: bool = False) -> None:
    """Write one UTF-8 JSON object, creating its parent directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    separators = (",", ":") if compact else None
    indent = None if compact else 2
    payload = json.dumps(
        value,
        ensure_ascii=False,
        indent=indent,
        separators=separators,
    )
    path.write_text(payload + "\n", encoding="utf-8")


def write_cache(path: Path, cache: dict[str, object]) -> None:
    """Write deterministic gzip or plain JSON cache content."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        cache, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if path.suffix.casefold() != ".gz":
        path.write_bytes(payload + b"\n")
        return
    with path.open("wb") as raw_handle:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw_handle, mtime=0
        ) as compressed:
            compressed.write(payload)


def update_cache(
    library: Path,
    cache_path: Path,
    resources: ReviewResources,
    *,
    rebuild: bool = False,
) -> tuple[list[tuple[str, dict[str, object]]], dict[str, int]]:
    """Refresh new/changed files by SHA-256 and forget deleted files."""

    cache = (
        _empty_cache(resources.fingerprint)
        if rebuild
        else load_cache(cache_path, resources.fingerprint)
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
        if isinstance(cached, dict) and cached.get("sha256") == digest:
            entry = cached
            hits += 1
        else:
            entry = scan_markdown_file(path, resources, raw)
            misses += 1
        new_files[relative] = entry
    removed = len(set(old_files) - set(new_files))
    updated = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "lexical_fingerprint": resources.fingerprint,
        "library": portable_path(library, cache_path.parent),
        "files": new_files,
    }
    write_cache(cache_path, updated)
    return list(new_files.items()), {
        "cache_hits": hits,
        "cache_misses": misses,
        "cache_removed": removed,
    }


def _classify_candidate(
    *,
    broken_word: str,
    occurrences: int,
    joined_occurrences: int,
    resources: ReviewResources,
) -> dict[str, object]:
    """Classify one unresolved pair with conservative corpus evidence."""

    # Decision keys are case-insensitive. Keep ordinary lexical replacements
    # canonical so a sentence-initial occurrence does not force capitalization
    # onto the same boundary in the middle of a sentence.
    left, right = broken_word.casefold().split()
    joined = left + right
    joined_zipf = resources.scorer.zipf(joined)
    phrase_zipf = resources.scorer.zipf(f"{left} {right}")
    evidence = {
        "joined_zipf": round(joined_zipf, 3),
        "phrase_zipf": round(phrase_zipf, 3),
        "joined_occurrences": joined_occurrences,
        "spaced_occurrences": occurrences,
        "left_known": resources.dictionary.contains(left),
        "right_known": resources.dictionary.contains(right),
    }

    sufficient_evidence = (
        max(joined_occurrences, occurrences)
        >= MINIMUM_CORPUS_OCCURRENCES
    )
    if not sufficient_evidence:
        return {
            "status": "insufficient",
            "replacement": None,
            "confidence": 0.0,
            "classification_basis": (
                "neither joined nor spaced form has the minimum of "
                f"{MINIMUM_CORPUS_OCCURRENCES} corpus occurrences"
            ),
            "evidence": evidence,
        }

    corpus_dominates = (
        joined_occurrences >= MINIMUM_CORPUS_OCCURRENCES
        and joined_occurrences
        >= occurrences * CORPUS_DOMINANCE_RATIO
        and joined_zipf >= phrase_zipf + 0.25
    )
    lexical_dominates = (
        joined_occurrences >= MINIMUM_CORPUS_OCCURRENCES
        and joined_occurrences >= occurrences
        and joined_zipf
        >= max(resources.settings.wordfreq_minimum_zipf, 3.0)
        and joined_zipf >= phrase_zipf + 1.25
    )
    phrase_dominates = (
        occurrences >= MINIMUM_CORPUS_OCCURRENCES
        and occurrences
        >= max(
            MINIMUM_CORPUS_OCCURRENCES,
            joined_occurrences * CORPUS_DOMINANCE_RATIO,
        )
        and phrase_zipf >= joined_zipf + 1.25
    )
    if corpus_dominates or lexical_dominates:
        basis = (
            "joined form dominates the scanned corpus and phrase score"
            if corpus_dominates
            else "joined lexical score strongly exceeds the spaced phrase"
        )
        return {
            "status": "accepted",
            "replacement": joined,
            "confidence": 97.0,
            "classification_basis": basis,
            "evidence": evidence,
        }
    if phrase_dominates:
        return {
            "status": "rejected",
            "replacement": None,
            "confidence": 97.0,
            "classification_basis": (
                "spaced phrase score strongly exceeds the joined form"
            ),
            "evidence": evidence,
        }
    return {
        "status": "review",
        "replacement": joined,
        "confidence": 0.0,
        "classification_basis": (
            "joined word and legitimate phrase remain contextually plausible"
        ),
        "evidence": evidence,
    }


def records_from_cache(
    library: Path,
    cached_files: list[tuple[str, dict[str, object]]],
    resources: ReviewResources,
    *,
    max_contexts: int,
    output_base: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    """Aggregate unique files and split decided from ambiguous candidates."""

    pair_counts: Counter[str] = Counter()
    variants: dict[str, Counter[str]] = defaultdict(Counter)
    contexts: dict[str, list[dict[str, object]]] = defaultdict(list)
    word_counts: Counter[str] = Counter()
    seen_digests: set[str] = set()
    duplicates = 0
    for relative, entry in cached_files:
        digest = str(entry.get("sha256", ""))
        if digest in seen_digests:
            duplicates += 1
            continue
        seen_digests.add(digest)
        word_counts.update(entry.get("words", {}))
        for item in entry.get("pairs", []):
            key = str(item["key"])
            pair_counts[key] += int(item["count"])
            variants[key].update(item.get("variants", {}))
            for context in item.get("contexts", []):
                if len(contexts[key]) >= max_contexts:
                    break
                contexts[key].append({"file": relative, **context})

    evaluator = BrokenWordEvaluator(
        resources.dictionary,
        resources.scorer,
        resources.settings,
        BrokenWordDecisions(),
    )
    decided: list[dict[str, object]] = []
    ambiguous: list[dict[str, object]] = []
    existing = 0
    already_automatic = 0
    insufficient_evidence = 0
    for key in sorted(pair_counts):
        left, right = key.split()
        if resources.decisions.is_rejected(left, right) or (
            resources.decisions.accepted_replacement(left, right) is not None
        ):
            existing += 1
            continue
        if evaluator.evaluate(left, right) is not None:
            already_automatic += 1
            continue
        display = variants[key].most_common(1)[0][0]
        classification = _classify_candidate(
            broken_word=display,
            occurrences=pair_counts[key],
            joined_occurrences=word_counts[left + right],
            resources=resources,
        )
        if classification["status"] == "insufficient":
            insufficient_evidence += 1
            continue
        candidate = {
            "broken_word": display,
            "occurrences": pair_counts[key],
            "variants": dict(variants[key]),
            "contexts": contexts[key],
            **classification,
        }
        (ambiguous if classification["status"] == "review" else decided).append(
            candidate
        )

    generated_at = utc_timestamp()
    common = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "generated_at": generated_at,
        "source_root": portable_path(library, output_base),
        "files_discovered": len(cached_files),
        "unique_files_scanned": len(seen_digests),
        "identical_duplicates_skipped": duplicates,
        "existing_decisions_skipped": existing,
        "already_automatic_skipped": already_automatic,
        "insufficient_evidence_skipped": insufficient_evidence,
    }
    main = {
        **common,
        "instructions": (
            "These high-confidence proposals remain inert until explicitly "
            "promoted with --promote-broken-word-review."
        ),
        "decided_candidates": len(decided),
        "candidates": decided,
    }
    unresolved = {
        **common,
        "instructions": (
            "Review each candidate. Change status to accepted or rejected, "
            "then explicitly promote this file; status review is ignored."
        ),
        "ambiguous_candidates": len(ambiguous),
        "candidates": ambiguous,
    }
    return main, unresolved


def run_review_pipeline(
    library: Path,
    *,
    config: PipelineConfig,
    main_output: Path,
    ambiguous_output: Path,
    cache_path: Path,
    max_contexts: int = 3,
    rebuild_cache: bool = False,
) -> dict[str, int]:
    """Run cached scanning, classification, and review-record generation."""

    if not library.is_dir():
        raise ValueError(f"Library folder does not exist: {library}")
    if max_contexts < 1:
        raise ValueError("maximum broken-word contexts must be at least 1")
    resources = resources_from_config(config)
    files, cache_stats = update_cache(
        library,
        cache_path,
        resources,
        rebuild=rebuild_cache,
    )
    if not files:
        raise ValueError(f"No Markdown files found in: {library}")
    main, ambiguous = records_from_cache(
        library,
        files,
        resources,
        max_contexts=max_contexts,
        output_base=main_output.parent,
    )
    main["ambiguous_output"] = portable_path(
        ambiguous_output, main_output.parent
    )
    write_json(main_output, main)
    write_json(ambiguous_output, ambiguous)
    return {
        "files": len(files),
        "decided": len(main["candidates"]),
        "accepted": sum(
            item.get("status") == "accepted" for item in main["candidates"]
        ),
        "rejected": sum(
            item.get("status") == "rejected" for item in main["candidates"]
        ),
        "ambiguous": len(ambiguous["candidates"]),
        "insufficient": main["insufficient_evidence_skipped"],
        **cache_stats,
    }


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not load JSON '{path}': {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON must contain an object: {path}")
    return value


def promote_review(
    review_path: Path,
    decisions_path: Path,
) -> dict[str, int]:
    """Promote explicitly accepted/rejected candidates into the decision store."""

    review = _load_json_object(review_path)
    candidates = review.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError(f"Review JSON has no candidates list: {review_path}")
    if decisions_path.exists():
        store = _load_json_object(decisions_path)
    else:
        store = {"accepted": {}, "rejected": []}
    accepted = store.get("accepted")
    rejected = store.get("rejected")
    if not isinstance(accepted, dict) or not isinstance(rejected, list):
        raise ValueError(
            "Broken-word decisions require an accepted object and rejected list."
        )

    accepted_keys = {" ".join(str(key).casefold().split()): key for key in accepted}
    rejected_values = {
        " ".join(str(value).casefold().split()): str(value)
        for value in rejected
    }
    promoted = 0
    ignored = 0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            ignored += 1
            continue
        status = candidate.get("status")
        if status not in {"accepted", "rejected"}:
            ignored += 1
            continue
        broken = " ".join(str(candidate.get("broken_word", "")).split())
        if not BOUNDARY_RE.fullmatch(broken):
            raise ValueError(f"Invalid broken-word candidate: {broken!r}")
        key = broken.casefold()
        if status == "accepted":
            replacement = str(candidate.get("replacement", "")).strip()
            if not replacement:
                raise ValueError(f"Accepted candidate has no replacement: {broken}")
            value: str | dict[str, object] = replacement
            blocked_previous = candidate.get("blocked_previous", [])
            blocked_following = candidate.get("blocked_following", [])
            if blocked_previous or blocked_following:
                if not isinstance(blocked_previous, list) or not isinstance(
                    blocked_following, list
                ):
                    raise ValueError(
                        f"Context blockers must be lists for: {broken}"
                    )
                value = {
                    "replacement": replacement,
                    "blocked_previous": blocked_previous,
                    "blocked_following": blocked_following,
                }
            existing_key = accepted_keys.get(key, broken)
            accepted[existing_key] = value
            accepted_keys[key] = existing_key
            rejected_values.pop(key, None)
        else:
            existing_key = accepted_keys.pop(key, None)
            if existing_key is not None:
                accepted.pop(existing_key, None)
            rejected_values[key] = broken
        promoted += 1

    store["accepted"] = accepted
    store["rejected"] = [rejected_values[key] for key in sorted(rejected_values)]
    # Validate the final schema and conflicts before replacing the file.
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = decisions_path.with_suffix(decisions_path.suffix + ".tmp")
    write_json(temporary, store)
    try:
        BrokenWordDecisions.load(temporary)
        temporary.replace(decisions_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"promoted": promoted, "ignored": ignored}


def import_review_candidates(
    review_path: Path,
    candidate_path: Path,
) -> dict[str, int]:
    """Import generated review evidence into the non-authoritative store.

    Accepted and unresolved entries with replacements become transformer
    candidates. Corpus-rejected entries become suppressions. Nothing is added
    to the human-reviewed decision store.
    """

    review = _load_json_object(review_path)
    items = review.get("candidates")
    if not isinstance(items, list):
        raise ValueError(f"Review JSON has no candidates list: {review_path}")
    if candidate_path.exists():
        # Reject malformed existing data instead of silently stringifying it.
        OCRBoundaryCandidates.load(candidate_path)
        store = _load_json_object(candidate_path)
    else:
        store = {
            "_description": (
                "Generated OCR boundary candidates; contextual validation "
                "is required before mutation."
            ),
            "candidates": {},
            "suppressed": [],
        }
    raw_candidates = store.get("candidates", {})
    raw_suppressed = store.get("suppressed", [])
    if not isinstance(raw_candidates, dict) or not isinstance(
        raw_suppressed, list
    ):
        raise ValueError(
            "OCR candidate store requires a candidates object and "
            "suppressed list."
        )

    candidates = {
        " ".join(str(key).casefold().split()): value.strip()
        for key, value in raw_candidates.items()
    }
    suppressed = {
        " ".join(str(value).casefold().split()) for value in raw_suppressed
    }
    candidates_added = 0
    candidates_updated = 0
    suppressions_added = 0
    ignored = 0
    for item in items:
        if not isinstance(item, dict):
            ignored += 1
            continue
        status = item.get("status")
        broken = " ".join(str(item.get("broken_word", "")).split())
        if not BOUNDARY_RE.fullmatch(broken):
            raise ValueError(f"Invalid broken-word candidate: {broken!r}")
        key = broken.casefold()
        if status in {"accepted", "review"}:
            replacement = str(item.get("replacement", "")).strip()
            if not replacement:
                ignored += 1
                continue
            if key not in candidates:
                candidates_added += 1
            elif candidates[key] != replacement:
                candidates_updated += 1
            candidates[key] = replacement
            suppressed.discard(key)
        elif status == "rejected":
            if key not in suppressed:
                suppressions_added += 1
            suppressed.add(key)
            candidates.pop(key, None)
        else:
            ignored += 1

    store["candidates"] = {
        key: candidates[key] for key in sorted(candidates)
    }
    store["suppressed"] = sorted(suppressed)
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = candidate_path.with_suffix(candidate_path.suffix + ".tmp")
    write_json(temporary, store)
    try:
        OCRBoundaryCandidates.load(temporary)
        temporary.replace(candidate_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "candidates_added": candidates_added,
        "candidates_updated": candidates_updated,
        "suppressions_added": suppressions_added,
        "ignored": ignored,
    }


__all__ = [
    "ReviewResources",
    "load_cache",
    "import_review_candidates",
    "promote_review",
    "records_from_cache",
    "resources_from_config",
    "run_review_pipeline",
    "scan_markdown_file",
    "update_cache",
]
