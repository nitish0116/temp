"""
modules/report/change_log.py

Change tracking and JSON export.
"""

from __future__ import annotations

import json

from dataclasses import (
    dataclass,
    asdict,
)

from pathlib import Path

from datetime import datetime


@dataclass
class ChangeRecord:
    """One text correction event.

    Example:
        ``instance = ChangeRecord("RegexOCR", 0, 0, 1, "teh", "the", 98.0, "Safe correction", "2026-01-01T00:00:00")``
        Expected behavior: One text correction event.
    """

    stage: str

    block_index: int

    segment_index: int

    line: int

    before: str

    after: str

    confidence: float

    reason: str

    timestamp: str


class ChangeLog:
    """Stores all pipeline corrections.

    Example:
        ``instance = ChangeLog()``
        Expected behavior: Stores all pipeline corrections.
    """

    def __init__(self):
        """Initialize an empty ordered collection of change records.

        Example:
            ``instance = ChangeLog()``
            Expected behavior: Initialize an empty ordered collection of change records.
        """

        self.records = []

    # ---------------------------------------------------------

    def add(
        self,
        *,
        stage,
        block_index,
        segment_index,
        line,
        before,
        after,
        confidence,
        reason,
    ):
        """Add correction record.

        Example:
            ``instance.add(stage="RegexOCR", block_index=0, segment_index=0, line=1, before="teh", after="the", confidence=98.0, reason="Safe correction")``
            Expected behavior: Add correction record.
        """

        record = ChangeRecord(
            stage=stage,
            block_index=block_index,
            segment_index=segment_index,
            line=line,
            before=before,
            after=after,
            confidence=confidence,
            reason=reason,
            timestamp=datetime.utcnow().isoformat(),
        )

        self.records.append(record)

    # ---------------------------------------------------------

    def total_changes(
        self,
    ):
        """Number of changes.

        Example:
            ``result = instance.total_changes()``
            Expected behavior: Number of changes.
        """

        return len(self.records)

    # ---------------------------------------------------------

    def high_confidence(
        self,
        threshold=90.0,
    ):
        """Return safe automatic corrections.

        Example:
            ``result = instance.high_confidence()``
            Expected behavior: Return safe automatic corrections.
        """

        return [item for item in self.records if item.confidence >= threshold]

    # ---------------------------------------------------------

    def needs_review(
        self,
        threshold=85.0,
    ):
        """Return uncertain changes.

        Example:
            ``result = instance.needs_review()``
            Expected behavior: Return uncertain changes.
        """

        return [item for item in self.records if item.confidence < threshold]

    # ---------------------------------------------------------

    def export_json(
        self,
        path,
    ):
        """Save complete change log.

        Example:
            ``result = instance.export_json(Path("output.json"))``
            Expected behavior: Save complete change log.
        """

        path = Path(path)

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        data = [asdict(record) for record in self.records]

        with path.open(
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                data,
                file,
                indent=2,
                ensure_ascii=False,
            )
