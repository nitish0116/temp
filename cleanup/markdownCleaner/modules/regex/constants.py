"""
modules/regex/constants.py

OCR correction rules and regex patterns.

This module contains only:
    - replacement dictionaries
    - regex patterns
    - compiled regex objects
    - confidence values
"""

from __future__ import annotations

import re

# ============================================================
# OCR Character Confusion
# ============================================================

"""
Common OCR character substitutions.

These are intentionally limited.

Do NOT blindly replace:
    l -> 1
    O -> 0

because they can damage valid text.
"""


OCR_CHARACTER_REPLACEMENTS = {
    # Common OCR letter confusion
    "rn": "m",
    "vv": "w",
}


OCR_CHARACTER_CONFIDENCE = {
    "rn": 90.0,
    "vv": 85.0,
}


# ============================================================
# Number / Letter Confusion
# ============================================================

"""
Only apply inside alphabetic words.

Examples:

    l0ve -> love
    1ife -> life

"""

NUMBER_LETTER_REPLACEMENTS = {
    "0": "o",
    "1": "l",
    "5": "s",
    "8": "b",
}


NUMBER_LETTER_CONFIDENCE = {
    "0": 80.0,
    "1": 80.0,
    "5": 70.0,
    "8": 70.0,
}


# ============================================================
# Broken Word Detection
# ============================================================

"""
OCR often inserts spaces inside words.

Examples:

    some thing
    every thing
    to gether

"""

BROKEN_WORD_PATTERNS = [
    (
        re.compile(
            r"\b(to|some|every|any|no|what|where)\s+(thing|one|body)\b",
            re.IGNORECASE,
        ),
        r"\1\2",
        85.0,
    ),
]


# ============================================================
# Hyphenation
# ============================================================

"""
PDF line-break hyphenation.

Example:

    inter-
    national

becomes:

    international
"""


HYPHENATION_PATTERN = re.compile(
    r"(\w+)-\s*\n\s*(\w+)",
)


HYPHENATION_CONFIDENCE = 98.0


# ============================================================
# Repeated Characters
# ============================================================

"""
OCR duplication.

Examples:

    helllo -> hello

"""

REPEATED_CHARACTER_PATTERN = re.compile(r"\b([a-zA-Z])\1{2,}\b")


REPEATED_CHARACTER_CONFIDENCE = 75.0


# ============================================================
# OCR Character Regex Rules
# ============================================================

"""
Patterns where replacement depends on context.

Example:

    l0ve

should become:

    love

but:

    10

should not become:

    lo
"""


ZERO_IN_WORD_PATTERN = re.compile(r"(?<=[A-Za-z])0(?=[A-Za-z])")


ONE_IN_WORD_PATTERN = re.compile(r"(?<=[A-Za-z])1(?=[A-Za-z])")


FIVE_IN_WORD_PATTERN = re.compile(r"(?<=[A-Za-z])5(?=[A-Za-z])")


EIGHT_IN_WORD_PATTERN = re.compile(r"(?<=[A-Za-z])8(?=[A-Za-z])")


# ============================================================
# Statistics Keys
# ============================================================

REGEX_STAT_KEYS = {
    "ocr_character_fixed",
    "broken_words_fixed",
    "hyphenations_fixed",
    "repeated_characters_fixed",
    "number_letter_fixed",
}
