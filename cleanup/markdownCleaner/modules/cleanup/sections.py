"""Bounded front-matter, section, footnote, and publisher-tail cleanup.

All removals in this module require local structural evidence.  No helper
assumes that content is disposable merely because it appears near an edge of
the document.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from .headings import ATX_HEADING, plain_heading_text
from .markup import UNDERLINE_TAG

HeadingResolver = Callable[[str], str | None]

FOOTNOTE_DEFINITION = re.compile(
    r"(?m)^\s*\[\^[^\]]+\]:.*(?:\n(?: {2,}|\t).*)*\n?"
)
"""Match a Markdown footnote definition and its continuation lines."""

FOOTNOTE_REFERENCE = re.compile(r"\[\^[^\]]+\]")
"""Match an inline Markdown footnote reference."""

GLOSSARY_FOOTNOTE = re.compile(
    r"^\s*>?\s*\d+\s+\*\*\S(?:.*?\S)?\*\*(?:\s+.*)?$"
)
"""Match a bounded numbered glossary note with a bold term."""

SIGNUP_OR_NEWSLETTER = re.compile(
    r"(?:\bsign\s*up\b.*\bnewsletter\b|"
    r"\bnewsletter\s+sign\s*up\b|"
    r"(?:https?://|www\.)?(?:www\.)?yenpress\.com(?:/\S*)?|"
    r"\byen\s+(?:press\s+)?newsletter\b)",
    re.I,
)
"""Recognize generic publisher signup and newsletter promotion text."""

TRAILING_TOC_ITEM = re.compile(
    r"^\s*\d+[.)]\s+(?:cover|insert|title\s+page|copyright|"
    r"chapter\b.*|afterword|appendix\b.*|yen\s+newsletter)\s*$",
    re.I,
)
"""Match a numbered contents item allowed in a trailing TOC appendix."""

LOCAL_METADATA_HEADINGS = {
    "copyright",
    "contents",
    "table of contents",
    "title page",
    "publication information",
    "publishing information",
    "library of congress cataloging-in-publication data",
}
"""Normalized labels whose bounded metadata bodies may be removed."""

METADATA_LINE_PATTERNS = [
    re.compile(r"^isbn(?:s)?\s*[: ]", re.I),
    re.compile(r"^lccn\s*[: ]", re.I),
    re.compile(r"^first\s+(?:ebook|yen on|edition|published)\b", re.I),
    re.compile(r"^all rights reserved\.?$", re.I),
    re.compile(r"^©\s*\d{4}\b", re.I),
    re.compile(
        r"^(?:visit us at\s+)?(?:https?://)?(?:www\.)?\S+\.com\S*$",
        re.I,
    ),
]
"""Patterns for standalone publication metadata safe to remove locally."""

FRONT_MATTER_SIGNALS = [
    re.compile(r"\bcopyright\b", re.I),
    re.compile(r"\btable of contents\b|\bcontents\b", re.I),
    re.compile(r"\bbegin reading\b", re.I),
    re.compile(r"\byen (?:press|on)\b", re.I),
    re.compile(r"\bj-novel club\b", re.I),
    re.compile(r"\billustration by\b|\bcover art by\b", re.I),
    re.compile(r"\btranslation by\b", re.I),
    re.compile(r"\bisbn\b|\blccn\b|cataloging-in-publication", re.I),
    re.compile(r"\ball rights reserved\b", re.I),
    re.compile(r"\bscanning, uploading\b|\bdistribution of this book\b", re.I),
    re.compile(r"\bpublisher\b", re.I),
]
"""Publication indicators used to classify a bounded leading prefix."""

NARRATIVE_SECTION = re.compile(
    r"^(?:prologue|prelude|introduction|interlude|epilogue|"
    r"(?:chapter|story|part|book|act|section)\s+(?:\d+|[ivxlcdm]+)\b)",
    re.I,
)
"""Recognize the beginning of a known narrative section label."""


def section_key(value: object) -> str:
    """Return one canonical key for configured and detected section labels."""

    label = UNDERLINE_TAG.sub("", str(value)).strip()
    label = re.sub(r"^#{1,6}\s*", "", label)
    label = re.sub(r"[*_`]+", "", label)
    label = re.sub(r"[^a-z0-9]+", " ", label.casefold()).strip()
    label = re.sub(r"\bprofles\b", "profiles", label)
    label = re.sub(r"\bprofle\b", "profile", label)
    label = re.sub(r"^[a-z0-9 ]+\s+(character profiles?)$", r"\1", label)
    return re.sub(r"\s+", " ", label)


def _normalized_label(line: str) -> str:
    """Normalize Markdown and inline markup from a metadata label."""

    value = UNDERLINE_TAG.sub("", line).strip()
    value = re.sub(r"^#{1,6}\s*", "", value)
    value = re.sub(r"^[*_`]+|[*_`]+$", "", value).strip()
    return re.sub(r"\s+", " ", value).casefold().strip(" :")


def remove_leading_front_matter(
    text: str,
    *,
    heading_resolver: HeadingResolver = plain_heading_text,
) -> str:
    """Remove a leading publication prefix only when evidence is strong."""

    lines = text.splitlines()
    first_narrative: int | None = None

    # An explicit converter marker is stronger than chapter names in a TOC.
    for index, line in enumerate(lines):
        if re.match(r"^\s*\[chapter\]\s*\S+", line, flags=re.I):
            first_narrative = index
            break

    narrative_iter = enumerate(lines) if first_narrative is None else ()
    for index, line in narrative_iter:
        heading = heading_resolver(line)
        candidate = heading or UNDERLINE_TAG.sub("", line).strip()
        candidate = re.sub(r"^[*_`]+|[*_`]+$", "", candidate).strip()
        candidate = re.sub(r"^#{1,6}\s*", "", candidate).strip()
        if not NARRATIVE_SECTION.match(candidate):
            continue

        section_mentions = re.findall(
            r"\b(?:chapter|story|part|book|act|section)\s+"
            r"(?:\d+|[ivxlcdm]+)\b",
            candidate,
            flags=re.I,
        )
        is_atx = bool(ATX_HEADING.match(line.strip()))
        has_title_delimiter = bool(
            re.match(
                r"^(?:chapter|story|part|book|act|section)\s+"
                r"(?:\d+|[ivxlcdm]+)\s*[|:\-–—]\s*\S",
                candidate,
                flags=re.I,
            )
        )
        is_named = bool(
            re.match(
                r"^(?:prologue|prelude|introduction|interlude|epilogue)\s*$",
                candidate,
                flags=re.I,
            )
        )
        if (
            len(section_mentions) > 1
            or len(candidate) > 180
            or (not is_named and not is_atx and not has_title_delimiter)
        ):
            continue
        first_narrative = index
        break

    if first_narrative is None or first_narrative == 0:
        return text

    prefix = "\n".join(lines[:first_narrative])
    signal_count = sum(
        bool(pattern.search(prefix)) for pattern in FRONT_MATTER_SIGNALS
    )
    if signal_count < 2:
        return text
    return "\n".join(lines[first_narrative:]).lstrip()


def remove_local_metadata(
    text: str,
    *,
    heading_resolver: HeadingResolver = plain_heading_text,
) -> str:
    """Remove clearly identified bounded metadata sections and lines."""

    lines = text.splitlines()
    out: list[str] = []
    index = 0

    while index < len(lines):
        heading = heading_resolver(lines[index])
        normalized = (
            heading.casefold().strip(" :")
            if heading
            else _normalized_label(lines[index])
        )
        if normalized in LOCAL_METADATA_HEADINGS:
            index += 1
            while index < len(lines):
                next_heading = heading_resolver(lines[index])
                next_plain = _normalized_label(lines[index])
                structural = bool(
                    next_heading
                    and _normalized_label(next_heading)
                    not in LOCAL_METADATA_HEADINGS
                )
                if not structural and NARRATIVE_SECTION.match(next_plain):
                    structural = True
                if structural:
                    break
                index += 1
            continue

        stripped = UNDERLINE_TAG.sub("", lines[index]).strip()
        if any(pattern.search(stripped) for pattern in METADATA_LINE_PATTERNS):
            index += 1
            continue

        out.append(lines[index])
        index += 1
    return "\n".join(out)


def remove_named_sections(
    text: str,
    names: Iterable[str],
    *,
    heading_resolver: HeadingResolver = plain_heading_text,
) -> tuple[str, list[str]]:
    """Remove only explicitly named sections, preserving later sections."""

    targets = {section_key(name) for name in names if str(name).strip()}
    if not targets:
        return text, []

    lines = text.splitlines()
    out: list[str] = []
    removed: list[str] = []
    index = 0
    while index < len(lines):
        heading = heading_resolver(lines[index])
        if heading and section_key(heading) in targets:
            removed.append(heading)
            index += 1
            while index < len(lines):
                next_heading = heading_resolver(lines[index])
                if next_heading:
                    if section_key(next_heading) in targets:
                        index += 1
                        continue
                    break
                index += 1
            continue
        out.append(lines[index])
        index += 1
    return "\n".join(out), removed


def remove_promotional_tail(
    text: str,
    *,
    heading_resolver: HeadingResolver = plain_heading_text,
) -> str:
    """Remove a next-volume promotional tail with strong local evidence."""

    lines = text.splitlines()
    for index, line in enumerate(lines):
        heading = heading_resolver(line)
        candidate = heading or UNDERLINE_TAG.sub("", line).strip()
        candidate = re.sub(r"^#{1,6}\s*", "", candidate).strip()
        if not re.match(r"^volume\s+\d+\b", candidate, flags=re.I):
            continue
        window = "\n".join(lines[index : min(len(lines), index + 5)])
        if re.search(r"\bcoming\s+soon\b", window, flags=re.I):
            return "\n".join(lines[:index]).rstrip()

    # Some converters merge the volume title and Coming soon onto one line.
    for index, line in enumerate(lines):
        if re.search(r"\bvolume\s+\d+\b", line, flags=re.I) and re.search(
            r"\bcoming\s+soon\b",
            line,
            flags=re.I,
        ):
            return "\n".join(lines[:index]).rstrip()
    return text


def remove_bounded_glossary_footnotes(text: str) -> tuple[str, int]:
    """Remove paragraphs explicitly shaped like numbered glossary notes."""

    paragraphs = re.split(r"(\n\s*\n+)", text)
    removed = 0
    out: list[str] = []
    for part in paragraphs:
        if not part.strip() or re.fullmatch(r"\n\s*\n+", part):
            out.append(part)
            continue
        lines = [line for line in part.splitlines() if line.strip()]
        if lines and GLOSSARY_FOOTNOTE.match(lines[0]):
            removed += 1
            continue
        out.append(part)
    return "".join(out), removed


def remove_generic_publisher_tail(text: str) -> tuple[str, str | None]:
    """Remove strongly identified newsletter or numbered-TOC material at EOF."""

    lines = text.splitlines()
    if not lines:
        return text, None
    search_start = max(0, len(lines) - max(300, len(lines) // 5))

    for index in range(search_start, len(lines)):
        if SIGNUP_OR_NEWSLETTER.search(lines[index]):
            return "\n".join(lines[:index]).rstrip(), (
                "publisher signup/newsletter tail"
            )

    matches = [
        index
        for index in range(search_start, len(lines))
        if TRAILING_TOC_ITEM.match(lines[index])
    ]
    for first, second in zip(matches, matches[1:]):
        if second - first <= 4:
            return (
                "\n".join(lines[:first]).rstrip(),
                "numbered contents appendix",
            )
    return text, None
