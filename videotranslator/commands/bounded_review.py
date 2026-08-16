"""Build portable, versioned review manifests for unresolved subtitle groups."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Callable

from .canonical_timed_text import append_provenance, validate_canonical_timed_text
from .qa_translation_integrity import adjudication_coverage_issues, integrity_issues


NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
LATIN_IDENTIFIER = re.compile(r"(?<![\w])(?:[A-Za-z][A-Za-z0-9]*(?:[-'][A-Za-z0-9]+)*)(?![\w])")
REVIEW_SCHEMA_VERSION = 1


def sha256_file(path: Path) -> str:
    """Hash a source media or extracted review clip without loading it at once."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def approval_key(item: dict, *, media_sha256: str, model: str, protocol_version: int) -> str:
    """Bind a future decision to the exact source region and evidence package."""
    value = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "protocol_version": protocol_version,
        "model": model,
        "media_sha256": media_sha256,
        "start": item["start"],
        "end": item["end"],
        "source_text": item["source_text"],
        "candidates": item["candidates"],
    }
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def build_bounded_review(
    document: dict, adjudication_report: dict, *, sample_id: str,
    source_media: str, media_sha256: str, adjudication_model: str,
    context_size: int = 3,
) -> dict:
    """Return pending review items for only unresolved adjudication groups."""
    checks = {item["semantic_group_id"]: item for item in adjudication_report["checks"]}
    items = []
    segments = document["segments"]
    for index, segment in enumerate(segments):
        group_id = str(segment["semantic_group_id"])
        check = checks[group_id]
        if check["passed"]:
            continue
        metadata = segment.get("metadata", {})
        candidates = {
            "primary": str(segment.get("translated_text") or ""),
            "dedicated_mt": str(metadata.get("dedicated_mt", {}).get("text") or ""),
            "speech_translation": str(metadata.get("speech_translation", {}).get("text") or ""),
        }
        item = {
            "semantic_group_id": group_id,
            "start": segment["start"],
            "end": segment["end"],
            "speaker": segment.get("speaker"),
            "confidence": segment.get("confidence"),
            "source_text": str(segment.get("source_text") or ""),
            "previous_source": [str(value.get("source_text") or "") for value in segments[max(0, index-context_size):index]],
            "following_source": [str(value.get("source_text") or "") for value in segments[index+1:index+context_size+1]],
            "candidates": candidates,
            "source_numbers": NUMBER.findall(str(segment.get("source_text") or "")),
            "source_latin_identifiers": LATIN_IDENTIFIER.findall(str(segment.get("source_text") or "")),
            "proposed_correction": check.get("proposed_translation"),
            "blocking_error": check.get("error"),
            "adjudicator_reason": check.get("reason"),
            "audio_clip": None,
            "audio_clip_sha256": None,
            "terminal_state": "unresolved",
            "review": {"status": "pending", "translation": None, "reviewer": None, "reviewed_at": None},
        }
        item["approval_key"] = approval_key(
            item, media_sha256=media_sha256, model=adjudication_model,
            protocol_version=int(adjudication_report["protocol_version"]),
        )
        items.append(item)
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "artifact_type": "bounded_subtitle_review",
        "sample_id": sample_id,
        "source_media": source_media,
        "source_media_sha256": media_sha256,
        "adjudication_model": adjudication_model,
        "adjudication_protocol_version": adjudication_report["protocol_version"],
        "review_item_count": len(items),
        "status": "pending" if items else "resolved",
        "items": items,
    }


def attach_audio_clips(
    review: dict, clip_directory: Path, clip_directory_reference: str,
    extract: Callable[[float, float, Path], None], *, padding_seconds: float = 0.5,
) -> dict:
    """Extract and hash one bounded audio clip for every pending review item."""
    clip_directory.mkdir(parents=True, exist_ok=True)
    reference_root = clip_directory_reference.rstrip("/")
    for item in review["items"]:
        path = clip_directory / f"{item['semantic_group_id']}.wav"
        start = max(0.0, float(item["start"]) - padding_seconds)
        end = float(item["end"]) + padding_seconds
        extract(start, end, path)
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Review clip extraction produced no audio: {path}")
        item["audio_clip"] = f"{reference_root}/{path.name}"
        item["audio_clip_sha256"] = sha256_file(path)
    return review


