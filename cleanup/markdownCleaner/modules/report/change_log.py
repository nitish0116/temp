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
    """
    One text correction event.
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
    """
    Stores all pipeline corrections.
    """

    def __init__(self):

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
        """
        Add correction record.
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
        """
        Number of changes.
        """

        return len(self.records)

    # ---------------------------------------------------------

    def high_confidence(
        self,
        threshold=90.0,
    ):
        """
        Return safe automatic corrections.
        """

        return [item for item in self.records if item.confidence >= threshold]

    # ---------------------------------------------------------

    def needs_review(
        self,
        threshold=85.0,
    ):
        """
        Return uncertain changes.
        """

        return [item for item in self.records if item.confidence < threshold]

    # ---------------------------------------------------------

    def export_json(
        self,
        path,
    ):
        """
        Save complete change log.
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
