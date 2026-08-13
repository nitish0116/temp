"""Deterministic preflight checks for unattended subtitle workflows."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path


class PreflightError(RuntimeError):
    """A prerequisite failed before expensive processing began."""


def preflight_reprocess(
    source_path: Path,
    translated_path: Path,
    output: Path,
    *,
    diarization_path: Path | None = None,
    minimum_free_bytes: int = 100 * 1024 * 1024,
) -> dict:
    """Validate artifacts, writable output, disk space, and resume state."""
    checks = []
    documents = {}
    for name, path in (("source", source_path), ("translated", translated_path), ("diarization", diarization_path)):
        if path is None:
            continue
        if not path.is_file():
            raise PreflightError(f"Required {name} artifact not found: {path}")
        try:
            documents[name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PreflightError(f"Required {name} artifact is not valid readable JSON: {path}") from error
        checks.append({"check": f"{name}-artifact", "status": "passed", "path": str(path.resolve())})
    source_count = len(documents["source"].get("segments", []))
    target_count = len(documents["translated"].get("segments", []))
    if not source_count or source_count != target_count:
        raise PreflightError(
            f"Source/translation segment mismatch: source={source_count}, translated={target_count}"
        )
    checks.append({"check": "segment-count", "status": "passed", "count": source_count})
    try:
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=output, prefix=".preflight-", delete=True):
            pass
    except OSError as error:
        raise PreflightError(f"Output directory is not writable: {output}") from error
    checks.append({"check": "writable-output", "status": "passed", "path": str(output.resolve())})
    free = shutil.disk_usage(output).free
    if free < minimum_free_bytes:
        raise PreflightError(
            f"Insufficient disk space: {free} bytes free; {minimum_free_bytes} required"
        )
    checks.append({"check": "free-disk-space", "status": "passed", "free_bytes": free, "minimum_bytes": minimum_free_bytes})
    resumable = [
        name for name in ("canonical-subtitles.json", "qa.json", "incremental-report.json", "passed.srt", "rejected.srt")
        if (output / name).is_file()
    ]
    checks.append({"check": "resume-artifacts", "status": "passed", "found": resumable})
    return {"schema_version": 1, "passed": True, "checks": checks, "documents": documents}
