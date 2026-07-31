"""Validated persistence for approved, learned, and rejected vocabulary."""

from __future__ import annotations

import json
from pathlib import Path

from .tokens import TERM_PATTERN


LEARNED_DESCRIPTION = (
    "Words explicitly reviewed by the user. Add entries with "
    "`python -m markdownCleaner.cli --learn-words WORD ...`."
)
REJECTED_DESCRIPTION = (
    "Reviewed terms intentionally excluded from glossary candidate reports. "
    "They are not protected from SymSpell correction. Add entries with "
    "`python -m markdownCleaner.cli --reject-words WORD ...`."
)


def word_list(data, *, label: str) -> list[str]:
    """Extract words from legacy lists or structured vocabulary objects."""

    if isinstance(data, list):
        return [str(word) for word in data]
    if isinstance(data, dict):
        if "words" in data:
            words = data["words"]
            if not isinstance(words, list):
                raise ValueError(f"{label} JSON field 'words' must be a list.")
            return [str(word) for word in words]
        return [
            str(word)
            for word in data
            if not str(word).startswith("_")
        ]
    raise ValueError(f"{label} JSON must contain a list or object.")


def merge_words(
    path: str | Path,
    words: list[str],
    *,
    structured: bool,
    description: str = LEARNED_DESCRIPTION,
) -> list[str]:
    """Validate, case-deduplicate, sort, and persist reviewed vocabulary."""

    target = Path(path)
    existing: list[str] = []
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in {target}: line {exc.lineno}, "
                f"column {exc.colno}. Use the appropriate CLI word-review "
                "command to update it safely."
            ) from exc
        existing = word_list(data, label="Vocabulary")

    by_key = {
        word.casefold(): word
        for word in existing
        if word.strip()
    }
    added: list[str] = []
    for raw in words:
        word = str(raw).strip()
        if not TERM_PATTERN.fullmatch(word) or len(word) < 2:
            raise ValueError(f"Invalid vocabulary word: {raw!r}")
        key = word.casefold()
        if key not in by_key:
            by_key[key] = word
            added.append(word)

    target.parent.mkdir(parents=True, exist_ok=True)
    values = sorted(by_key.values(), key=str.casefold)
    data = (
        {"_description": description, "words": values}
        if structured
        else values
    )
    target.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return added


def merge_approved_words(path: str | Path, words: list[str]) -> list[str]:
    """Add explicitly approved terms to the custom glossary JSON list."""

    return merge_words(path, words, structured=False)


def merge_learned_words(path: str | Path, words: list[str]) -> list[str]:
    """Add reviewed protected terms to the structured learned-word file."""

    return merge_words(path, words, structured=True)


def merge_rejected_words(path: str | Path, words: list[str]) -> list[str]:
    """Suppress reviewed terms from future candidate reports."""

    return merge_words(
        path,
        words,
        structured=True,
        description=REJECTED_DESCRIPTION,
    )


def load_reviewed_words(path: str | Path | None) -> set[str]:
    """Load a reviewed-word file as normalized, case-insensitive keys."""

    if not path:
        return set()
    target = Path(path)
    if not target.exists():
        return set()
    data = json.loads(target.read_text(encoding="utf-8"))
    return {
        word.strip().casefold()
        for word in word_list(data, label="Reviewed words")
        if word.strip()
    }


__all__ = [
    "LEARNED_DESCRIPTION",
    "REJECTED_DESCRIPTION",
    "load_reviewed_words",
    "merge_approved_words",
    "merge_learned_words",
    "merge_rejected_words",
    "merge_words",
    "word_list",
]
