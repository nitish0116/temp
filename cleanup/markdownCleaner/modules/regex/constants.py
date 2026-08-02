"""
modules/regex/constants.py

OCR correction rules and regex patterns.

This module contains declarative rule definitions, replacement dictionaries,
compiled patterns, and confidence values. Mutation and audit logging remain in
the processors that consume these definitions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

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
    # Intentionally empty by default.  Global replacements such as rn -> m
    # corrupt valid words (internal -> intemal, turned -> tumed).
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
    "0": 85.0,
    "1": 80.0,
    "5": 75.0,
    "8": 75.0,
}

NUMBER_LETTER_CONFIG_KEYS = {
    "0": "zero_to_o",
    "1": "one_to_l",
    "5": "five_to_s",
    "8": "eight_to_b",
}


# ============================================================
# Broken Word Detection
# ============================================================

"""
OCR often inserts spaces inside words.

Examples:

    some thing
    every thing
    any body

"""


class BoundaryCorrection(str, Enum):
    """Direction of a deterministic word-boundary correction."""

    JOIN = "join"
    SPLIT = "split"


class BoundaryEvidence(str, Enum):
    """The OCR extraction defect evidenced by a boundary rule."""

    INSERTED_BOUNDARY = "ocr_inserted_boundary"
    MISSING_BOUNDARY = "ocr_missing_boundary"


@dataclass(frozen=True)
class BrokenWordRule:
    """One explicit, auditable word-boundary correction rule."""

    name: str
    pattern: re.Pattern[str]
    correction: BoundaryCorrection
    evidence: BoundaryEvidence
    confidence: float

    def replacement_for(self, match: re.Match[str]) -> str:
        """Build a replacement while retaining the matched token's casing."""

        separator = "" if self.correction is BoundaryCorrection.JOIN else " "
        return match.group("left") + separator + match.group("right")


def _split_rule(left: str, right: str) -> BrokenWordRule:
    """Create an exact missing-boundary repair rule."""

    return BrokenWordRule(
        name=f"split_{left}_{right}",
        pattern=re.compile(
            rf"\b(?P<left>{left})(?P<right>{right})\b",
            re.IGNORECASE,
        ),
        correction=BoundaryCorrection.SPLIT,
        evidence=BoundaryEvidence.MISSING_BOUNDARY,
        confidence=99.0,
    )


# Joined-word decisions live in data/broken_word_decisions.json, where they can
# be reviewed without adding code conditions. This deterministic stage retains
# only missing-boundary repairs whose intended direction is unambiguous.
BROKEN_WORD_RULES: tuple[BrokenWordRule, ...] = (
    _split_rule("to", "one"),
    _split_rule("no", "one"),
)

# Backward-compatible import name.  Its entries are now typed rules rather
# than positional tuples.
BROKEN_WORD_PATTERNS = BROKEN_WORD_RULES


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
    r"(\w+)-[ \t]*\r?\n[ \t]*(\w+)",
)


HYPHENATION_CONFIDENCE = 98.0


# ============================================================
# Repeated Characters
# ============================================================

"""
Standalone OCR noise made from one character repeated three or more times.

Examples:

    aaaa -> a

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
