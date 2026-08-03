"""Batched masked-language-model validation for suspicious OCR boundaries."""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
import math
from typing import Protocol, Sequence

from .broken_words import (
    MergeCandidate,
    MergeDecision,
    MergeEvidence,
    MergeEvidenceKind,
)
from .training_data import BoundaryTrainingDataWriter, example_id
from ..core.config import require_bool


LOGGER = logging.getLogger("ocr_cleanup")


@dataclass(frozen=True, slots=True)
class ContextValidatorSettings:
    """Configuration for optional transformer boundary validation."""

    enabled: bool = False
    model: str = "distilbert/distilroberta-base"
    candidate_file: str | None = None
    batch_size: int = 16
    max_length: int = 128
    context_characters: int = 600
    merge_margin: float = 0.35
    device: str = "auto"
    local_files_only: bool = False

    @classmethod
    def from_config(cls, config) -> "ContextValidatorSettings":
        get = config.get
        model = get(
            "context_validator.model",
            "distilbert/distilroberta-base",
        )
        candidate_file = get(
            "context_validator.candidate_file",
            "data/ocr_boundary_candidates.json",
        )
        return cls(
            enabled=require_bool(
                get("context_validator.enabled", False),
                "context_validator.enabled",
            ),
            model="" if model is None else str(model).strip(),
            candidate_file=(
                None if candidate_file is None else str(candidate_file)
            ),
            batch_size=int(get("context_validator.batch_size", 16)),
            max_length=int(get("context_validator.max_length", 128)),
            context_characters=int(
                get("context_validator.context_characters", 600)
            ),
            merge_margin=float(get("context_validator.merge_margin", 0.35)),
            device=str(get("context_validator.device", "auto")).strip(),
            local_files_only=require_bool(
                get("context_validator.local_files_only", False),
                "context_validator.local_files_only",
            ),
        )

    def validate(self) -> None:
        """Reject invalid resource and batching limits before model loading."""

        if not self.model.strip():
            raise ValueError("context_validator.model cannot be empty.")
        if self.batch_size < 1:
            raise ValueError("context_validator.batch_size must be at least 1.")
        if self.max_length < 16:
            raise ValueError("context_validator.max_length must be at least 16.")
        if self.context_characters < 80:
            raise ValueError(
                "context_validator.context_characters must be at least 80."
            )
        if self.merge_margin < 0:
            raise ValueError("context_validator.merge_margin cannot be negative.")
        if self.device.strip().casefold() not in {"auto", "cpu", "cuda"}:
            raise ValueError(
                "context_validator.device must be auto, cpu, or cuda."
            )


@dataclass(frozen=True, slots=True)
class ScoringVariant:
    """One local sentence variant and its candidate character span."""

    text: str
    target_start: int
    target_end: int


class VariantScorer(Protocol):
    """Minimal scoring interface used for model injection in tests."""

    def score(self, variants: Sequence[ScoringVariant]) -> list[float]: ...


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    """Candidates accepted and rejected by contextual evidence."""

    accepted: tuple[MergeCandidate, ...] = ()
    rejected: tuple[MergeDecision, ...] = ()


