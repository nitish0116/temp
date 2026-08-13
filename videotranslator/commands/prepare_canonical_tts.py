"""Prepare approved canonical timed text for TTS and dubbing consumers."""

from __future__ import annotations

from copy import deepcopy

try:
    from .canonical_timed_text import append_provenance, validate_canonical_timed_text
except ImportError:
    from canonical_timed_text import append_provenance, validate_canonical_timed_text


def canonical_is_approved(document: dict) -> bool:
    """Require an explicit passing promotion or translation-integrity decision."""
    metadata = document.get("metadata", {})
    return bool(
        metadata.get("approval", {}).get("status") == "approved"
        or metadata.get("translation_integrity", {}).get("passed") is True
    )


def prepare_canonical_tts(
    document: dict,
    speaker_voices: dict[str, str] | None = None,
    *,
    require_approval: bool = True,
) -> dict:
    """Create a compatibility TTS script without losing canonical lineage."""
    validate_canonical_timed_text(document)
    if document["stage"] != "translated":
        raise ValueError("TTS requires translated canonical timed text")
    if require_approval and not canonical_is_approved(document):
        raise ValueError("TTS requires explicitly approved canonical timed text")
    voices = speaker_voices or {}
    segments = []
    for cue in document["segments"]:
        text = (cue.get("translated_text") or "").strip()
        if not text:
            raise ValueError(f"TTS cue {cue['id']} has no translated text")
        voice = cue.get("metadata", {}).get("voice") or voices.get(cue["speaker"])
        if not voice:
            raise ValueError(f"No voice assigned for speaker {cue['speaker']}")
        segments.append({
            **deepcopy(cue),
            "text": text,
            "voice": voice,
            "duration_constraint": {
                **deepcopy(cue.get("metadata", {}).get("duration_constraint", {})),
                "available_seconds": round(float(cue["end"]) - float(cue["start"]), 3),
            },
            "provenance": append_provenance(
                cue, "tts-handoff", "approved-canonical-timed-text",
                voice=voice, speaker=cue["speaker"],
            ),
        })
    return {
        "schema_version": 1,
        "project_id": str(document.get("metadata", {}).get("project_id", "canonical-subtitles")),
        "source_schema_version": document["schema_version"],
        "source_language": document["source_language"],
        "output_language": document["output_language"],
        "approval": {"status": "approved", "source": "canonical-quality-gate"},
        "segments": segments,
    }
