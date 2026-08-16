"""Build portable, versioned review manifests for unresolved subtitle groups."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable


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
