"""Detect conservative repeated headers, footers, and page-number lines."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re

from ..core.stage import PipelineStage, StageResult


PAGE_NUMBER = re.compile(r"^\s*(?:page\s+)?(?:\d{1,4}|[ivxlcdm]{1,8})\s*$", re.I)
STRUCTURAL_PREFIX = re.compile(r"^\s*(?:#|>|[-*+]\s|\d+[.)]\s|```|~~~|\|)")


@dataclass(frozen=True, slots=True)
class PageArtifact:
    """One repeated normalized line and all one-based source locations."""

    text: str
    lines: tuple[int, ...]
    kind: str


def find_page_artifacts(
    text: str,
    *,
    minimum_occurrences: int = 3,
    minimum_line_gap: int = 12,
    maximum_length: int = 80,
) -> list[PageArtifact]:
    """Find high-signal page furniture without modifying the document."""

    occurrences: dict[str, list[tuple[int, str]]] = defaultdict(list)
    page_numbers: list[tuple[int, str]] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        value = " ".join(raw.split())
        if (
            not value
            or len(value) > maximum_length
            or STRUCTURAL_PREFIX.match(value)
        ):
            continue
        if PAGE_NUMBER.fullmatch(value):
            page_numbers.append((line_number, raw))
            continue
        if value.endswith(('.', '!', '?', '…', ':', ';')):
            continue
        key = value.casefold()
        occurrences[key].append((line_number, raw))

    findings: list[PageArtifact] = []
    for entries in occurrences.values():
        lines = tuple(item[0] for item in entries)
        representative = entries[0][1]
        word_count = len(re.findall(r"[A-Za-z]+", representative))
        if not 2 <= word_count <= 12:
            continue
        kind = "repeated header/footer"
        if len(lines) < minimum_occurrences:
            continue
        if any(
            right - left < minimum_line_gap
            for left, right in zip(lines, lines[1:])
        ):
            continue
        findings.append(PageArtifact(representative, lines, kind))
    page_lines = tuple(item[0] for item in page_numbers)
    if (
        len(page_lines) >= minimum_occurrences
        and all(
            right - left >= minimum_line_gap
            for left, right in zip(page_lines, page_lines[1:])
        )
    ):
        preview = ", ".join(item[1].strip() for item in page_numbers[:5])
        findings.append(
            PageArtifact(preview, page_lines, "standalone page numbers")
        )
    return sorted(findings, key=lambda item: item.lines[0])


class PageArtifactStage(PipelineStage):
    """Report or remove conservatively detected repeated page furniture."""

    name = "PageArtifacts"
    config_section = "page_artifacts"

    def process(self, context) -> StageResult:
        text = context.current_markdown or context.original_markdown
        findings = find_page_artifacts(
            text,
            minimum_occurrences=int(self.get_config("minimum_occurrences", 3)),
            minimum_line_gap=int(self.get_config("minimum_line_gap", 12)),
            maximum_length=int(self.get_config("maximum_length", 80)),
        )[: int(self.get_config("report_limit", 100))]
        mode = str(self.get_config("mode", "report_only")).casefold()
        if mode not in {"report_only", "remove"}:
            raise ValueError("page_artifacts.mode must be report_only or remove")

        if mode == "remove" and findings:
            target_lines = {line for item in findings for line in item.lines}
            source_lines = text.splitlines(keepends=True)
            text = "".join(
                value
                if index not in target_lines
                else ("\n" if value.endswith("\n") else "")
                for index, value in enumerate(source_lines, start=1)
            )
            context.replace_markdown(text)

        for finding in findings:
            context.tracker.add(
                stage=self.name,
                block_index=-1,
                segment_index=-1,
                line=finding.lines[0],
                before=finding.text,
                after="" if mode == "remove" else finding.text,
                confidence=95.0 if mode == "remove" else 0.0,
                reason=(
                    f"{'Removed' if mode == 'remove' else 'Report only; preserved'} "
                    f"{finding.kind} at lines {', '.join(map(str, finding.lines))}"
                ),
                applied=mode == "remove",
            )
        return StageResult(
            stage=self.name,
            changes=len(findings) if mode == "remove" else 0,
        )
