"""Compare primary subtitles with an independent multilingual translation."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Callable

try:
    from .canonical_timed_text import append_provenance, validate_canonical_timed_text
    from .qa_translation_integrity import integrity_issues
    from .runtime_device import resolve_device
    from .translate_contextual import TranslationRequest
except ImportError:
    from canonical_timed_text import append_provenance, validate_canonical_timed_text
    from qa_translation_integrity import integrity_issues
    from runtime_device import resolve_device
    from translate_contextual import TranslationRequest


AGREEMENT_PROTOCOL_VERSION = 1
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
NEGATION = re.compile(r"\b(?:no|not|never|neither|nobody|nothing|nowhere|can't|cannot|won't|isn't|aren't|wasn't|weren't|don't|doesn't|didn't)\b", re.I)


class MultilingualSimilarity:
    """Cosine similarity using a multilingual sentence-transformer checkpoint."""

    def __init__(self, model_name: str, device: str = "auto") -> None:
        """Load a Transformers encoder and use attention-mask mean pooling.

        Example:: the default MiniLM checkpoint embeds source Korean and target
        English in the same normalized 384-dimensional space.
        """
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        self.model_name = model_name
        self.device = resolve_device(device)
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModel.from_pretrained(model_name).to(self.device).eval()

    def __call__(self, left: str, right: str) -> float:
        """Return cosine similarity for two sentences in any supported language.

        Example:: semantically equivalent translations score higher than unrelated
        dialogue; selection uses relative margins instead of brittle absolute scores.
        """
        inputs = self.tokenizer(
            [left, right], padding=True, truncation=True, max_length=256,
            return_tensors="pt",
        )
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with self.torch.no_grad():
            hidden = self.model(**inputs).last_hidden_state
        mask = inputs["attention_mask"].unsqueeze(-1)
        embeddings = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
        embeddings = self.torch.nn.functional.normalize(embeddings, p=2, dim=1)
        return float((embeddings[0] @ embeddings[1]).cpu())


def agreement_issues(
    source: str, primary: str, independent: str,
    source_primary_similarity: float, source_independent_similarity: float,
    candidate_similarity: float,
    *, minimum_candidate_similarity: float = 0.72,
    minimum_consensus_source_similarity: float = 0.20,
) -> list[str]:
    """Return deterministic reasons that two target candidates need escalation.

    Example:: identical low-evidence translations trigger ``low_evidence_consensus``
    instead of being accepted merely because two small models made the same error.
    """
    issues = []
    if candidate_similarity < minimum_candidate_similarity:
        issues.append("semantic_disagreement")
    if set(NUMBER.findall(primary)) != set(NUMBER.findall(independent)):
        issues.append("number_disagreement")
    if bool(NEGATION.search(primary)) != bool(NEGATION.search(independent)):
        issues.append("polarity_disagreement")
    if (
        primary.strip().casefold() == independent.strip().casefold()
        and max(source_primary_similarity, source_independent_similarity)
        < minimum_consensus_source_similarity
    ):
        issues.append("low_evidence_consensus")
    if integrity_issues(source, independent):
        issues.append("independent_integrity_failure")
    return issues


def cache_key(source: str, source_language: str, target_language: str, model: str) -> str:
    """Hash all versioned independent-translation inputs for deterministic reuse.

    Example:: changing the source, language pair, model, or protocol changes the
    resulting cache filename.
    """
    payload = json.dumps({
        "protocol_version": AGREEMENT_PROTOCOL_VERSION,
        "source": source, "source_language": source_language,
        "target_language": target_language, "model": model,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cached_candidate(
    request: TranslationRequest, translator: Callable[[TranslationRequest], str],
    model_name: str, cache_directory: Path | None,
) -> tuple[str, bool]:
    """Read or atomically create one independent translation candidate.

    Example:: a second identical headless run returns the cached candidate without
    calling Ollama again.
    """
    key = cache_key(
        request.current_text, request.source_language,
        request.target_language, model_name,
    )
    path = None if cache_directory is None else cache_directory / f"{key}.json"
    if path and path.is_file():
        value = json.loads(path.read_text(encoding="utf-8"))
        text = str(value.get("translated_text") or "").strip()
        if text:
            return text, True
    text = str(translator(request)).strip()
    if not text:
        raise RuntimeError(f"Independent translation returned empty text for {request.group_id}")
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"translated_text": text}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    return text, False


def enforce_translation_agreement(
    document: dict,
    independent_translate: Callable[[TranslationRequest], str],
    similarity: Callable[[str, str], float],
    *,
    independent_model: str,
    cache_directory: Path | None = None,
    minimum_candidate_similarity: float = 0.72,
    minimum_consensus_source_similarity: float = 0.20,
    promotion_margin: float = 0.025,
) -> tuple[dict, dict]:
    """Promote a demonstrably better independent candidate or reject disagreement.

    Example:: ``Seoul`` can replace ``Seattle`` when the independent candidate has
    higher source similarity and passes the same deterministic integrity checks.
    """
    validate_canonical_timed_text(document)
    output = deepcopy(document)
    checks = []
    segments = output["segments"]
    for index, segment in enumerate(segments):
        source = str(segment.get("source_text") or "")
        primary = str(segment.get("translated_text") or "")
        request = TranslationRequest(
            group_id=f"agreement-{segment['semantic_group_id']}",
            source_language=output["source_language"],
            target_language=output["output_language"],
            current_text=source,
            previous=tuple(
                str(item.get("source_text") or "") for item in segments[max(0, index - 3):index]
            ),
            following=tuple(
                str(item.get("source_text") or "") for item in segments[index + 1:index + 4]
            ),
            required_numbers=tuple(NUMBER.findall(source)),
        )
        independent, cache_hit = cached_candidate(
            request, independent_translate, independent_model, cache_directory,
        )
        source_primary = similarity(source, primary)
        source_independent = similarity(source, independent)
        candidates = similarity(primary, independent)
        issues = agreement_issues(
            source, primary, independent, source_primary, source_independent,
            candidates, minimum_candidate_similarity=minimum_candidate_similarity,
            minimum_consensus_source_similarity=minimum_consensus_source_similarity,
        )
        selected = "primary"
        passed = not issues
        if issues and not integrity_issues(source, independent):
            if source_independent >= source_primary + promotion_margin:
                segment["translated_text"] = independent
                segment["provenance"] = append_provenance(
                    segment, "translation-agreement", "independent-candidate-promotion",
                    model=independent_model, issues=issues,
                    source_primary_similarity=round(source_primary, 4),
                    source_independent_similarity=round(source_independent, 4),
                )
                selected, passed = "independent", True
        checks.append({
            "semantic_group_id": segment["semantic_group_id"],
            "primary": primary, "independent": independent,
            "selected": selected, "passed": passed, "issues": issues,
            "cache_hit": cache_hit,
            "source_primary_similarity": round(source_primary, 4),
            "source_independent_similarity": round(source_independent, 4),
            "candidate_similarity": round(candidates, 4),
        })
    report = {
        "schema_version": 1, "protocol_version": AGREEMENT_PROTOCOL_VERSION,
        "passed": all(check["passed"] for check in checks),
        "group_count": len(checks),
        "failed_group_count": sum(not check["passed"] for check in checks),
        "independent_model": independent_model,
        "checks": checks,
    }
    output["metadata"] = {**output.get("metadata", {}), "translation_agreement": report}
    validate_canonical_timed_text(output)
    return output, report
