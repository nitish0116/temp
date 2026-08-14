"""Compare primary subtitles with an independent multilingual translation."""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Callable

try:
    from .canonical_timed_text import append_provenance, validate_canonical_timed_text
    from .qa_translation_integrity import integrity_issues
    from .runtime_device import resolve_device
    from .translate_contextual import TranslationRequest, valid_translation_response
except ImportError:
    from canonical_timed_text import append_provenance, validate_canonical_timed_text
    from qa_translation_integrity import integrity_issues
    from runtime_device import resolve_device
    from translate_contextual import TranslationRequest, valid_translation_response


AGREEMENT_PROTOCOL_VERSION = 1
MAX_EMBEDDING_TOKENS = 256
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
NEGATION = re.compile(r"\b(?:no|not|never|neither|nobody|nothing|nowhere|can't|cannot|won't|isn't|aren't|wasn't|weren't|don't|doesn't|didn't)\b", re.I)


class MultilingualSimilarity:
    """Cosine similarity using a multilingual sentence-transformer checkpoint."""

    def __init__(
        self, model_name: str, device: str = "auto", *, local_files_only: bool = False,
    ) -> None:
        """Load a Transformers encoder and use attention-mask mean pooling.

        Example:: the default MiniLM checkpoint embeds source Korean and target
        English in the same normalized 384-dimensional space.
        """
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        self.model_name = model_name
        self.device = resolve_device(device)
        if self.device == "cpu":
            torch.set_num_threads(max(torch.get_num_threads(), min(os.cpu_count() or 1, 8)))
        print(f"Loading semantic tokenizer from local cache: {model_name}", flush=True)
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_name, local_files_only=local_files_only,
        )
        print(f"Loading semantic encoder on {self.device}: {model_name}", flush=True)
        self.model = AutoModel.from_pretrained(
            model_name, local_files_only=local_files_only,
        ).to(self.device).eval()
        print(f"Semantic encoder ready: {model_name}", flush=True)
        self._embedding_cache: dict[str, object] = {}

    def prepare(self, texts: list[str], batch_size: int = 64) -> None:
        """Precompute normalized embeddings in batches for repeated comparisons.

        Example:: a 200-cue episode embeds each unique source and candidate once,
        rather than launching three encoder passes for every cue.
        """
        pending = sorted(
            dict.fromkeys(text for text in texts if text not in self._embedding_cache),
            key=len,
        )
        for offset in range(0, len(pending), batch_size):
            batch = pending[offset:offset + batch_size]
            inputs = self.tokenizer(
                batch, padding=True, truncation=True, max_length=MAX_EMBEDDING_TOKENS,
                return_tensors="pt",
            )
            inputs = {key: value.to(self.device) for key, value in inputs.items()}
            with self.torch.no_grad():
                hidden = self.model(**inputs).last_hidden_state
            mask = inputs["attention_mask"].unsqueeze(-1)
            embeddings = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
            embeddings = self.torch.nn.functional.normalize(embeddings, p=2, dim=1).cpu()
            self._embedding_cache.update(zip(batch, embeddings))
            print(
                f"Semantic embeddings: {min(offset + len(batch), len(pending))}/"
                f"{len(pending)} new texts",
                flush=True,
            )

    def __call__(self, left: str, right: str) -> float:
        """Return cosine similarity for two sentences in any supported language.

        Example:: semantically equivalent translations score higher than unrelated
        dialogue; selection uses relative margins instead of brittle absolute scores.
        """
        self.prepare([left, right])
        return float(self._embedding_cache[left] @ self._embedding_cache[right])


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
    retry_translate: Callable[[TranslationRequest], str] | None = None,
    retry_model: str | None = None,
    cache_directory: Path | None = None,
    minimum_candidate_similarity: float = 0.72,
    minimum_consensus_source_similarity: float = 0.20,
    promotion_margin: float = 0.025,
    maximum_invalid_candidate_rate: float = 0.25,
    minimum_backend_health_sample_size: int = 4,
    backend_health_probe_size: int = 8,
) -> tuple[dict, dict]:
    """Promote a demonstrably better independent candidate or reject disagreement.

    Example:: ``Seoul`` can replace ``Seattle`` when the independent candidate has
    higher source similarity and passes the same deterministic integrity checks.
    """
    validate_canonical_timed_text(document)
    output = deepcopy(document)
    checks = []
    pending_retries = []
    segments = output["segments"]
    prepared_candidates = []
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
        prepared_candidates.append((request, independent, cache_hit))
        probe_size = min(len(segments), backend_health_probe_size)
        if (
            probe_size >= minimum_backend_health_sample_size
            and len(prepared_candidates) == probe_size
        ):
            invalid_probe_count = sum(
                not valid_translation_response(candidate, candidate_request)
                for candidate_request, candidate, _cache_hit in prepared_candidates
            )
            invalid_probe_rate = invalid_probe_count / probe_size
            if invalid_probe_rate > maximum_invalid_candidate_rate:
                checks = []
                for probe_index, probe_segment in enumerate(segments):
                    evaluated = probe_index < probe_size
                    probe_request, candidate, cache_hit = (
                        prepared_candidates[probe_index]
                        if evaluated else (None, None, False)
                    )
                    checks.append({
                        "semantic_group_id": probe_segment["semantic_group_id"],
                        "primary": str(probe_segment.get("translated_text") or ""),
                        "independent": candidate,
                        "retry": None, "selected": "primary", "passed": False,
                        "issues": [
                            "independent_backend_output_contract_failure"
                            if evaluated else "independent_backend_not_evaluated"
                        ],
                        "primary_output_valid": (
                            valid_translation_response(
                                str(probe_segment.get("translated_text") or ""),
                                probe_request,
                            ) if evaluated else None
                        ),
                        "independent_output_valid": (
                            valid_translation_response(candidate, probe_request)
                            if evaluated else False
                        ),
                        "cache_hit": cache_hit,
                        "source_primary_similarity": None,
                        "source_independent_similarity": None,
                        "source_retry_similarity": None,
                        "candidate_similarity": None,
                    })
                report = {
                    "schema_version": 1,
                    "protocol_version": AGREEMENT_PROTOCOL_VERSION,
                    "passed": False, "group_count": len(checks),
                    "failed_group_count": len(checks),
                    "independent_model": independent_model,
                    "retry_model": retry_model,
                    "backend_issue": "independent_backend_output_contract_failure",
                    "invalid_independent_candidate_count": invalid_probe_count,
                    "invalid_independent_candidate_rate": round(invalid_probe_rate, 4),
                    "backend_health_probe_count": probe_size,
                    "checks": checks,
                }
                print(
                    f"Translation agreement stopped after {probe_size}-group health probe: "
                    f"independent invalid rate {invalid_probe_rate:.1%}", flush=True,
                )
                output["metadata"] = {
                    **output.get("metadata", {}), "translation_agreement": report,
                }
                validate_canonical_timed_text(output)
                return output, report
    prepare_similarity = getattr(similarity, "prepare", None)
    if callable(prepare_similarity):
        prepare_similarity([
            text
            for index, segment in enumerate(segments)
            for text in (
                str(segment.get("source_text") or ""),
                str(segment.get("translated_text") or ""),
                prepared_candidates[index][1],
            )
        ])
    for index, segment in enumerate(segments):
        source = str(segment.get("source_text") or "")
        primary = str(segment.get("translated_text") or "")
        request, independent, cache_hit = prepared_candidates[index]
        source_primary = similarity(source, primary)
        source_independent = similarity(source, independent)
        candidates = similarity(primary, independent)
        issues = agreement_issues(
            source, primary, independent, source_primary, source_independent,
            candidates, minimum_candidate_similarity=minimum_candidate_similarity,
            minimum_consensus_source_similarity=minimum_consensus_source_similarity,
        )
        primary_valid = valid_translation_response(primary, request)
        independent_valid = valid_translation_response(independent, request)
        if not primary_valid:
            issues.append("primary_output_contract_failure")
        if not independent_valid:
            issues.append("independent_output_contract_failure")
        selected = "primary"
        retry = None
        source_retry = None
        passed = not issues
        if issues and independent_valid and not integrity_issues(source, independent):
            if source_independent >= source_primary + promotion_margin:
                segment["translated_text"] = independent
                segment["provenance"] = append_provenance(
                    segment, "translation-agreement", "independent-candidate-promotion",
                    model=independent_model, issues=issues,
                    source_primary_similarity=round(source_primary, 4),
                    source_independent_similarity=round(source_independent, 4),
                )
                selected, passed = "independent", True
        if issues and not passed and retry_translate is not None:
            pending_retries.append((
                index, request, source, source_primary, source_independent, issues,
            ))
        checks.append({
            "semantic_group_id": segment["semantic_group_id"],
            "primary": primary, "independent": independent,
            "retry": retry,
            "selected": selected, "passed": passed, "issues": issues,
            "primary_output_valid": primary_valid,
            "independent_output_valid": independent_valid,
            "cache_hit": cache_hit,
            "source_primary_similarity": round(source_primary, 4),
            "source_independent_similarity": round(source_independent, 4),
            "source_retry_similarity": None if source_retry is None else round(source_retry, 4),
            "candidate_similarity": round(candidates, 4),
        })
        completed = index + 1
        if completed == len(segments) or completed % 10 == 0:
            print(
                f"Translation agreement: {completed}/{len(segments)} groups; "
                f"failed={sum(not check['passed'] for check in checks)}",
                flush=True,
            )
    invalid_candidate_count = sum(
        not check["independent_output_valid"] for check in checks
    )
    invalid_candidate_rate = (
        invalid_candidate_count / len(checks) if checks else 0.0
    )
    backend_issue = None
    if (
        len(checks) >= minimum_backend_health_sample_size
        and invalid_candidate_rate > maximum_invalid_candidate_rate
    ):
        backend_issue = "independent_backend_output_contract_failure"
        pending_retries = []
        output = deepcopy(document)
        segments = output["segments"]
        for check in checks:
            check["selected"] = "primary"
            check["passed"] = False
            if backend_issue not in check["issues"]:
                check["issues"].append(backend_issue)
        print(
            f"Translation agreement stopped retries: independent invalid rate "
            f"{invalid_candidate_rate:.1%} exceeds {maximum_invalid_candidate_rate:.1%}",
            flush=True,
        )
    prepared_retries = []
    for pending in pending_retries:
        index, request, source, source_primary, source_independent, issues = pending
        segment = segments[index]
        retry_request = TranslationRequest(
            group_id=f"agreement-retry-{segment['semantic_group_id']}",
            source_language=request.source_language,
            target_language=request.target_language,
            current_text=request.current_text,
            previous=request.previous,
            following=request.following,
            required_numbers=request.required_numbers,
        )
        try:
            retry, _retry_cache_hit = cached_candidate(
                retry_request, retry_translate, retry_model or "stronger-retry",
                cache_directory,
            )
        except Exception as error:
            checks[index]["retry_error"] = f"{type(error).__name__}: {error}"
            print(
                f"Translation agreement retry failed for "
                f"{segment['semantic_group_id']}: {type(error).__name__}",
                flush=True,
            )
            continue
        prepared_retries.append((pending, retry, retry_request))
    if callable(prepare_similarity):
        prepare_similarity([retry for _pending, retry, _request in prepared_retries])
    for retry_index, prepared in enumerate(prepared_retries, start=1):
        pending, retry, retry_request = prepared
        index, request, source, source_primary, source_independent, issues = pending
        segment = segments[index]
        source_retry = similarity(source, retry)
        check = checks[index]
        check["retry"] = retry
        check["source_retry_similarity"] = round(source_retry, 4)
        retry_valid = valid_translation_response(retry, retry_request)
        retry_confirms_primary = (
            retry.strip().casefold() == check["primary"].strip().casefold()
            and valid_translation_response(check["primary"], retry_request)
        )
        valid_baselines = [
            score for score, valid in (
                (source_primary, check["primary_output_valid"]),
                (source_independent, check["independent_output_valid"]),
            ) if valid
        ]
        retry_improves = (
            source_retry >= max(valid_baselines, default=-1.0) + promotion_margin
            or not valid_translation_response(check["primary"], retry_request)
        )
        if not integrity_issues(source, retry) and retry_valid and (
            retry_confirms_primary or retry_improves
        ):
            segment["translated_text"] = retry
            segment["provenance"] = append_provenance(
                segment, "translation-agreement", "stronger-model-retry",
                model=retry_model or "stronger-retry", issues=issues,
                source_primary_similarity=round(source_primary, 4),
                source_independent_similarity=round(source_independent, 4),
                source_retry_similarity=round(source_retry, 4),
            )
            method = "retry-confirmed-primary" if retry_confirms_primary else "retry"
            check["selected"], check["passed"] = method, True
        if retry_index == len(prepared_retries) or retry_index % 5 == 0:
            print(
                f"Translation agreement retries: {retry_index}/{len(prepared_retries)}; "
                f"unresolved={sum(not item['passed'] for item in checks)}",
                flush=True,
            )
    report = {
        "schema_version": 1, "protocol_version": AGREEMENT_PROTOCOL_VERSION,
        "passed": all(check["passed"] for check in checks),
        "group_count": len(checks),
        "failed_group_count": sum(not check["passed"] for check in checks),
        "independent_model": independent_model,
        "retry_model": retry_model,
        "backend_issue": backend_issue,
        "invalid_independent_candidate_count": invalid_candidate_count,
        "invalid_independent_candidate_rate": round(invalid_candidate_rate, 4),
        "checks": checks,
    }
    output["metadata"] = {**output.get("metadata", {}), "translation_agreement": report}
    validate_canonical_timed_text(output)
    return output, report
