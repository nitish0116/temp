"""Final text normalization and conservative OCR-noise detection."""

from __future__ import annotations

import re

DECORATIVE_SEPARATOR_LINE = re.compile(
    r"(?m)^[ \t]*(?:[*_]{1,3}[ \t]*)?(?:#{1,6}[ \t]+)?"
    r"(?:[◆◇■□●○♦♢✦✧❖◈※＊*•·~_=+\-][ \t]*){3,}"
    r"[.,;:!?]?[ \t]*(?:[*_]{1,3})?[ \t]*$"
)
"""Match a whole line made from at least three ornamental glyphs."""


def find_conservative_ocr_noise(
    text: str,
    *,
    limit: int = 100,
) -> list[tuple[int, str, str]]:
    """Find high-signal OCR garbage without changing document text."""

    findings: list[tuple[int, str, str]] = []
    if limit <= 0:
        return findings
    consonants = re.compile(r"[bcdfghjklmnpqrstvwxyz]{8,}", re.I)
    symbols = re.compile(r"[><~=|/@#^&\\]{4,}")
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or re.match(r"^#{1,6}\s", line):
            continue
        alpha = sum(char.isalpha() for char in line)
        reason = None
        if len(line) >= 10 and alpha / len(line) < 0.12 and symbols.search(line):
            reason = "very low alphabetic content with dense symbol noise"
        else:
            clusters = consonants.findall(line)
            if clusters and sum(map(len, clusters)) / len(line) > 0.50:
                reason = "line is dominated by an improbable consonant cluster"
        if reason:
            findings.append((number, line, reason))
            if len(findings) >= limit:
                break
    return findings


def strip_markdown_emphasis(text: str) -> str:
    """Remove emphasis delimiters while preserving internal underscores."""

    text = re.sub(r"(?<!\w)\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\w)", r"\1", text)
    text = re.sub(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)", r"\1", text)
    text = re.sub(r"(?<!\w)\*(?=\S)(.+?)(?<=\S)\*(?!\w)", r"\1", text)
    return re.sub(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)", r"\1", text)


def normalize_spacing(text: str) -> str:
    """Normalize horizontal whitespace, blank lines, and punctuation spacing."""

    lines = []
    for line in text.splitlines():
        if re.match(r"^\s*#{1,6}\s+", line):
            lines.append(re.sub(r"\s+$", "", line))
        else:
            lines.append(re.sub(r"[ \t]+", " ", line).strip())
    normalized = "\n".join(lines)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    normalized = re.sub(r"[ \t]+([,.;:!?])", r"\1", normalized)
    return normalized.strip() + "\n"
