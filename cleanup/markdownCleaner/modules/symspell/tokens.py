"""Shared token patterns for dictionary-backed text processing."""

from __future__ import annotations

import re


# The final alternative recognizes the common mojibake representation of a
# curly apostrophe so already-corrupted source text can still be tokenized and
# repaired. New text should use either the ASCII or Unicode apostrophe.
APOSTROPHE_PATTERN = r"(?:['\u2019]|\u00e2\u20ac\u2122)"
WORD_PATTERN = re.compile(
    rf"[A-Za-z]+(?:{APOSTROPHE_PATTERN}[A-Za-z]+|-[A-Za-z]+)*"
)
TERM_PATTERN = re.compile(
    rf"{WORD_PATTERN.pattern}(?:[ \t]+{WORD_PATTERN.pattern})*"
)


__all__ = [
    "APOSTROPHE_PATTERN",
    "TERM_PATTERN",
    "WORD_PATTERN",
]
