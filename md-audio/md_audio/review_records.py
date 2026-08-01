"""Shared JSON and path helpers for the hyphen-review commands."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any


ReviewRecord = dict[str, Any]


def utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp for generated review metadata."""
    return datetime.now(timezone.utc).isoformat()


def portable_path(path: Path, base: Path) -> str:
    """Return a POSIX-style relative path without a machine-specific root."""
    try:
        return Path(os.path.relpath(path.resolve(), base.resolve())).as_posix()
    except ValueError:
        return path.name


def load_review_record(path: Path) -> ReviewRecord:
    """Load a review object and validate that it has a candidate list."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not load review JSON '{path}': {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("candidates"), list):
        raise SystemExit(f"Review JSON has no candidates list: {path}")
    return value


def write_review_record(path: Path, record: ReviewRecord, *, compact: bool = False) -> None:
    """Write a UTF-8 review record, creating its parent directory as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    else:
        payload = json.dumps(record, ensure_ascii=False, indent=2)
    path.write_text(payload + "\n", encoding="utf-8")
