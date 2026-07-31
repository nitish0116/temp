"""
modules/report/change_log.py

Change tracking and JSON export.
"""

from __future__ import annotations

import json

from copy import deepcopy
from dataclasses import (
    dataclass,
    asdict,
)

from pathlib import Path

from datetime import UTC, datetime
from typing import Iterable


@dataclass
class ChangeRecord:
    """One text edit or report-only audit event.

    Example:
        ``instance = ChangeRecord("RegexOCR", 0, 0, 1, "teh", "the", 98.0, "Safe correction", "2026-01-01T00:00:00")``
        Expected behavior: One pipeline audit event.
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

    broken_word: str | None = None


class ChangeLog:
    """Store all pipeline edit and review records.

    Example:
        ``instance = ChangeLog()``
        Expected behavior: Store all pipeline audit records.
    """

    def __init__(self, records: Iterable[ChangeRecord] | None = None):
        """Initialize an empty ordered collection of change records.

        Example:
            ``instance = ChangeLog()``
            Expected behavior: Initialize an empty ordered collection of change records.
        """

        self.records: list[ChangeRecord] = list(records or ())

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
        broken_word=None,
    ):
        """Add an edit or report-only audit record.

        Example:
            ``instance.add(stage="RegexOCR", block_index=0, segment_index=0, line=1, before="teh", after="the", confidence=98.0, reason="Safe correction")``
            Expected behavior: Add an audit record.
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
            timestamp=datetime.now(UTC).isoformat(),
            broken_word=broken_word,
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
        """Return records at or above the confidence threshold.

        Example:
            ``result = instance.high_confidence()``
            Expected behavior: Return high-confidence records.
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

    def with_minimum_confidence(self, threshold: float) -> ChangeLog:
        """Return an independent log containing records at or above ``threshold``.

        The original log and its mutable record objects are not shared. Report
        export uses this view when ``include_low_confidence`` is disabled.
        """
        return ChangeLog(deepcopy(self.high_confidence(threshold)))

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
