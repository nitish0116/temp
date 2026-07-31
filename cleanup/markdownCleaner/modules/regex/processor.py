"""Base contract for deterministic OCR regex processors."""

from __future__ import annotations

from ..core.config import require_bool
from ..core.processor import SegmentProcessor


class RegexProcessor(SegmentProcessor):
    """Add regex-correction configuration to the shared processor contract."""

    name = "Regex"

    def correction_enabled(
        self,
        key: str,
        default: bool = True,
    ) -> bool:
        """Return the enabled state of one regex correction.

        Both the documented ``{"enabled": true}`` form and a direct boolean
        are accepted so programmatic configurations remain concise.
        """

        value = self.config.get(
            f"regex.corrections.{key}",
            default,
        )
        if isinstance(value, dict):
            value = value.get("enabled", default)
        return require_bool(value, f"regex.corrections.{key}.enabled")
