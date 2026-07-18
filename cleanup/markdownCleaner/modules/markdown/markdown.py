"""
markdown.py

Part 2A.1
Core document model and parser skeleton.

This module is responsible for representing a Markdown document as a
collection of typed blocks. Later stages will populate the parser with
code fence detection, table extraction, HTML preservation, etc.

Nothing in this file modifies text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import List, Optional

HEADING_ATX = re.compile(r"^\s{0,3}#{1,6}\s+.+$")
HEADING_SETEXT = re.compile(r"^[=-]{3,}\s*$")

HORIZONTAL_RULE = re.compile(r"^\s{0,3}((\*\s*){3,}|(-\s*){3,}|(_\s*){3,})\s*$")

BLOCKQUOTE = re.compile(r"^\s{0,3}>\s?.*$")

LIST_ITEM = re.compile(r"^\s{0,3}([*+-]|\d+[.)])\s+.+$")

TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")

TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")

FOOTNOTE = re.compile(r"^\[\^[^\]]+\]:\s+.*$")

IMAGE_ONLY = re.compile(r"^\s*!\[[^\]]*\]\([^)]+\)\s*$")

LINK_ONLY = re.compile(r"^\s*\[[^\]]+\]\([^)]+\)\s*$")

REFERENCE_LINK = re.compile(r"^\s*\[[^\]]+\]:\s+\S+.*$")


# ==========================================================
# Block Types
# ==========================================================


class BlockType(Enum):
    """Logical Markdown block types."""

    UNKNOWN = auto()

    BLANK = auto()

    PARAGRAPH = auto()

    HEADING = auto()

    CODE_FENCE = auto()

    INLINE_CODE = auto()

    TABLE = auto()

    HTML = auto()

    IMAGE = auto()

    LINK = auto()

    FOOTNOTE = auto()

    BLOCKQUOTE = auto()

    LIST = auto()

    HORIZONTAL_RULE = auto()

    YAML_FRONTMATTER = auto()


# ==========================================================
# Markdown Block
# ==========================================================


@dataclass(slots=True)
class MarkdownBlock:
    """
    Represents one logical block inside the document.
    """

    block_type: BlockType

    text: str

    start_line: int

    end_line: int

    editable: bool = True

    metadata: dict = field(default_factory=dict)

    def line_count(self) -> int:
        return self.end_line - self.start_line + 1

    def copy(self) -> "MarkdownBlock":
        return MarkdownBlock(
            block_type=self.block_type,
            text=self.text,
            start_line=self.start_line,
            end_line=self.end_line,
            editable=self.editable,
            metadata=dict(self.metadata),
        )

    def __repr__(self):

        preview = self.text.replace("\n", "\\n")

        if len(preview) > 60:
            preview = preview[:57] + "..."

        return (
            f"<MarkdownBlock "
            f"type={self.block_type.name} "
            f"lines={self.start_line}-{self.end_line} "
            f"editable={self.editable} "
            f"text='{preview}'>"
        )


# ==========================================================
# Markdown Document
# ==========================================================


@dataclass
class MarkdownDocument:
    """
    Container for every parsed block.
    """

    source: Optional[Path] = None

    blocks: List[MarkdownBlock] = field(default_factory=list)

    def add(self, block: MarkdownBlock):

        self.blocks.append(block)

    def __len__(self):

        return len(self.blocks)

    def __iter__(self):

        return iter(self.blocks)

    def editable_blocks(self):

        for block in self.blocks:

            if block.editable:
                yield block

    def protected_blocks(self):

        for block in self.blocks:

            if not block.editable:
                yield block

    def rebuild(self) -> str:
        """
        Reconstruct markdown exactly as stored.
        """

        return "\n".join(block.text for block in self.blocks)

    def statistics(self):

        stats = {}

        for block in self.blocks:

            name = block.block_type.name

            stats[name] = stats.get(name, 0) + 1

        return stats


# ==========================================================
# Parser Skeleton
# ==========================================================

import re

FENCE_PATTERN = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")
HTML_START = re.compile(r"^\s*<([A-Za-z][A-Za-z0-9]*)\b")
HTML_END = re.compile(r".*</([A-Za-z][A-Za-z0-9]*)>\s*$")


class MarkdownParser:

    def parse(self, markdown: str) -> MarkdownDocument:

        document = MarkdownDocument()
        lines = markdown.splitlines()

        i = 0

        total = len(lines)

        while i < total:

            line = lines[i]

            #
            # Blank line
            #
            if line.strip() == "":

                document.add(
                    MarkdownBlock(
                        block_type=BlockType.BLANK,
                        text="",
                        start_line=i + 1,
                        end_line=i + 1,
                        editable=False,
                    )
                )

                i += 1
                continue

            #
            # YAML front matter
            #
            if i == 0 and line.strip() == "---":

                start = i

                i += 1

                while i < total:

                    if lines[i].strip() == "---":

                        break

                    i += 1

                end = min(i, total - 1)

                document.add(
                    MarkdownBlock(
                        block_type=BlockType.YAML_FRONTMATTER,
                        text="\n".join(lines[start : end + 1]),
                        start_line=start + 1,
                        end_line=end + 1,
                        editable=False,
                    )
                )

                i = end + 1
                continue

            #
            # Fenced code block
            #
            match = FENCE_PATTERN.match(line)

            if match:

                marker = match.group(2)

                start = i

                i += 1

                while i < total:

                    current = lines[i]

                    if current.strip().startswith(marker):

                        break

                    i += 1

                end = min(i, total - 1)

                document.add(
                    MarkdownBlock(
                        block_type=BlockType.CODE_FENCE,
                        text="\n".join(lines[start : end + 1]),
                        start_line=start + 1,
                        end_line=end + 1,
                        editable=False,
                    )
                )

                i = end + 1

                continue

            #
            # Indented code block
            #
            if line.startswith("    ") or line.startswith("\t"):

                start = i

                i += 1

                while i < total:

                    current = lines[i]

                    if current.startswith("    ") or current.startswith("\t"):

                        i += 1

                        continue

                    break

                end = i - 1

                document.add(
                    MarkdownBlock(
                        block_type=BlockType.CODE_FENCE,
                        text="\n".join(lines[start : end + 1]),
                        start_line=start + 1,
                        end_line=end + 1,
                        editable=False,
                    )
                )

                continue

            #
            # HTML block
            #
            if HTML_START.match(line):

                start = i

                if HTML_END.match(line):

                    end = i

                else:

                    i += 1

                    while i < total:

                        if HTML_END.match(lines[i]):

                            break

                        i += 1

                    end = min(i, total - 1)

                document.add(
                    MarkdownBlock(
                        block_type=BlockType.HTML,
                        text="\n".join(lines[start : end + 1]),
                        start_line=start + 1,
                        end_line=end + 1,
                        editable=False,
                    )
                )

                i = end + 1

                continue

            #
            # Normal paragraph
            #
            # ======================================================
            # ATX heading
            # ======================================================
            if HEADING_ATX.match(line):

                document.add(
                    MarkdownBlock(
                        block_type=BlockType.HEADING,
                        text=line,
                        start_line=i + 1,
                        end_line=i + 1,
                        editable=False,
                    )
                )

                i += 1
                continue

            # ======================================================
            # Setext heading (previous line + underline)
            # ======================================================
            if i + 1 < total and lines[i].strip() != "" and HEADING_SETEXT.match(lines[i + 1]):

                document.add(
                    MarkdownBlock(
                        block_type=BlockType.HEADING,
                        text=lines[i] + "\n" + lines[i + 1],
                        start_line=i + 1,
                        end_line=i + 2,
                        editable=False,
                    )
                )

                i += 2
                continue

            # ======================================================
            # Horizontal rule
            # ======================================================
            if HORIZONTAL_RULE.match(line):

                document.add(
                    MarkdownBlock(
                        block_type=BlockType.HORIZONTAL_RULE,
                        text=line,
                        start_line=i + 1,
                        end_line=i + 1,
                        editable=False,
                    )
                )

                i += 1
                continue

            # ======================================================
            # Blockquote
            # ======================================================
            if BLOCKQUOTE.match(line):

                start = i
                quote = [line]

                i += 1

                while i < total and BLOCKQUOTE.match(lines[i]):

                    quote.append(lines[i])
                    i += 1

                document.add(
                    MarkdownBlock(
                        block_type=BlockType.BLOCKQUOTE,
                        text="\n".join(quote),
                        start_line=start + 1,
                        end_line=i,
                        editable=False,
                    )
                )

                continue

            # ======================================================
            # List block
            # ======================================================
            if LIST_ITEM.match(line):

                start = i
                items = [line]

                i += 1

                while i < total:

                    current = lines[i]

                    if LIST_ITEM.match(current):

                        items.append(current)
                        i += 1
                        continue

                    if current.startswith("    ") or current.startswith("\t"):

                        items.append(current)
                        i += 1
                        continue

                    break

                document.add(
                    MarkdownBlock(
                        block_type=BlockType.LIST,
                        text="\n".join(items),
                        start_line=start + 1,
                        end_line=i,
                        editable=False,
                    )
                )

                continue

            # ======================================================
            # Table block
            # ======================================================
            if i + 1 < total and TABLE_ROW.match(line) and TABLE_SEPARATOR.match(lines[i + 1]):

                start = i
                rows = [line, lines[i + 1]]

                i += 2

                while i < total and TABLE_ROW.match(lines[i]):

                    rows.append(lines[i])
                    i += 1

                document.add(
                    MarkdownBlock(
                        block_type=BlockType.TABLE,
                        text="\n".join(rows),
                        start_line=start + 1,
                        end_line=i,
                        editable=False,
                    )
                )

                continue

            # ======================================================
            # Footnote definition
            # ======================================================
            if FOOTNOTE.match(line):

                start = i
                foot = [line]

                i += 1

                while i < total and (lines[i].startswith("    ") or lines[i].startswith("\t")):

                    foot.append(lines[i])
                    i += 1

                document.add(
                    MarkdownBlock(
                        block_type=BlockType.FOOTNOTE,
                        text="\n".join(foot),
                        start_line=start + 1,
                        end_line=i,
                        editable=False,
                    )
                )

                continue

            # ======================================================
            # Reference link definition
            # ======================================================
            if REFERENCE_LINK.match(line):

                document.add(
                    MarkdownBlock(
                        block_type=BlockType.LINK,
                        text=line,
                        start_line=i + 1,
                        end_line=i + 1,
                        editable=False,
                    )
                )

                i += 1
                continue

            # ======================================================
            # Standalone image
            # ======================================================
            if IMAGE_ONLY.match(line):

                document.add(
                    MarkdownBlock(
                        block_type=BlockType.IMAGE,
                        text=line,
                        start_line=i + 1,
                        end_line=i + 1,
                        editable=False,
                    )
                )

                i += 1
                continue

            # ======================================================
            # Standalone link
            # ======================================================
            if LINK_ONLY.match(line):

                document.add(
                    MarkdownBlock(
                        block_type=BlockType.LINK,
                        text=line,
                        start_line=i + 1,
                        end_line=i + 1,
                        editable=False,
                    )
                )

                i += 1
                continue

            # ======================================================
            # Editable paragraph
            # ======================================================
            start = i
            paragraph = [line]

            i += 1

            while i < total:

                current = lines[i]

                if current.strip() == "":
                    break

                if HEADING_ATX.match(current):
                    break

                if i + 1 < total and lines[i].strip() != "" and HEADING_SETEXT.match(lines[i + 1]):
                    break

                if HORIZONTAL_RULE.match(current):
                    break

                if BLOCKQUOTE.match(current):
                    break

                if LIST_ITEM.match(current):
                    break

                if (
                    i + 1 < total
                    and TABLE_ROW.match(current)
                    and TABLE_SEPARATOR.match(lines[i + 1])
                ):
                    break

                if FOOTNOTE.match(current):
                    break

                if REFERENCE_LINK.match(current):
                    break

                if IMAGE_ONLY.match(current):
                    break

                if LINK_ONLY.match(current):
                    break

                if FENCE_PATTERN.match(current):
                    break

                if current.startswith("    ") or current.startswith("\t"):
                    break

                if HTML_START.match(current):
                    break

                paragraph.append(current)
                i += 1

                document.add(
                    MarkdownBlock(
                        block_type=BlockType.PARAGRAPH,
                        text="\n".join(paragraph),
                        start_line=start + 1,
                        end_line=i,
                        editable=True,
                    )
                )

        return document


# ==========================================================
# Convenience API
# ==========================================================


def load_markdown(filename: str | Path) -> MarkdownDocument:

    return MarkdownParser().parse_file(filename)


def parse_markdown(text: str) -> MarkdownDocument:

    return MarkdownParser().parse(text)
