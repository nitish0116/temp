"""Compatibility processor base for segment-oriented SymSpell extensions."""

from __future__ import annotations

from ..core.processor import SegmentProcessor


class SymSpellProcessor(SegmentProcessor):
    """Retain the historical SymSpell processor API on the shared base."""

    name = "SymSpell"