class TransformerVariantScorer:
    """Compute localized pseudo-log-likelihood with a masked language model."""

    def __init__(self, settings: ContextValidatorSettings) -> None:
        self.settings = settings
        self._torch = None
        self._tokenizer = None
        self._model = None
        self._device = None

    def _initialize(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoModelForMaskedLM, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "Transformer context validation requires torch and "
                "transformers. Install requirements-transformer.txt."
            ) from exc

        tokenizer = AutoTokenizer.from_pretrained(
            self.settings.model,
            use_fast=True,
            local_files_only=self.settings.local_files_only,
        )
        if not tokenizer.is_fast or tokenizer.mask_token_id is None:
            raise RuntimeError(
                "The context-validator model requires a fast tokenizer with "
                "a mask token."
            )
        model = AutoModelForMaskedLM.from_pretrained(
            self.settings.model,
            local_files_only=self.settings.local_files_only,
        )
        requested = self.settings.device.strip().casefold()
        device = (
            "cuda"
            if requested == "auto" and torch.cuda.is_available()
            else "cpu"
            if requested == "auto"
            else requested
        )
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError(
                "context_validator.device is cuda, but CUDA is unavailable."
            )
        model.to(device)
        model.eval()
        self._torch = torch
        self._tokenizer = tokenizer
        self._model = model
        self._device = device

    def score(self, variants: Sequence[ScoringVariant]) -> list[float]:
        """Return mean target-token log probability for every variant."""

        if not variants:
            return []
        self._initialize()
        torch = self._torch
        tokenizer = self._tokenizer
        model = self._model
        assert torch is not None and tokenizer is not None and model is not None

        tasks: list[dict[str, object]] = []
        scores: list[list[float]] = [[] for _ in variants]
        for variant_index, variant in enumerate(variants):
            encoded = tokenizer(
                variant.text,
                add_special_tokens=True,
                truncation=True,
                max_length=self.settings.max_length,
                return_offsets_mapping=True,
            )
            offsets = encoded.pop("offset_mapping")
            input_ids = list(encoded["input_ids"])
            attention = list(encoded["attention_mask"])
            positions = [
                index
                for index, (start, end) in enumerate(offsets)
                if end > start
                and start < variant.target_end
                and end > variant.target_start
            ]
            for position in positions:
                masked = list(input_ids)
                label = masked[position]
                masked[position] = tokenizer.mask_token_id
                tasks.append(
                    {
                        "variant": variant_index,
                        "input_ids": masked,
                        "attention_mask": attention,
                        "position": position,
                        "label": label,
                    }
                )

        for offset in range(0, len(tasks), self.settings.batch_size):
            batch_tasks = tasks[offset : offset + self.settings.batch_size]
            padded = tokenizer.pad(
                {
                    "input_ids": [item["input_ids"] for item in batch_tasks],
                    "attention_mask": [
                        item["attention_mask"] for item in batch_tasks
                    ],
                },
                padding=True,
                return_tensors="pt",
            )
            padded = {
                key: value.to(self._device) for key, value in padded.items()
            }
            with torch.no_grad():
                logits = model(**padded).logits
                log_probs = torch.log_softmax(logits, dim=-1)
            for row, item in enumerate(batch_tasks):
                probability = log_probs[
                    row,
                    int(item["position"]),
                    int(item["label"]),
                ]
                scores[int(item["variant"])].append(float(probability.item()))

        return [
            sum(values) / len(values) if values else -math.inf
            for values in scores
        ]