def _reviewed_at(value: object) -> str:
    """Require an ISO-8601 reviewer timestamp with an explicit timezone."""
    if not isinstance(value, str):
        raise ValueError("reviewed_at must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("reviewed_at must be an ISO-8601 string") from error
    if parsed.tzinfo is None:
        raise ValueError("reviewed_at must include a timezone")
    return value


def apply_review_decisions(document: dict, review: dict, decisions: dict) -> tuple[dict, dict]:
    """Apply exact-key human decisions while retaining every unsafe item unresolved."""
    validate_canonical_timed_text(document)
    if decisions.get("sample_id") != review.get("sample_id"):
        raise ValueError("decision sample_id does not match review artifact")
    supplied = decisions.get("decisions")
    if not isinstance(supplied, list):
        raise ValueError("decisions must be a list")
    by_id = {}
    for decision in supplied:
        group_id = str(decision.get("semantic_group_id") or "")
        if not group_id or group_id in by_id:
            raise ValueError("decision semantic_group_id values must be unique and nonempty")
        by_id[group_id] = decision
    output = deepcopy(document)
    segments = {str(item["semantic_group_id"]): item for item in output["segments"]}
    results = []
    for item in review["items"]:
        group_id = str(item["semantic_group_id"])
        segment = segments.get(group_id)
        if segment is None:
            raise ValueError(f"review group is missing from document: {group_id}")
        metadata = segment.get("metadata", {})
        current_candidates = {
            "primary": str(segment.get("translated_text") or ""),
            "dedicated_mt": str(metadata.get("dedicated_mt", {}).get("text") or ""),
            "speech_translation": str(metadata.get("speech_translation", {}).get("text") or ""),
        }
        if (
            item["start"] != segment["start"] or item["end"] != segment["end"]
            or item["source_text"] != str(segment.get("source_text") or "")
            or item["candidates"] != current_candidates
        ):
            raise ValueError(f"review evidence is stale for {group_id}")
        expected_key = approval_key(
            item, media_sha256=review["source_media_sha256"],
            model=review["adjudication_model"],
            protocol_version=int(review["adjudication_protocol_version"]),
        )
        if item.get("approval_key") != expected_key:
            raise ValueError(f"review approval key is stale for {group_id}")
        decision = by_id.get(group_id)
        if decision is None:
            results.append({"semantic_group_id": group_id, "status": "unresolved", "reason": "no decision supplied"})
            continue
        if decision.get("approval_key") != expected_key:
            raise ValueError(f"decision approval key does not match {group_id}")
        status = decision.get("status")
        if status not in {"human_verified", "unresolved"}:
            raise ValueError(f"unsupported review status for {group_id}: {status}")
        reviewer = str(decision.get("reviewer") or "").strip()
        if not reviewer:
            raise ValueError(f"reviewer is required for {group_id}")
        reviewed_at = _reviewed_at(decision.get("reviewed_at"))
        if status == "human_verified":
            translation = str(decision.get("translation") or "").strip()
            issues = integrity_issues(item["source_text"], translation)
            issues.extend(adjudication_coverage_issues(item["source_text"], translation))
            if issues:
                kinds = ", ".join(str(issue["type"]) for issue in issues)
                raise ValueError(f"human translation failed integrity checks for {group_id}: {kinds}")
            segment["translated_text"] = translation
            segment["provenance"] = append_provenance(
                segment, "bounded-human-review", "approval-key-verified",
                reviewer=reviewer, reviewed_at=reviewed_at, approval_key=expected_key,
            )
        results.append({
            "semantic_group_id": group_id, "status": status,
            "reviewer": reviewer, "reviewed_at": reviewed_at,
        })
    unknown = sorted(set(by_id) - {str(item["semantic_group_id"]) for item in review["items"]})
    if unknown:
        raise ValueError(f"decisions contain groups outside the bounded review: {unknown}")
    report = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "sample_id": review["sample_id"],
        "passed": bool(results) and all(item["status"] == "human_verified" for item in results),
        "review_item_count": len(results),
        "human_verified_count": sum(item["status"] == "human_verified" for item in results),
        "unresolved_count": sum(item["status"] != "human_verified" for item in results),
        "results": results,
    }
    output["metadata"] = {**output.get("metadata", {}), "bounded_human_review": report}
    validate_canonical_timed_text(output)
    return output, report


def stratified_accepted_group_ids(adjudication_report: dict, sample_size: int = 8) -> list[str]:
    """Select reproducible accepted groups across early, middle, and late strata."""
    if sample_size < 1:
        raise ValueError("sample_size must be positive")
    accepted = [
        str(item["semantic_group_id"]) for item in adjudication_report["checks"]
        if item["passed"]
    ]
    if len(accepted) <= sample_size:
        return accepted
    strata = [accepted[:len(accepted)//3], accepted[len(accepted)//3:2*len(accepted)//3], accepted[2*len(accepted)//3:]]
    base, remainder = divmod(sample_size, 3)
    selected = []
    for index, values in enumerate(strata):
        count = min(len(values), base + (1 if index < remainder else 0))
        if count == 1:
            chosen = [values[len(values)//2]]
        elif count > 1:
            chosen = [values[round(position * (len(values)-1) / (count-1))] for position in range(count)]
        else:
            chosen = []
        selected.extend(chosen)
    return selected


def build_accepted_audit(
    document: dict, adjudication_report: dict, *, sample_id: str,
    source_media: str, media_sha256: str, adjudication_model: str,
    sample_size: int = 8, context_size: int = 3,
) -> dict:
    """Build a hashed human-audit artifact from stratified accepted groups."""
    selected = set(stratified_accepted_group_ids(adjudication_report, sample_size))
    audit_report = deepcopy(adjudication_report)
    for check in audit_report["checks"]:
        group_id = str(check["semantic_group_id"])
        check["passed"] = group_id not in selected
        if group_id in selected:
            check["proposed_translation"] = check.get("selected_translation")
            check["error"] = None
            check["reason"] = "selected for stratified accepted-group audit"
    audit = build_bounded_review(
        document, audit_report, sample_id=sample_id, source_media=source_media,
        media_sha256=media_sha256, adjudication_model=adjudication_model,
        context_size=context_size,
    )
    audit["artifact_type"] = "accepted_subtitle_audit"
    audit["selection"] = {
        "method": "deterministic_early_middle_late",
        "requested_size": sample_size,
        "selected_size": len(audit["items"]),
    }
    for item in audit["items"]:
        item["terminal_state"] = "adjudicator_verified"
        item["review"]["status"] = "audit_pending"
    return audit
