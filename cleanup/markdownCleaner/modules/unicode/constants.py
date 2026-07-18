"""
modules/unicode/constants.py

Unicode cleanup constants.

This module contains only:
    - Unicode character groups
    - Replacement mappings
    - Precomputed translation tables

No processing logic should be added here.
"""

from __future__ import annotations

# ============================================================
# Unicode Normalization Forms
# ============================================================

UNICODE_NORMALIZATION_FORMS = {
    "NFC",
    "NFKC",
    "NFD",
    "NFKD",
}


DEFAULT_NORMALIZATION_FORM = "NFKC"


# ============================================================
# Zero Width Characters
# ============================================================

ZERO_WIDTH_CHARACTERS = frozenset(
    {
        "\u200b",  # ZERO WIDTH SPACE
        "\u200c",  # ZERO WIDTH NON-JOINER
        "\u200d",  # ZERO WIDTH JOINER
        "\u2060",  # WORD JOINER
        "\ufeff",  # ZERO WIDTH NO-BREAK SPACE / BOM
    }
)


# ============================================================
# Soft Hyphen
# ============================================================

SOFT_HYPHEN = "\u00ad"


# ============================================================
# Unicode Spaces
# ============================================================

UNICODE_SPACES = {
    "\u00a0": " ",  # NO-BREAK SPACE
    "\u1680": " ",  # OGHAM SPACE MARK
    "\u2000": " ",  # EN QUAD
    "\u2001": " ",  # EM QUAD
    "\u2002": " ",  # EN SPACE
    "\u2003": " ",  # EM SPACE
    "\u2004": " ",  # THREE-PER-EM SPACE
    "\u2005": " ",  # FOUR-PER-EM SPACE
    "\u2006": " ",  # SIX-PER-EM SPACE
    "\u2007": " ",  # FIGURE SPACE
    "\u2008": " ",  # PUNCTUATION SPACE
    "\u2009": " ",  # THIN SPACE
    "\u200a": " ",  # HAIR SPACE
    "\u202f": " ",  # NARROW NO-BREAK SPACE
    "\u205f": " ",  # MEDIUM MATHEMATICAL SPACE
    "\u3000": " ",  # IDEOGRAPHIC SPACE
}


# ============================================================
# Ligatures
# ============================================================

LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "ft",
    "ﬆ": "st",
}


# ============================================================
# Quote Characters
# ============================================================

DOUBLE_QUOTES = {
    "“": '"',
    "”": '"',
    "„": '"',
    "‟": '"',
    "❝": '"',
    "❞": '"',
    "«": '"',
    "»": '"',
}


SINGLE_QUOTES = {
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "`": "'",
    "´": "'",
}


QUOTES = {
    **DOUBLE_QUOTES,
    **SINGLE_QUOTES,
}


# ============================================================
# Dash Characters
# ============================================================

DASHES = {
    "‐": "-",  # Hyphen
    "-": "-",  # Non-breaking hyphen
    "‒": "-",  # Figure dash
    "–": "-",  # En dash
    "—": "-",  # Em dash
    "―": "-",  # Horizontal bar
    "−": "-",  # Minus sign
}


# ============================================================
# Ellipsis
# ============================================================

ELLIPSIS = {
    "…": "...",
}


# ============================================================
# Control Characters
# ============================================================

ALLOWED_CONTROL_CHARACTERS = frozenset(
    {
        "\n",
        "\r",
        "\t",
    }
)


# ============================================================
# Translation Tables
# ============================================================

# These are created once and reused by processors.

LIGATURE_TRANSLATION = str.maketrans(LIGATURES)


SPACE_TRANSLATION = str.maketrans(UNICODE_SPACES)


QUOTE_TRANSLATION = str.maketrans(QUOTES)


DASH_TRANSLATION = str.maketrans(DASHES)


ELLIPSIS_TRANSLATION = str.maketrans(ELLIPSIS)


# ============================================================
# Combined Simple Character Translation
# ============================================================

PUNCTUATION_TRANSLATION = str.maketrans(
    {
        **QUOTES,
        **DASHES,
        **ELLIPSIS,
    }
)


# ============================================================
# Statistics Names
# ============================================================

UNICODE_STAT_KEYS = {
    "normalized",
    "zero_width_removed",
    "soft_hyphen_removed",
    "ligatures_fixed",
    "spaces_normalized",
    "quotes_normalized",
    "dashes_normalized",
    "ellipsis_normalized",
    "control_chars_removed",
}
