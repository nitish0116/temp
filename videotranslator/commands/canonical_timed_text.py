"""Versioned canonical timed-text validation and legacy compatibility."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


SCHEMA_VERSION = 1
ARTIFACT_TYPE = "canonical_timed_text"
STAGES = {"raw_asr", "clean_transcript", "canonical_source", "translated"}
SEGMENT_FIELDS = {
    "id", "semantic_group_id", "source_cue_ids", "start", "end",
    "source_text", "translated_text", "speaker", "words", "confidence",
    "provenance", "metadata",
}
DOCUMENT_FIELDS = {
    "schema_version", "artifact_type", "stage", "source_language",
    "output_language", "language_probability", "metadata", "segments",
}


def provenance_entries(value: Any) -> list[dict[str, Any]]:
    """Normalize old string provenance into the canonical event representation."""
    if value is None:
        return []
    if isinstance(value, list):
        return deepcopy(value)
    if isinstance(value, dict):
        return [deepcopy(value)]
    return [{"stage": "legacy", "method": str(value)}]


def append_provenance(segment: dict[str, Any], stage: str, method: str, **details: Any) -> list[dict[str, Any]]:
    """Return prior lineage plus one machine-readable stage event."""
    return [
        *provenance_entries(segment.get("provenance")),
        {"stage": stage, "method": method, **details},
    ]


def validate_canonical_timed_text(document: dict[str, Any]) -> None:
    """Validate required types and invariants without a runtime dependency."""
    unexpected_document_fields = set(document) - DOCUMENT_FIELDS
    if unexpected_document_fields:
        raise ValueError(
            "Unexpected canonical fields: " + ", ".join(sorted(unexpected_document_fields))
        )
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported canonical schema version: {document.get('schema_version')!r}")
    if document.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("artifact_type must be 'canonical_timed_text'")
    if document.get("stage") not in STAGES:
        raise ValueError(f"Unsupported canonical stage: {document.get('stage')!r}")
    for field in ("source_language", "output_language"):
        if not isinstance(document.get(field), str) or not document[field]:
            raise ValueError(f"{field} must be a nonempty string")
    segments = document.get("segments")
    if not isinstance(segments, list):
        raise ValueError("segments must be an array")
    identifiers: set[str] = set()
    for index, segment in enumerate(segments):
        missing = SEGMENT_FIELDS - set(segment)
        if missing:
            raise ValueError(f"segment {index} is missing fields: {', '.join(sorted(missing))}")
        unexpected = set(segment) - SEGMENT_FIELDS
        if unexpected:
            raise ValueError(
                f"segment {index} has unexpected fields: {', '.join(sorted(unexpected))}"
            )
        identifier = segment["id"]
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"segment {index} id must be a nonempty string")
        if identifier in identifiers:
            raise ValueError(f"duplicate segment id: {identifier}")
        identifiers.add(identifier)
        if not isinstance(segment["semantic_group_id"], str) or not segment["semantic_group_id"]:
            raise ValueError(f"segment {identifier} requires semantic_group_id")
        if not isinstance(segment["source_cue_ids"], list) or not segment["source_cue_ids"]:
            raise ValueError(f"segment {identifier} requires source_cue_ids")
        if any(not isinstance(item, (str, int)) for item in segment["source_cue_ids"]):
            raise ValueError(f"segment {identifier} has invalid source_cue_ids")
        start, end = segment["start"], segment["end"]
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or start < 0 or end <= start:
            raise ValueError(f"segment {identifier} has invalid timing")
        for field in ("words", "provenance"):
            if not isinstance(segment[field], list):
                raise ValueError(f"segment {identifier} {field} must be an array")
        for field in ("confidence", "metadata"):
            if not isinstance(segment[field], dict):
                raise ValueError(f"segment {identifier} {field} must be an object")
        for field in ("source_text", "translated_text"):
            if segment[field] is not None and not isinstance(segment[field], str):
                raise ValueError(f"segment {identifier} {field} must be text or null")
        if not isinstance(segment["speaker"], str) or not segment["speaker"]:
            raise ValueError(f"segment {identifier} speaker must be a nonempty string")


def adapt_legacy_transcript(document: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a start/end/text transcript while retaining all legacy fields."""
    if document.get("artifact_type") == ARTIFACT_TYPE:
        upgraded = deepcopy(document)
        validate_canonical_timed_text(upgraded)
        return upgraded
    if not isinstance(document.get("segments"), list):
        raise ValueError("Legacy transcript must contain a segments array")
    task = document.get("task", "transcribe")
    source_language = str(document.get("language") or "und")
    output_language = str(document.get("output_language") or source_language)
    translated = task == "translate"
    segments = []
    for index, legacy in enumerate(document["segments"], start=1):
        cue_id = legacy.get("id", index)
        legacy_extra = {key: deepcopy(value) for key, value in legacy.items() if key not in {"start", "end", "text", "words", "speaker", "provenance"}}
        provenance = provenance_entries(legacy.get("provenance"))
        provenance.append({"stage": "schema-migration", "method": "legacy-transcript-adapter-v1"})
        segments.append({
            "id": f"cue-{int(cue_id):04d}" if isinstance(cue_id, int) else str(cue_id),
            "semantic_group_id": str(legacy.get("semantic_group_id") or f"group-{index:04d}"),
            "source_cue_ids": deepcopy(legacy.get("source_cue_ids") or [cue_id]),
            "start": legacy["start"],
            "end": legacy["end"],
            "source_text": legacy.get("source_text") if translated else legacy.get("text"),
            "translated_text": legacy.get("text") if translated else legacy.get("translated_text"),
            "speaker": str(legacy.get("speaker") or "unknown"),
            "words": deepcopy(legacy.get("words") or []),
            "confidence": deepcopy(legacy.get("confidence") or {}),
            "provenance": provenance,
            "metadata": legacy_extra,
        })
    canonical = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "stage": "translated" if translated else "canonical_source",
        "source_language": source_language,
        "output_language": output_language,
        "language_probability": document.get("language_probability"),
        "metadata": {key: deepcopy(value) for key, value in document.items() if key not in {"segments", "language", "output_language", "language_probability"}},
        "segments": segments,
    }
    validate_canonical_timed_text(canonical)
    return canonical


def to_legacy_transcript(document: dict[str, Any]) -> dict[str, Any]:
    """Return a compatibility transcript for current start/end/text consumers."""
    validate_canonical_timed_text(document)
    translated = document["stage"] == "translated"
    legacy = deepcopy(document.get("metadata", {}))
    legacy.update({
        "language": document["source_language"],
        "language_probability": document.get("language_probability"),
        "task": "translate" if translated else "transcribe",
        "output_language": document["output_language"],
        "segments": [],
    })
    for segment in document["segments"]:
        item = deepcopy(segment["metadata"])
        item.update({
            "start": segment["start"],
            "end": segment["end"],
            "text": segment["translated_text"] if translated else segment["source_text"],
        })
        if segment["words"]:
            item["words"] = deepcopy(segment["words"])
        if segment["speaker"] != "unknown":
            item["speaker"] = segment["speaker"]
        legacy["segments"].append(item)
    return legacy
