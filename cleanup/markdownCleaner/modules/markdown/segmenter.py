"""
modules/markdown/segmenter.py

Markdown text segmentation model.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class MarkdownSegment:
    """Represents a single markdown processing unit.

    Example:
        ``instance = MarkdownSegment("Example text.", 1)``
        Expected behavior: Represents a single markdown processing unit.
    """

    text: str

    line_number: int

    #
    # Location tracking
    #

    block_index: int = 0

    segment_index: int = 0

    #
    # Original location
    #

    start_line: int = 0

    end_line: int = 0

    #
    # Runtime modified content
    #

    current_text: str | None = None

    # Synthetic spans created inside a paragraph use ``False`` so whitespace
    # normalization does not mistake mid-line spaces for Markdown indentation.
    starts_at_line_boundary: bool = True

    def __post_init__(
        self,
    ):
        """Initialize current text and missing source-line boundaries.

        Example:
            ``result = instance.__post_init__()``
            Expected behavior: Initialize current text and missing source-line boundaries.
        """

        if self.current_text is None:

            self.current_text = self.text

        if self.start_line == 0:

            self.start_line = self.line_number

        if self.end_line == 0:

            self.end_line = self.line_number

    # ---------------------------------------------------------

    def update(
        self,
        value: str,
    ):
        """Update processed text.

        Example:
            ``instance.update("value")``
            Expected behavior: Update processed text.
        """

        original_has_newline = self.get_text().endswith("\n")

        if original_has_newline:
            value = value.rstrip("\n") + "\n"

        self.current_text = value

    # ---------------------------------------------------------

    def get_text(
        self,
    ):
        """Return current processed text.

        Example:
            ``result = instance.get_text()``
            Expected behavior: Return current processed text.
        """

        return self.current_text if self.current_text is not None else ""


# Backward-compatible name used by older processor modules.
# Keep this alias so mixed/stale project copies do not fail at import time.
TextSegment = MarkdownSegment


@dataclass(frozen=True, slots=True)
class MarkdownSpan:
    """One exact text span classified for safe processor traversal."""

    text: str
    protected: bool


_LINK_PREFIX = re.compile(r"!?\[[^\]\n]*\]\(")
_REFERENCE_DESTINATION = re.compile(
    r"!?\[(?P<label>[^\]\n]+)\]\[(?P<destination>[^\]\n]*)\]"
)
_SHORTCUT_REFERENCE = re.compile(
    r"\[(?P<identifier>\^?[^\]\n]+)\](?![\[(])"
)
_INLINE_HTML = re.compile(r"<!--.*?-->|</?[A-Za-z][^>\n]*>", re.DOTALL)
_AUTOLINK = re.compile(
    r"<(?:https?://[^ <>\n]+|mailto:[^ <>\n]+|[^ <>@\n]+@[^ <>@\n]+)>",
    re.IGNORECASE,
)


def _inline_code_ranges(text: str) -> list[tuple[int, int]]:
    """Return ranges enclosed by matching Markdown backtick runs."""

    ranges: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        start = text.find("`", index)
        if start < 0:
            break

        marker_end = start
        while marker_end < len(text) and text[marker_end] == "`":
            marker_end += 1
        marker_length = marker_end - start

        candidate = marker_end
        close_end = None
        while candidate < len(text):
            close = text.find("`", candidate)
            if close < 0:
                break
            run_end = close
            while run_end < len(text) and text[run_end] == "`":
                run_end += 1
            if run_end - close == marker_length:
                close_end = run_end
                break
            candidate = run_end

        if close_end is None:
            index = marker_end
            continue
        ranges.append((start, close_end))
        index = close_end

    return ranges


def _link_destination_ranges(text: str) -> list[tuple[int, int]]:
    """Return inline-link destinations, including balanced parentheses."""

    ranges: list[tuple[int, int]] = []
    for match in _LINK_PREFIX.finditer(text):
        start = match.end()
        index = start
        nested = 0
        while index < len(text) and text[index] != "\n":
            character = text[index]
            if character == "\\" and index + 1 < len(text):
                index += 2
                continue
            if character == "(":
                nested += 1
            elif character == ")":
                if nested == 0:
                    ranges.append((start, index))
                    break
                nested -= 1
            index += 1
        else:
            # Malformed Markdown should still keep its URL-like tail literal.
            ranges.append((start, index))
    return ranges


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []

    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    return merged


def protected_span_ranges(
    text: str,
    *,
    protect_footnote_identifiers: bool = True,
) -> tuple[tuple[int, int], ...]:
    """Return merged ranges occupied by protected inline Markdown."""

    ranges = _inline_code_ranges(text)
    ranges.extend(_link_destination_ranges(text))
    for match in _REFERENCE_DESTINATION.finditer(text):
        group = "destination" if match.group("destination") else "label"
        ranges.append(match.span(group))
    for match in _SHORTCUT_REFERENCE.finditer(text):
        identifier = match.group("identifier")
        if not protect_footnote_identifiers and identifier.startswith("^"):
            continue
        ranges.append(match.span("identifier"))
    ranges.extend(match.span() for match in _INLINE_HTML.finditer(text))
    ranges.extend(match.span() for match in _AUTOLINK.finditer(text))
    return tuple(_merge_ranges(ranges))


def split_protected_spans(
    text: str,
    *,
    protect_footnote_identifiers: bool = True,
) -> list[MarkdownSpan]:
    """Split paragraph text around inline Markdown that must remain literal.

    Backtick code, inline HTML, autolinks, link destinations, and reference
    identifiers are protected. The returned spans concatenate exactly to the
    input.
    """

    merged = protected_span_ranges(
        text,
        protect_footnote_identifiers=protect_footnote_identifiers,
    )
    if not merged:
        return [MarkdownSpan(text=text, protected=False)]

    spans: list[MarkdownSpan] = []
    cursor = 0
    for start, end in merged:
        if cursor < start:
            spans.append(MarkdownSpan(text[cursor:start], protected=False))
        spans.append(MarkdownSpan(text[start:end], protected=True))
        cursor = end
    if cursor < len(text):
        spans.append(MarkdownSpan(text[cursor:], protected=False))
    return spans


def process_editable_spans(
    segment: MarkdownSegment,
    process: Callable[[MarkdownSegment], None],
) -> None:
    """Apply ``process`` to synthetic editable spans and rebuild ``segment``.

    Synthetic spans retain the parent block/segment indexes and accurate line
    offsets, so processor audit records remain tied to the source paragraph.
    """

    spans = split_protected_spans(segment.get_text())
    if len(spans) == 1 and not spans[0].protected:
        process(segment)
        return

    rebuilt: list[str] = []
    line_offset = 0
    starts_at_line_boundary = True
    for span in spans:
        if span.protected or not span.text:
            rebuilt.append(span.text)
        else:
            start_line = segment.start_line + line_offset
            editable = MarkdownSegment(
                text=span.text,
                current_text=span.text,
                line_number=start_line,
                start_line=start_line,
                end_line=start_line + span.text.count("\n"),
                block_index=segment.block_index,
                segment_index=segment.segment_index,
                starts_at_line_boundary=starts_at_line_boundary,
            )
            process(editable)
            rebuilt.append(editable.get_text())
        line_offset += span.text.count("\n")
        if span.text:
            starts_at_line_boundary = span.text.endswith("\n")

    segment.current_text = "".join(rebuilt)
