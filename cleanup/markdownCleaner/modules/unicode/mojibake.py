"""Conservative repair and reporting for mis-decoded UTF-8 text."""

from __future__ import annotations

import re

from ..markdown.segmenter import MarkdownSegment
from .processor import UnicodeProcessor


MOJIBAKE_MARKER = re.compile(r"(?:Ã.|Â.|â€.|â€™|ï¬.|ðŸ.)")
MOJIBAKE_TOKEN = re.compile(r"\S*[ÃÂâïð]\S*")


def mojibake_score(text: str) -> int:
    """Return a simple count of high-signal UTF-8-as-Windows-1252 markers."""

    return len(MOJIBAKE_MARKER.findall(text)) + text.count("�") * 2


def repair_mojibake(text: str, maximum_passes: int = 2) -> str:
    """Undo Windows-1252 decoding only when the marker score improves."""

    current = text
    for _ in range(maximum_passes):
        try:
            candidate = current.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            def repair_token(match: re.Match[str]) -> str:
                token = match.group(0)
                try:
                    repaired = token.encode("cp1252").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    return token
                return (
                    repaired
                    if mojibake_score(repaired) < mojibake_score(token)
                    else token
                )

            candidate = MOJIBAKE_TOKEN.sub(repair_token, current)
        if mojibake_score(candidate) >= mojibake_score(current):
            break
        current = candidate
    return current


class MojibakeProcessor(UnicodeProcessor):
    """Repair reversible mojibake and report irreversible replacement chars."""

    name = "Mojibake"

    def process(self, segment: MarkdownSegment) -> bool:
        before = segment.current_text
        if not before or not self.enabled("mojibake", True):
            return False
        after = repair_mojibake(before)
        changed = self.apply_change(
            segment=segment,
            before=before,
            after=after,
            reason="Repaired UTF-8 text decoded as Windows-1252",
            statistic="mojibake_repaired",
            confidence=99.0,
        )
        if "�" in after:
            self.tracker.add(
                stage=self.name,
                block_index=segment.block_index,
                segment_index=segment.segment_index,
                line=segment.start_line,
                before=after,
                after=after,
                confidence=0.0,
                reason=(
                    "Report only; Unicode replacement character cannot be "
                    "recovered without the source bytes"
                ),
                applied=False,
            )
        return changed