class BoundaryContextValidator:
    """Compare spaced and joined candidates using batched local MLM scores."""

    TRUSTED_EVIDENCE = frozenset(
        {
            MergeEvidenceKind.PROTECTED_TERM,
            MergeEvidenceKind.REVIEWED_DECISION,
        }
    )

    def __init__(
        self,
        settings: ContextValidatorSettings,
        scorer: VariantScorer | None = None,
        training_writer: BoundaryTrainingDataWriter | None = None,
    ) -> None:
        settings.validate()
        self.settings = settings
        self.scorer = scorer or TransformerVariantScorer(settings)
        self.training_writer = training_writer

    def validate(
        self,
        text: str,
        candidates: Sequence[MergeCandidate],
    ) -> ValidationOutcome:
        """Keep trusted decisions and context-score every automatic proposal."""

        trusted: list[MergeCandidate] = []
        pending: list[MergeCandidate] = []
        variants: list[ScoringVariant] = []
        for candidate in candidates:
            if candidate.decision.evidence.kind in self.TRUSTED_EVIDENCE:
                trusted.append(candidate)
                continue
            pending.append(candidate)
            spaced, joined = self._variants(text, candidate)
            variants.extend((spaced, joined))

        scores = self.scorer.score(variants) if variants else []
        if len(scores) != len(variants):
            raise RuntimeError("Context validator returned an invalid score count.")

        accepted = list(trusted)
        rejected: list[MergeDecision] = []
        for index, candidate in enumerate(pending):
            spaced_score, joined_score = scores[index * 2 : index * 2 + 2]
            scoreable = math.isfinite(spaced_score) and math.isfinite(
                joined_score
            )
            margin = joined_score - spaced_score if scoreable else -math.inf
            source = candidate.decision.evidence
            detail = (
                f"{source.kind.value}; spaced={spaced_score:.3f}; "
                f"joined={joined_score:.3f}; margin={margin:.3f}; "
                f"required={self.settings.merge_margin:.3f}"
            )
            evidence = MergeEvidence(
                kind=MergeEvidenceKind.TRANSFORMER_CONTEXT,
                term=candidate.decision.replacement.casefold(),
                rank=source.rank,
                dictionary_frequency=source.dictionary_frequency,
                zipf=source.zipf,
                detail=detail,
            )
            if scoreable and margin >= self.settings.merge_margin:
                confidence = min(99.0, 92.0 + min(margin, 2.0) * 3.5)
                decision = replace(
                    candidate.decision,
                    confidence=confidence,
                    reason=(
                        "Transformer context-validated OCR broken-word merge: "
                        + detail
                    ),
                    evidence=evidence,
                )
                accepted.append(replace(candidate, decision=decision))
            else:
                rejected.append(
                    replace(
                        candidate.decision,
                        confidence=99.0,
                        reason=(
                            "Transformer rejected OCR broken-word merge; text "
                            "preserved: " + detail
                        ),
                        evidence=evidence,
                    )
                )
        if self.training_writer is not None:
            try:
                self.training_writer.add(
                    self._training_examples(pending, variants, scores)
                )
            except (OSError, ValueError) as exc:
                LOGGER.warning(
                    "Classifier training-data collection failed; cleaning "
                    "will continue: %s",
                    exc,
                )
        return ValidationOutcome(tuple(accepted), tuple(rejected))

    def _training_examples(
        self,
        candidates: Sequence[MergeCandidate],
        variants: Sequence[ScoringVariant],
        scores: Sequence[float],
    ) -> list[dict[str, object]]:
        """Build advisory examples; only users supply trusted labels."""

        examples: list[dict[str, object]] = []
        for index, candidate in enumerate(candidates):
            spaced, joined = variants[index * 2 : index * 2 + 2]
            spaced_score, joined_score = scores[index * 2 : index * 2 + 2]
            margin = joined_score - spaced_score
            pieces = candidate.decision.broken_word.split(maxsplit=1)
            examples.append(
                {
                    "id": example_id(
                        spaced.text,
                        joined.text,
                        candidate.decision.broken_word,
                    ),
                    "context": spaced.text,
                    "spaced_text": spaced.text,
                    "joined_text": joined.text,
                    "left": pieces[0] if pieces else "",
                    "right": pieces[1] if len(pieces) == 2 else "",
                    "replacement": candidate.decision.replacement,
                    "transformer_label": (
                        "join"
                        if math.isfinite(margin)
                        and margin >= self.settings.merge_margin
                        else "keep_spaced"
                    ),
                    "transformer_spaced_score": (
                        spaced_score if math.isfinite(spaced_score) else None
                    ),
                    "transformer_joined_score": (
                        joined_score if math.isfinite(joined_score) else None
                    ),
                    "transformer_margin": (
                        margin if math.isfinite(margin) else None
                    ),
                    "user_label": None,
                    "review_status": "pending",
                    "reviewed_at": None,
                    "user_notes": "",
                    "evidence": candidate.decision.evidence.kind.value,
                }
            )
        return examples

    def _variants(
        self,
        text: str,
        candidate: MergeCandidate,
    ) -> tuple[ScoringVariant, ScoringVariant]:
        radius = self.settings.context_characters // 2
        window_start = max(0, candidate.start - radius)
        window_end = min(len(text), candidate.end + radius)
        local = text[window_start:window_end]
        start = candidate.start - window_start
        end = candidate.end - window_start
        replacement = candidate.decision.replacement
        joined = local[:start] + replacement + local[end:]
        return (
            ScoringVariant(local, start, end),
            ScoringVariant(joined, start, start + len(replacement)),
        )


__all__ = [
    "BoundaryContextValidator",
    "ContextValidatorSettings",
    "ScoringVariant",
    "TransformerVariantScorer",
    "ValidationOutcome",
    "VariantScorer",
]
