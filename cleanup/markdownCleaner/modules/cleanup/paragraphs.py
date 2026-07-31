"""Paragraph reconstruction helpers for OCR/PDF extracted Markdown."""

from __future__ import annotations

import re
from collections.abc import Callable

SECTION_NUMBER = re.compile(r"^_?(\d+)_?$")
"""Match a bare section number with optional emphasis."""


def is_heading(block: str) -> bool:
    """Return whether an entire block is an ATX Markdown heading."""

    return bool(re.match(r"^\s*#{1,6}\s+", block))


def is_list_or_special(block: str) -> bool:
    """Return whether a block contains structure that must retain line breaks."""

    stripped = block.lstrip()
    return bool(
        stripped.startswith(("```", "~~~", ">", "|"))
        or re.match(r"^(?:[-*+] |\d+[.)] )", stripped)
    )


def looks_structured_lines(lines: list[str]) -> bool:
    """Identify records, cards, and tables whose line boundaries matter."""

    if len(lines) < 2:
        return False
    short = sum(len(line) <= 48 for line in lines)
    labelish = sum(
        bool(re.match(r"^[A-Z][A-Za-z0-9 /&()'’+\-]{0,40}:?$", line))
        for line in lines
    )
    colon_labels = sum(":" in line[:30] for line in lines)
    return (short / len(lines) >= 0.75 and labelish >= 2) or colon_labels >= 2


def should_join(
    left: str,
    right: str,
    *,
    heading_predicate: Callable[[str], bool] = is_heading,
    special_predicate: Callable[[str], bool] = is_list_or_special,
) -> bool:
    """Decide whether adjacent OCR blocks are fragments of one paragraph."""

    if (
        not left
        or not right
        or heading_predicate(left)
        or heading_predicate(right)
    ):
        return False
    if special_predicate(left) or special_predicate(right):
        return False
    if SECTION_NUMBER.match(left.strip()) or SECTION_NUMBER.match(right.strip()):
        return False
    left_value = left.rstrip()
    right_value = right.lstrip()
    if not right_value:
        return False
    if left_value.endswith("-") and re.match(r"^[a-z]", right_value):
        return True
    if left_value.endswith("—") and right_value.startswith("—"):
        return True
    return bool(
        len(left_value) <= 320
        and len(right_value) <= 1200
        and len(left_value) + len(right_value) <= 1400
        and not re.search(r"[.!?…\"'’”)]$", left_value)
        and re.match(r"^[a-z]", right_value)
    )


def split_overlong_paragraph(
    text: str,
    max_chars: int = 1800,
) -> list[str]:
    """Split exceptionally long prose at sentence or whitespace boundaries."""

    if len(text) <= max_chars or text.startswith("#"):
        return [text]

    sentences = re.split(r'(?<=[.!?…])\s+(?=["\'“‘(A-Z0-9])', text)
    if len(sentences) <= 1:
        chunks: list[str] = []
        remaining = text
        while len(remaining) > max_chars:
            cut = remaining.rfind(" ", 0, max_chars)
            if cut < max_chars // 2:
                cut = max_chars
            chunks.append(remaining[:cut].strip())
            remaining = remaining[cut:].strip()
        if remaining:
            chunks.append(remaining)
        return chunks

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        proposed = sentence if not current else current + " " + sentence
        if current and len(proposed) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = proposed
    if current:
        chunks.append(current)
    return chunks


def reconstruct_paragraphs(
    text: str,
    *,
    dehyphenate: bool = True,
    heading_predicate: Callable[[str], bool] = is_heading,
    special_predicate: Callable[[str], bool] = is_list_or_special,
    structured_predicate: Callable[[list[str]], bool] = looks_structured_lines,
    join_predicate: Callable[[str, str], bool] | None = None,
    paragraph_splitter: Callable[[str], list[str]] = split_overlong_paragraph,
) -> str:
    """Join OCR fragments while preserving headings and structured blocks."""

    raw_blocks = re.split(r"\n\s*\n+", text.strip())
    blocks: list[str] = []
    for block in raw_blocks:
        stripped = block.strip()
        if not stripped:
            continue
        if heading_predicate(stripped) or special_predicate(stripped):
            blocks.append(stripped)
            continue
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        if structured_predicate(lines):
            blocks.append("\n".join(lines))
            continue
        if len(lines) > 1:
            joined = lines[0]
            for line in lines[1:]:
                if (
                    dehyphenate
                    and joined.endswith("-")
                    and re.match(r"^[a-z]", line)
                ):
                    joined = joined[:-1] + line
                else:
                    joined += " " + line
            blocks.append(joined)
        else:
            blocks.append(stripped)

    if join_predicate is None:
        join_predicate = lambda left, right: should_join(
            left,
            right,
            heading_predicate=heading_predicate,
            special_predicate=special_predicate,
        )

    merged: list[str] = []
    for block in blocks:
        if merged and join_predicate(merged[-1], block):
            left = merged.pop().rstrip()
            if (
                dehyphenate
                and left.endswith("-")
                and re.match(r"^[a-z]", block)
            ):
                merged.append(left[:-1] + block.lstrip())
            elif left.endswith("—") and block.lstrip().startswith("—"):
                merged.append(left + block.lstrip()[1:])
            else:
                merged.append(left + " " + block.lstrip())
        else:
            merged.append(block)

    bounded: list[str] = []
    for block in merged:
        if heading_predicate(block) or special_predicate(block):
            bounded.append(block)
        else:
            bounded.extend(paragraph_splitter(block))
    return "\n\n".join(bounded)
