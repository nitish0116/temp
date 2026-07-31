"""Resolve and execute explicit glossary review commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from markdownCleaner.modules.core.config import PipelineConfig
from markdownCleaner.modules.symspell.vocabulary import (
    merge_approved_words,
    merge_learned_words,
    merge_rejected_words,
)


MergeWords = Callable[[Path, Sequence[str]], list[str]]


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    """One validated glossary-review action."""

    words: Sequence[str]
    explicit_file: Path | None
    config_key: str
    default_file: str
    label: str
    merge: MergeWords


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """Outcome printed by the CLI after a glossary-review action."""

    label: str
    target: Path
    added: list[str]


def review_action_count(args: object) -> int:
    """Return the number of mutually exclusive review actions requested."""
    return sum(
        bool(getattr(args, name))
        for name in ("approve_words", "learn_words", "reject_words")
    )


def review_request(args: object) -> ReviewRequest | None:
    """Translate parsed arguments into one declarative review request."""
    if getattr(args, "reject_words"):
        return ReviewRequest(
            words=args.reject_words,
            explicit_file=args.rejected_file,
            config_key="vocabulary_candidates.rejected",
            default_file="data/rejected_words.json",
            label="Rejected words",
            merge=merge_rejected_words,
        )
    if getattr(args, "learn_words"):
        return ReviewRequest(
            words=args.learn_words,
            explicit_file=args.learned_file,
            config_key="symspell.learned",
            default_file="data/learned_words.json",
            label="Learned words",
            merge=merge_learned_words,
        )
    if getattr(args, "approve_words"):
        return ReviewRequest(
            words=args.approve_words,
            explicit_file=args.glossary_file,
            config_key="symspell.glossary",
            default_file="data/custom_words.json",
            label="Glossary",
            merge=merge_approved_words,
        )
    return None


def resolve_review_target(
    request: ReviewRequest,
    config: PipelineConfig,
) -> Path:
    """Resolve an explicit or configuration-relative review destination."""
    if request.explicit_file:
        return request.explicit_file.resolve()
    configured = config.get(request.config_key, request.default_file)
    resolved = config.resolve_path(configured)
    if resolved is None:
        raise ValueError(f"{request.config_key} cannot be null")
    return Path(resolved)


def apply_review_request(
    request: ReviewRequest,
    config_path: Path,
) -> ReviewResult:
    """Load configuration and persist the terms from ``request``."""
    config = PipelineConfig.load(config_path)
    target = resolve_review_target(request, config)
    added = request.merge(target, request.words)
    return ReviewResult(label=request.label, target=target, added=added)
