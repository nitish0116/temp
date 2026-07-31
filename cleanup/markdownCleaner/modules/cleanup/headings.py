"""Heading recognition and normalization helpers for document cleanup.

The functions in this module are pure: they accept Markdown text and return
normalized text or a classification.  Keeping them separate from the pipeline
stage makes the structural rules testable without constructing a context.
"""

from __future__ import annotations

import re

from ..markdown.markdown import BlockType, MarkdownParser
from .markup import UNDERLINE_TAG

ATX_HEADING = re.compile(r"^(\s*#{1,6})\s+(.+?)\s*$")
"""Capture the marker and title of an ATX heading."""

START_HEADING = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:<u>\s*)?(?:prologue|prelude|introduction|"
    r"(?:chapter|story|part|book|volume|act|section)\s+(?:\d+|[ivxlcdm]+)\s*[|:])",
    re.IGNORECASE,
)
"""Backward-compatible narrative-start pattern; it does not truncate text."""

BACK_MATTER_HEADING = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?[_*\s]*(?:<u>\s*)?[_*\s]*(?:"
    r"a(?:fter|fer)word|yen\s+news(?:letter|leter)|newsletter|newsleter"
    r")[_*\s]*(?:</u>)?[_*\s]*$"
)
"""Recognize a standalone Afterword or newsletter back-matter heading."""

NUMBERED_HEADING = re.compile(
    r"^(chapter|story|part|book|volume|act|section)\s+"
    r"([\divxlcdm]+)(?:\s*[|:\-–—]\s*|\s+)(.+)$",
    re.IGNORECASE,
)
"""Capture a numbered narrative heading and its title."""

BARE_NUMBERED_HEADING = re.compile(
    r"^(chapter|story|part|book|volume|act|section)\s+([\divxlcdm]+)\s*$",
    re.IGNORECASE,
)
"""Capture a numbered narrative heading without a subtitle."""

SETEXT_UNDERLINE = re.compile(r"^(=+|-+)\s*$")
"""Recognize the underline in a parsed Setext heading."""

NAMED_HEADING = re.compile(
    r"^(prologue|epilogue|prelude|introduction|interlude|appendix|"
    r"afterword|foreword|acknowledg(?:e)?ments?|character\s+profiles?|glossary|"
    r"bonus\s+short\s+stories|short\s+stories|side\s+stories|extras?|"
    r"notes|references|bibliography)(?:\s*[|:\-–—]\s*(.+))?$",
    re.IGNORECASE,
)
"""Match a known unnumbered section heading with an optional subtitle."""


def plain_heading_text(line: str) -> str | None:
    """Return normalized text when a line is structurally heading-like."""

    raw = line.strip()
    atx = ATX_HEADING.match(raw)
    body = atx.group(2).strip() if atx else raw
    body = UNDERLINE_TAG.sub("", body)
    body = re.sub(r"^[*_]+|[*_]+$", "", body).strip()

    # Image/OCR conversions often prefix profiles with a series title or use
    # the recurring Profle/Profles misspelling.
    if re.fullmatch(
        r"(?:[A-Z][A-Z0-9 _'’:\-]{2,}\s+)?character\s+prof(?:iles?|les?)",
        body,
        flags=re.I,
    ):
        return "Character Profiles"

    if atx or NAMED_HEADING.match(body) or NUMBERED_HEADING.match(body):
        return body
    return None


def normalize_setext_headings(text: str) -> str:
    """Convert parsed Setext headings to equivalent ATX headings."""

    document = MarkdownParser().parse(text)
    for block in document.blocks:
        if block.block_type is not BlockType.HEADING:
            continue
        lines = block.content.splitlines()
        if len(lines) != 2:
            continue
        underline = SETEXT_UNDERLINE.fullmatch(lines[1].strip())
        if underline is None:
            continue
        marker = "#" if underline.group(1).startswith("=") else "##"
        block.content = f"{marker} {lines[0].strip()}"
    return document.to_markdown()


def looks_like_false_heading(body: str) -> bool:
    """Detect only high-confidence converter-created prose headings."""

    value = body.strip()
    if not value:
        return True
    if NUMBERED_HEADING.match(value) or NAMED_HEADING.match(value):
        return False
    if value.startswith(("“", '"', "‘", "'", "—")):
        return True
    if value.endswith(("—", ",", ";", ":")):
        return True
    if re.match(r"^[a-z]", value):
        return True
    if re.fullmatch(
        r"[A-Z][A-Za-z'’\-]*(?:\s+[A-Z][A-Za-z'’\-]*)?[.!?…]",
        value,
    ):
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?(?:…|\.\.\.)?", value):
        return True
    return False


def normalize_headings(text: str) -> str:
    """Promote real headings, demote false ones, and standardize ATX levels."""

    out: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        atx = ATX_HEADING.match(stripped)
        body = atx.group(2).strip() if atx else stripped
        body = UNDERLINE_TAG.sub("", body).strip()
        body = re.sub(r"^\*\*(.*?)\*\*$", r"\1", body)
        body = re.sub(r"^__(.*?)__$", r"\1", body).strip()

        chapter_marker = re.match(
            r"^\[chapter\]\s*([\divxlcdm]+)(?:\s*[|:\-–—]?\s*)(.*)$",
            body,
            flags=re.I,
        )
        if chapter_marker:
            number, title = chapter_marker.groups()
            number = (
                number.upper()
                if re.fullmatch(r"[ivxlcdm]+", number, re.I)
                else number
            )
            title = title.strip()
            out.append(f"# Chapter {number}" + (f": {title}" if title else ""))
            continue

        bare_numbered = BARE_NUMBERED_HEADING.match(body)
        numbered = NUMBERED_HEADING.match(body)
        named = NAMED_HEADING.match(body)

        if bare_numbered:
            kind, number = bare_numbered.groups()
            kind = kind[:1].upper() + kind[1:].lower()
            number = (
                number.upper()
                if re.fullmatch(r"[ivxlcdm]+", number, re.I)
                else number
            )
            out.append(f"# {kind} {number}")
            continue

        if numbered:
            kind, number, title = numbered.groups()
            kind = kind[:1].upper() + kind[1:].lower()
            number = (
                number.upper()
                if re.fullmatch(r"[ivxlcdm]+", number, re.I)
                else number
            )
            out.append(f"# {kind} {number}: {title.strip()}")
            continue

        if named:
            kind, title = named.groups()
            canonical = kind[:1].upper() + kind[1:]
            if re.fullmatch(r"character\s+profiles?", kind, re.I):
                canonical = "Character Profiles"
            if title:
                canonical += f": {title.strip()}"
            out.append(f"# {canonical}")
            continue

        if atx:
            if looks_like_false_heading(body):
                out.append(body)
            else:
                level = len(atx.group(1).strip())
                out.append(f"{'#' * level} {body}")
        else:
            out.append(raw.rstrip())

    # Collapse adjacent exact duplicate headings only.
    deduped: list[str] = []
    for line in out:
        if line.startswith("#"):
            previous_nonblank = next(
                (item for item in reversed(deduped) if item.strip()),
                None,
            )
            if (
                previous_nonblank
                and previous_nonblank.startswith("#")
                and line.casefold() == previous_nonblank.casefold()
            ):
                continue
        deduped.append(line)
    return "\n".join(deduped)
