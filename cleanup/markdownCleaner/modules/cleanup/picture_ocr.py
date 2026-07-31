"""Readability-aware handling of converter picture-OCR blocks."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from .markup import (
    HTML_BREAK,
    HTML_COMMENT,
    PICTURE_BLOCK,
    UNDERLINE_TAG,
)


def clean_picture_block(block: str) -> str:
    """Remove picture-OCR wrappers and normalize their extracted text."""
    inner = re.sub(
        r"^<!--\s*Start of picture text\s*-->", "", block, flags=re.I
    ).strip()
    inner = re.sub(
        r"<!--\s*End of picture text\s*-->$", "", inner, flags=re.I
    ).strip()
    inner = HTML_BREAK.sub("\n", inner)
    inner = HTML_COMMENT.sub("", inner)
    inner = UNDERLINE_TAG.sub("", inner)
    inner = re.sub(r"[ \t]+", " ", inner)
    return re.sub(r"\n{3,}", "\n\n", inner).strip()


def picture_text_is_readable(text: str) -> bool:
    """Return whether image OCR contains enough language-like content to keep."""
    if not text.strip():
        return False
    chars = [char for char in text if not char.isspace()]
    if not chars:
        return False

    alpha = sum(char.isalpha() for char in chars)
    alnum = sum(char.isalnum() for char in chars)
    alpha_ratio = alpha / len(chars)
    alnum_ratio = alnum / len(chars)
    words = re.findall(r"[A-Za-z][A-Za-z'’\-]{1,}", text)
    meaningful = [word for word in words if len(word) >= 3]
    nonempty_lines = [line.strip() for line in text.splitlines() if line.strip()]

    if len(nonempty_lines) >= 8:
        language_like_lines = 0
        noisy_lines = 0
        for line in nonempty_lines:
            line_words = re.findall(r"[A-Za-z][A-Za-z'’\-]{1,}", line)
            line_chars = [char for char in line if not char.isspace()]
            line_alpha = sum(char.isalpha() for char in line_chars)
            line_alpha_ratio = (
                line_alpha / len(line_chars) if line_chars else 0.0
            )
            if len(line_words) >= 3 and line_alpha_ratio >= 0.55:
                language_like_lines += 1
            if len(line_words) <= 1 or line_alpha_ratio < 0.35:
                noisy_lines += 1

        if noisy_lines / len(nonempty_lines) >= 0.50:
            return False
        if language_like_lines / len(nonempty_lines) < 0.25:
            return False

    if len(meaningful) >= 3 and alpha_ratio >= 0.45:
        return True
    return len(meaningful) >= 6 and alnum_ratio >= 0.55


def filter_picture_ocr(
    text: str,
    *,
    mode: str,
    excluded_sections: Iterable[str] | None,
    normalize_headings: Callable[[str], str],
    section_key: Callable[[object], str],
) -> tuple[str, int, int]:
    """Filter picture OCR and return text plus removed/preserved counters.

    Heading and section-name policies are injected by the document stage,
    keeping this module independent of its orchestration class.
    """
    mode = mode.lower()
    if mode not in {"keep", "remove", "safe"}:
        mode = "safe"

    excluded_keys = {
        section_key(name)
        for name in (excluded_sections or ())
        if str(name).strip()
    }

    removed = 0
    preserved = 0
    pieces: list[str] = []
    cursor = 0
    for match in PICTURE_BLOCK.finditer(text):
        pieces.append(text[cursor : match.start()])
        cleaned = clean_picture_block(match.group(0))
        if cleaned:
            cleaned = normalize_headings(cleaned)

        marker = None
        if cleaned and excluded_keys:
            cleaned_lines = [
                line.strip() for line in cleaned.splitlines() if line.strip()
            ]
            for line in cleaned_lines:
                if section_key(line) in excluded_keys:
                    marker = line
                    break
            if marker is None and len(cleaned_lines) >= 2:
                combined = section_key(" ".join(cleaned_lines[:2]))
                if combined in excluded_keys:
                    marker = " ".join(cleaned_lines[:2])

        if marker:
            preserved += 1
            pieces.append("\n\n# " + marker + "\n\n")
        else:
            keep = mode == "keep" or (
                mode == "safe" and picture_text_is_readable(cleaned)
            )
            if keep and cleaned:
                preserved += 1
                pieces.append("\n\n" + cleaned + "\n\n")
            else:
                removed += 1
        cursor = match.end()

    pieces.append(text[cursor:])
    return "".join(pieces), removed, preserved
