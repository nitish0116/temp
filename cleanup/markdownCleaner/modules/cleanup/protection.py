"""Protect literal Markdown content during whole-document cleanup."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from ..markdown.markdown import BlockType, MarkdownBlock, MarkdownParser
from ..markdown.segmenter import split_protected_spans


_PROTECTED_BLOCK_TYPES = frozenset(
    {
        BlockType.CODE_FENCE,
        BlockType.YAML_FRONTMATTER,
        BlockType.TABLE,
        BlockType.IMAGE,
        BlockType.LINK,
        BlockType.BLOCKQUOTE,
        BlockType.LIST,
    }
)


def _supported_cleanup_html(text: str) -> bool:
    """Return whether a protected HTML span is intentionally cleanup markup."""
    folded = text.casefold().lstrip()
    return (
        folded.startswith("<!--")
        or folded.startswith("<br")
        or folded.startswith("<u")
        or folded.startswith("</u")
    )


@dataclass(slots=True)
class ProtectedMarkdown:
    """Hold placeholder text and exact content needed to restore it."""

    text: str
    replacements: dict[str, str]
    block_placeholders: frozenset[str]

    def restore(self, text: str) -> str:
        """Restore each placeholder still present after structural removals."""
        for placeholder, original in self.replacements.items():
            if placeholder not in self.block_placeholders:
                text = text.replace(placeholder, original)
                continue

            pattern = re.compile(rf"[ \t]*{re.escape(placeholder)}[ \t]*")

            def restore_block(match: re.Match[str]) -> str:
                before = match.string[match.start() - 1 : match.start()]
                after = match.string[match.end() : match.end() + 1]
                leading = "\n" if before and before not in "\r\n" else ""
                trailing = "\n" if after and after not in "\r\n" else ""
                return leading + original + trailing

            text = pattern.sub(restore_block, text)
        return text


def protect_markdown(
    text: str,
    *,
    cleanup_block: Callable[[MarkdownBlock], bool] | None = None,
    protect_footnote_identifiers: bool = True,
) -> ProtectedMarkdown:
    """Replace literal blocks/spans with collision-free cleanup placeholders.

    Structural removal may intentionally delete an entire placeholder along
    with its excluded section. Every placeholder that survives is restored
    exactly. Supported converter comments, ``<br>``, and ``<u>`` markup remain
    editable because document cleanup handles those constructs explicitly.
    """
    prefix = "MDCLEANPROTECTED"
    while prefix in text:
        prefix += "X"

    document = MarkdownParser().parse(text)
    replacements: dict[str, str] = {}
    block_placeholders: set[str] = set()
    counter = 0

    def token(kind: str) -> str:
        nonlocal counter
        counter += 1
        return f"{prefix}{kind}{counter:06d}"

    for block in document.blocks:
        is_cleanup_target = bool(cleanup_block and cleanup_block(block))
        protect_complete_block = (
            (
                block.block_type in _PROTECTED_BLOCK_TYPES
                and not is_cleanup_target
            )
            or (
                block.block_type is BlockType.HTML
                and not _supported_cleanup_html(block.current_text)
                and not is_cleanup_target
            )
        )
        if protect_complete_block:
            # A non-heading sentence-like sentinel survives paragraph cleanup
            # but remains ordinary section content, so excluded sections and
            # metadata removal can delete it with their surrounding material.
            placeholder = token("BLOCK") + "."
            replacements[placeholder] = block.current_text
            block_placeholders.add(placeholder)
            block.current_text = placeholder
            continue

        parts: list[str] = []
        for span in split_protected_spans(
            block.current_text,
            protect_footnote_identifiers=protect_footnote_identifiers,
        ):
            if not span.protected or _supported_cleanup_html(span.text):
                parts.append(span.text)
                continue
            placeholder = token("SPAN")
            replacements[placeholder] = span.text
            parts.append(placeholder)
        block.current_text = "".join(parts)

    return ProtectedMarkdown(
        document.to_markdown(),
        replacements,
        frozenset(block_placeholders),
    )
