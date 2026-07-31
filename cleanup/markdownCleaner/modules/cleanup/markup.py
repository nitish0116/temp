"""Shared recognition and normalization of converter HTML markup."""

from __future__ import annotations

import re


PICTURE_BLOCK = re.compile(
    r"<!--\s*Start of picture text\s*-->.*?<!--\s*End of picture text\s*-->",
    re.IGNORECASE | re.DOTALL,
)
"""Match one complete converter picture-text comment block."""

HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
"""Match one residual HTML comment."""

UNDERLINE_TAG = re.compile(r"</?u\s*>", re.IGNORECASE)
"""Match a simple opening or closing underline tag."""

HTML_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
"""Match supported HTML line-break variants."""


def normalize_inline_markup(text: str) -> str:
    """Normalize the small legacy HTML subset accepted by cleanup stages.

    This intentionally is not a general HTML parser: converter line breaks,
    underline wrappers, comments, and horizontal whitespace are the only
    supported constructs.
    """
    text = HTML_BREAK.sub("\n", text)
    text = UNDERLINE_TAG.sub("", text)
    text = HTML_COMMENT.sub("", text)
    return re.sub(r"[ \t]+", " ", text)
