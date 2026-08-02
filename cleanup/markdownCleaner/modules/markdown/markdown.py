"""Typed Markdown block model and conservative structure-preserving parser.

The parser identifies structure conservatively. ProcessingContext may expose
visible prose inside headings, lists, blockquotes, tables, footnotes, and links
to Markdown-aware processors while code, destinations, HTML, YAML, and control
syntax remain protected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Iterator

import yaml


HEADING_ATX = re.compile(r"^\s{0,3}#{1,6}\s+.+$")
HEADING_SETEXT = re.compile(r"^[=-]{3,}\s*$")
HORIZONTAL_RULE = re.compile(r"^\s{0,3}((\*\s*){3,}|(-\s*){3,}|(_\s*){3,})\s*$")
BLOCKQUOTE = re.compile(r"^\s{0,3}>\s?.*$")
LIST_ITEM = re.compile(r"^\s{0,3}([*+-]|\d+[.)])\s+.+$")
TABLE_ROW = re.compile(r"^\s*\|?.*\|.*\|?\s*$")
TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")
FOOTNOTE = re.compile(r"^\[\^[^\]]+\]:\s+.*$")
IMAGE_ONLY = re.compile(r"^\s*!\[[^\]]*\]\([^)]+\)\s*$")
LINK_ONLY = re.compile(r"^\s*\[[^\]]+\]\([^)]+\)\s*$")
REFERENCE_LINK = re.compile(r"^\s*\[[^\]]+\]:\s+\S+.*$")
FENCE_PATTERN = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")
HTML_START = re.compile(r"^\s*<([A-Za-z][A-Za-z0-9]*)\b")
HTML_COMMENT_START = re.compile(r"^\s*<!--")
HTML_COMMENT_END = re.compile(r"-->\s*$")
HTML_DECLARATION = re.compile(r"^\s*<![A-Za-z]")
HTML_AUTOLINK = re.compile(
    r"^\s*<(?:https?://[^ <>]+|mailto:[^ <>]+|[^ <>@]+@[^ <>@]+)>\s*$",
    re.IGNORECASE,
)
HTML_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


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


@dataclass(slots=True)
class MarkdownBlock:
    """Represent one logical block and its original source location."""

    block_type: BlockType
    text: str
    start_line: int
    end_line: int
    editable: bool = True
    metadata: dict = field(default_factory=dict)
    current_text: str | None = None

    def __post_init__(self) -> None:
        if self.current_text is None:
            self.current_text = self.text

    def line_count(self) -> int:
        """Return the number of source lines occupied by this block."""

        return self.end_line - self.start_line + 1

    def copy(self) -> "MarkdownBlock":
        """Return an independent copy, including the current edited text."""

        return MarkdownBlock(
            block_type=self.block_type,
            text=self.text,
            start_line=self.start_line,
            end_line=self.end_line,
            editable=self.editable,
            metadata=dict(self.metadata),
            current_text=self.current_text,
        )

    def __repr__(self) -> str:
        preview = self.content.replace("\n", "\\n")
        if len(preview) > 60:
            preview = preview[:57] + "..."
        return (
            f"<MarkdownBlock "
            f"type={self.block_type.name} "
            f"lines={self.start_line}-{self.end_line} "
            f"editable={self.editable} "
            f"text='{preview}'>"
        )

    def update(self, value: str) -> None:
        """Replace the block's current editable text."""

        self.current_text = value

    @property
    def content(self) -> str:
        """Compatibility alias for the current block text."""

        return self.current_text if self.current_text is not None else ""

    @content.setter
    def content(self, value: str) -> None:
        self.current_text = value


@dataclass
class MarkdownDocument:
    """Store parsed Markdown blocks in source order."""

    source: Path | None = None
    blocks: list[MarkdownBlock] = field(default_factory=list)
    trailing_newline: bool = False

    def add(self, block: MarkdownBlock) -> None:
        self.blocks.append(block)

    def __len__(self) -> int:
        return len(self.blocks)

    def __iter__(self) -> Iterator[MarkdownBlock]:
        return iter(self.blocks)

    def editable_blocks(self) -> Iterator[MarkdownBlock]:
        """Yield blocks whose narrative content may be edited."""

        return (block for block in self.blocks if block.editable)

    def protected_blocks(self) -> Iterator[MarkdownBlock]:
        """Yield structural blocks that must remain unchanged."""

        return (block for block in self.blocks if not block.editable)

    def rebuild(self) -> str:
        """Compatibility alias for :meth:`to_markdown`."""

        return self.to_markdown()

    def statistics(self) -> dict[str, int]:
        """Return block counts grouped by block type."""

        statistics: dict[str, int] = {}
        for block in self.blocks:
            name = block.block_type.name
            statistics[name] = statistics.get(name, 0) + 1
        return statistics

    def to_markdown(self) -> str:
        """Rebuild normalized-LF Markdown while preserving final-newline state."""

        markdown = "\n".join(block.content for block in self.blocks)
        if self.trailing_newline:
            markdown += "\n"
        return markdown


class MarkdownParser:
    """Parse Markdown into editable prose and protected structural blocks."""

    @staticmethod
    def _block(
        block_type: BlockType,
        lines: list[str],
        start: int,
        end: int | None = None,
        *,
        editable: bool = False,
    ) -> MarkdownBlock:
        """Build a block from inclusive zero-based line indexes."""

        final = start if end is None else end
        return MarkdownBlock(
            block_type=block_type,
            text="\n".join(lines[start : final + 1]),
            start_line=start + 1,
            end_line=final + 1,
            editable=editable,
        )

    @staticmethod
    def _is_paragraph_boundary(lines: list[str], index: int) -> bool:
        """Return whether ``lines[index]`` starts a protected block."""

        current = lines[index]
        total = len(lines)
        return bool(
            not current.strip()
            or HEADING_ATX.match(current)
            or (
                index + 1 < total
                and current.strip()
                and HEADING_SETEXT.match(lines[index + 1])
            )
            or HORIZONTAL_RULE.match(current)
            or BLOCKQUOTE.match(current)
            or LIST_ITEM.match(current)
            or (
                index + 1 < total
                and TABLE_ROW.match(current)
                and TABLE_SEPARATOR.match(lines[index + 1])
            )
            or FOOTNOTE.match(current)
            or REFERENCE_LINK.match(current)
            or IMAGE_ONLY.match(current)
            or LINK_ONLY.match(current)
            or FENCE_PATTERN.match(current)
            or current.startswith(("    ", "\t"))
            or HTML_COMMENT_START.match(current)
            or HTML_DECLARATION.match(current)
            or HTML_START.match(current)
        )

    @staticmethod
    def _frontmatter_end(lines: list[str]) -> int | None:
        """Return a closing delimiter only for mapping-shaped YAML metadata."""

        for end in range(1, len(lines)):
            if lines[end].strip() != "---":
                continue
            try:
                data = yaml.safe_load("\n".join(lines[1:end]))
            except yaml.YAMLError:
                return None
            return end if isinstance(data, dict) else None
        return None

    @staticmethod
    def _html_block_end(
        lines: list[str],
        start: int,
        opening: re.Match[str],
    ) -> int:
        """Find a matching close without swallowing an unclosed document tail."""

        line = lines[start]
        tag = opening.group(1)
        if (
            tag.casefold() in HTML_VOID_TAGS
            or line.rstrip().endswith("/>")
            or HTML_AUTOLINK.match(line)
        ):
            return start

        closing = re.compile(rf".*</{re.escape(tag)}>\s*$", re.IGNORECASE)
        for index in range(start, len(lines)):
            if closing.match(lines[index]):
                return index

        # CommonMark-style block HTML remains protected until a blank-line
        # boundary when its explicit close is absent.
        index = start
        while index + 1 < len(lines) and lines[index + 1].strip():
            index += 1
        return index

    @staticmethod
    def _html_comment_end(lines: list[str], start: int) -> int:
        """Find a bounded HTML comment, or protect only its unclosed opener."""

        for index in range(start, len(lines)):
            if HTML_COMMENT_END.search(lines[index]):
                return index

        index = start
        while index + 1 < len(lines) and lines[index + 1].strip():
            index += 1
        return index

    @staticmethod
    def _is_closing_fence(line: str, opening_marker: str) -> bool:
        """Return whether a line is a valid close for the opening fence."""

        character = re.escape(opening_marker[0])
        return bool(
            re.fullmatch(
                rf"[ \t]{{0,3}}{character}{{{len(opening_marker)},}}[ \t]*",
                line,
            )
        )

    def parse(self, markdown: str) -> MarkdownDocument:
        """Parse Markdown while retaining block order and final-newline state."""

        lines = markdown.splitlines()
        document = MarkdownDocument(
            trailing_newline=markdown.endswith(("\n", "\r")),
        )
        total = len(lines)
        index = 0

        while index < total:
            line = lines[index]

            if not line.strip():
                document.add(self._block(BlockType.BLANK, lines, index))
                index += 1
                continue

            if index == 0 and line.strip() == "---":
                end = self._frontmatter_end(lines)
                if end is not None:
                    document.add(
                        self._block(
                            BlockType.YAML_FRONTMATTER,
                            lines,
                            index,
                            end,
                        )
                    )
                    index = end + 1
                    continue

            fence = FENCE_PATTERN.match(line)
            if fence:
                marker = fence.group(2)
                start = index
                index += 1
                while index < total and not self._is_closing_fence(
                    lines[index],
                    marker,
                ):
                    index += 1
                end = min(index, total - 1)
                document.add(self._block(BlockType.CODE_FENCE, lines, start, end))
                index = end + 1
                continue

            if line.startswith(("    ", "\t")):
                start = index
                index += 1
                while index < total and lines[index].startswith(("    ", "\t")):
                    index += 1
                document.add(
                    self._block(BlockType.CODE_FENCE, lines, start, index - 1)
                )
                continue

            if HTML_COMMENT_START.match(line):
                start = index
                end = self._html_comment_end(lines, index)
                document.add(self._block(BlockType.HTML, lines, start, end))
                index = end + 1
                continue

            if HTML_DECLARATION.match(line):
                document.add(self._block(BlockType.HTML, lines, index))
                index += 1
                continue

            html = HTML_START.match(line)
            if html:
                end = self._html_block_end(lines, index, html)
                document.add(self._block(BlockType.HTML, lines, index, end))
                index = end + 1
                continue

            if HEADING_ATX.match(line):
                document.add(self._block(BlockType.HEADING, lines, index))
                index += 1
                continue

            if (
                index + 1 < total
                and line.strip()
                and HEADING_SETEXT.match(lines[index + 1])
            ):
                document.add(
                    self._block(BlockType.HEADING, lines, index, index + 1)
                )
                index += 2
                continue

            if HORIZONTAL_RULE.match(line):
                document.add(self._block(BlockType.HORIZONTAL_RULE, lines, index))
                index += 1
                continue

            if BLOCKQUOTE.match(line):
                start = index
                index += 1
                while index < total and BLOCKQUOTE.match(lines[index]):
                    index += 1
                document.add(
                    self._block(BlockType.BLOCKQUOTE, lines, start, index - 1)
                )
                continue

            if LIST_ITEM.match(line):
                start = index
                index += 1
                while index < total:
                    current = lines[index]
                    if LIST_ITEM.match(current) or current.startswith(("    ", "\t")):
                        index += 1
                        continue
                    break
                document.add(self._block(BlockType.LIST, lines, start, index - 1))
                continue

            if (
                index + 1 < total
                and TABLE_ROW.match(line)
                and TABLE_SEPARATOR.match(lines[index + 1])
            ):
                start = index
                index += 2
                while index < total and TABLE_ROW.match(lines[index]):
                    index += 1
                document.add(self._block(BlockType.TABLE, lines, start, index - 1))
                continue

            if FOOTNOTE.match(line):
                start = index
                index += 1
                while index < total and lines[index].startswith(("    ", "\t")):
                    index += 1
                document.add(
                    self._block(BlockType.FOOTNOTE, lines, start, index - 1)
                )
                continue

            if REFERENCE_LINK.match(line):
                document.add(self._block(BlockType.LINK, lines, index))
                index += 1
                continue

            if IMAGE_ONLY.match(line):
                document.add(self._block(BlockType.IMAGE, lines, index))
                index += 1
                continue

            if LINK_ONLY.match(line):
                document.add(self._block(BlockType.LINK, lines, index))
                index += 1
                continue

            start = index
            index += 1
            while index < total and not self._is_paragraph_boundary(lines, index):
                index += 1
            document.add(
                self._block(
                    BlockType.PARAGRAPH,
                    lines,
                    start,
                    index - 1,
                    editable=True,
                )
            )

        return document


def parse_markdown(text: str) -> MarkdownDocument:
    """Parse Markdown text using a fresh default parser."""

    return MarkdownParser().parse(text)
