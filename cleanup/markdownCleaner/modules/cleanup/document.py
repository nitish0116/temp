"""Generalized document-level cleanup for OCR/PDF extracted Markdown.

Design goals
------------
* Never discard arbitrary document ranges because of where a section appears.
* Prefer local, evidence-based cleanup over novel-specific sequence assumptions.
* Preserve unknown content and unknown headings by default.
* Allow explicit section exclusion by heading name without deleting later sections.
* Remove picture OCR only when it is very likely to be extraction noise.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ..core.stage import PipelineStage, StageResult


PICTURE_BLOCK = re.compile(
    r"<!--\s*Start of picture text\s*-->.*?<!--\s*End of picture text\s*-->",
    re.IGNORECASE | re.DOTALL,
)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
UNDERLINE_TAG = re.compile(r"</?u\s*>", re.IGNORECASE)
HTML_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
ATX_HEADING = re.compile(r"^(\s*#{1,6})\s+(.+?)\s*$")
SECTION_NUMBER = re.compile(r"^_?(\d+)_?$")
FOOTNOTE_DEFINITION = re.compile(r"(?m)^\s*\[\^[^\]]+\]:.*(?:\n(?: {2,}|\t).*)*\n?")
FOOTNOTE_REFERENCE = re.compile(r"\[\^[^\]]+\]")

# Backward-compatible regex exports retained for callers/tests from older releases.
# They are no longer used to truncate documents.
START_HEADING = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:<u>\s*)?(?:prologue|prelude|introduction|"
    r"(?:chapter|story|part|book|volume|act|section)\s+(?:\d+|[ivxlcdm]+)\s*[|:])",
    re.IGNORECASE,
)
BACK_MATTER_HEADING = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?[_*\s]*(?:<u>\s*)?[_*\s]*(?:"
    r"a(?:fter|fer)word|yen\s+news(?:letter|leter)|newsletter|newsleter"
    r")[_*\s]*(?:</u>)?[_*\s]*$"
)

# These patterns are intentionally generic. They only *promote* strong plain-text
# headings; existing Markdown headings are preserved even when their text is unknown.
NUMBERED_HEADING = re.compile(
    r"^(chapter|story|part|book|volume|act|section)\s+"
    r"([\divxlcdm]+)(?:\s*[|:\-–—]\s*|\s+)(.+)$",
    re.IGNORECASE,
)
NAMED_HEADING = re.compile(
    r"^(prologue|epilogue|prelude|introduction|interlude|appendix|"
    r"afterword|foreword|acknowledg(?:e)?ments?|character\s+profiles?|glossary|"
    r"notes|references|bibliography)(?:\s*[|:\-–—]\s*(.+))?$",
    re.IGNORECASE,
)

# Front-matter blocks which are safe to remove *locally* when explicitly enabled.
# No rule here slices from the start of the document to a later narrative heading.
LOCAL_METADATA_HEADINGS = {
    "copyright",
    "contents",
    "table of contents",
    "title page",
    "publication information",
    "publishing information",
    "library of congress cataloging-in-publication data",
}

# Common standalone metadata lines. These are removed individually only in safe
# front-matter cleanup; surrounding prose is never deleted because of them.
METADATA_LINE_PATTERNS = [
    re.compile(r"^isbn(?:s)?\s*[: ]", re.I),
    re.compile(r"^lccn\s*[: ]", re.I),
    re.compile(r"^first\s+(?:ebook|yen on|edition|published)\b", re.I),
    re.compile(r"^all rights reserved\.?$", re.I),
    re.compile(r"^©\s*\d{4}\b", re.I),
    re.compile(r"^(?:visit us at\s+)?(?:https?://)?(?:www\.)?\S+\.com\S*$", re.I),
]


@dataclass
class _Change:
    reason: str
    before: str
    after: str
    confidence: float = 99.0


class DocumentCleanupStage(PipelineStage):
    """Generalized whole-document cleanup before OCR correction stages."""

    name = "DocumentCleanup"
    config_section = "cleanup"

    def process(self, context) -> StageResult:
        text = context.current_markdown or context.original_markdown
        changes: list[_Change] = []

        picture_mode = str(self.config.get("cleanup.picture_ocr_mode", "safe")).lower()
        # Backward compatibility with the old boolean option.
        if not self.config.get("cleanup.remove_picture_ocr", True):
            picture_mode = "keep"
        text, removed_count, preserved_count = self._filter_picture_ocr(text, mode=picture_mode)
        if removed_count:
            changes.append(_Change(
                "Removed likely picture-OCR noise",
                f"{removed_count} noisy picture OCR block(s)",
                "",
                97.0,
            ))
        if preserved_count:
            changes.append(_Change(
                "Preserved readable picture-OCR content",
                f"{preserved_count} readable picture OCR block(s)",
                "text retained",
                99.0,
            ))

        # Residual non-picture comments are converter markup, not content.
        text = HTML_COMMENT.sub("", text)

        if self.config.get("cleanup.remove_front_matter", True):
            before = text
            text = self._remove_local_metadata(text)
            if text != before:
                changes.append(_Change(
                    "Removed local publication metadata without sequence-based truncation",
                    "metadata blocks/lines",
                    "",
                    96.0,
                ))

        excluded = self.config.get("cleanup.excluded_sections", ["Afterword", "Aferword"])
        if excluded:
            before = text
            text, removed_sections = self._remove_named_sections(text, excluded)
            if removed_sections:
                changes.append(_Change(
                    "Removed explicitly configured sections only",
                    ", ".join(removed_sections),
                    "",
                    99.0,
                ))

        if self.config.get("cleanup.remove_footnotes", True):
            new, count = FOOTNOTE_DEFINITION.subn("", text)
            new = FOOTNOTE_REFERENCE.sub("", new)
            if count or new != text:
                changes.append(_Change("Removed Markdown footnotes", "footnotes present", "footnotes removed", 95.0))
                text = new

        before_markup = text
        text = HTML_BREAK.sub("\n", text)
        text = UNDERLINE_TAG.sub("", text)
        text = HTML_COMMENT.sub("", text)
        text = re.sub(r"[ \t]+", " ", text)
        if text != before_markup:
            changes.append(_Change("Normalized residual HTML/Markdown markup", "legacy inline markup", "normalized markup"))

        before_headings = text
        text = self._normalize_headings(text)
        if text != before_headings:
            changes.append(_Change("Normalized structural headings", "irregular headings", "normalized Markdown headings"))

        before_paragraphs = text
        text = self._reconstruct_paragraphs(text)
        if text != before_paragraphs:
            changes.append(_Change("Reconstructed PDF/OCR wrapped paragraphs", "split paragraph lines", "joined prose paragraphs", 96.0))

        if self.config.get("cleanup.strip_markdown_emphasis", True):
            before_emphasis = text
            text = self._strip_markdown_emphasis(text)
            if text != before_emphasis:
                changes.append(_Change("Removed Markdown emphasis markers for TTS", "Markdown emphasis", "plain text"))

        before_space = text
        text = self._normalize_spacing(text)
        if text != before_space:
            changes.append(_Change("Normalized whitespace and blank lines", "irregular whitespace", "normalized whitespace"))

        if text != context.current_markdown:
            context.replace_markdown(text)

        for item in changes:
            context.tracker.add(
                stage=self.name,
                block_index=-1,
                segment_index=-1,
                line=0,
                before=item.before,
                after=item.after,
                confidence=item.confidence,
                reason=item.reason,
            )

        return StageResult(stage=self.name, changes=len(changes))

    # ------------------------------------------------------------------
    # Picture OCR
    # ------------------------------------------------------------------
    @staticmethod
    def _clean_picture_block(block: str) -> str:
        inner = re.sub(r"^<!--\s*Start of picture text\s*-->", "", block, flags=re.I).strip()
        inner = re.sub(r"<!--\s*End of picture text\s*-->$", "", inner, flags=re.I).strip()
        inner = HTML_BREAK.sub("\n", inner)
        inner = HTML_COMMENT.sub("", inner)
        inner = UNDERLINE_TAG.sub("", inner)
        inner = re.sub(r"[ \t]+", " ", inner)
        inner = re.sub(r"\n{3,}", "\n\n", inner).strip()
        return inner

    @staticmethod
    def _picture_text_is_readable(text: str) -> bool:
        """Conservative readability heuristic for OCR extracted from images.

        Readable captions, maps, profile cards, diagrams, and labels are retained.
        Blocks dominated by punctuation/single-character fragments are removed.
        """
        if not text.strip():
            return False
        chars = [c for c in text if not c.isspace()]
        if not chars:
            return False
        alpha = sum(c.isalpha() for c in chars)
        alnum = sum(c.isalnum() for c in chars)
        alpha_ratio = alpha / len(chars)
        alnum_ratio = alnum / len(chars)
        words = re.findall(r"[A-Za-z][A-Za-z'’\-]{1,}", text)
        meaningful = [w for w in words if len(w) >= 3]

        # A small caption or label can still be useful.
        if len(meaningful) >= 3 and alpha_ratio >= 0.45:
            return True
        # Larger text blocks need a reasonable amount of language-like content.
        if len(meaningful) >= 6 and alnum_ratio >= 0.55:
            return True
        return False

    @classmethod
    def _filter_picture_ocr(cls, text: str, mode: str = "safe") -> tuple[str, int, int]:
        """Filter picture OCR without relying on document sequence.

        Modes:
        * ``keep``: preserve every picture OCR block as cleaned text.
        * ``remove``: remove every picture OCR block.
        * ``safe`` (default): preserve readable blocks, remove likely gibberish.
        """
        mode = mode.lower()
        if mode not in {"keep", "remove", "safe"}:
            mode = "safe"

        removed = 0
        preserved = 0
        pieces: list[str] = []
        cursor = 0
        for match in PICTURE_BLOCK.finditer(text):
            pieces.append(text[cursor:match.start()])
            cleaned = cls._clean_picture_block(match.group(0))
            if cleaned:
                cleaned = cls._normalize_headings(cleaned)
            keep = mode == "keep" or (mode == "safe" and cls._picture_text_is_readable(cleaned))
            if keep and cleaned:
                preserved += 1
                pieces.append("\n\n" + cleaned + "\n\n")
            else:
                removed += 1
            cursor = match.end()
        pieces.append(text[cursor:])
        return "".join(pieces), removed, preserved

    # ------------------------------------------------------------------
    # Local metadata / explicit section exclusion
    # ------------------------------------------------------------------
    @staticmethod
    def _plain_heading_text(line: str) -> str | None:
        raw = line.strip()
        atx = ATX_HEADING.match(raw)
        body = atx.group(2).strip() if atx else raw
        body = UNDERLINE_TAG.sub("", body)
        body = re.sub(r"^[*_]+|[*_]+$", "", body).strip()
        if atx or NAMED_HEADING.match(body) or NUMBERED_HEADING.match(body):
            return body
        # Generic short title-like plain headings are deliberately not assumed;
        # preserving content is safer than over-classification.
        return None

    @classmethod
    def _remove_local_metadata(cls, text: str) -> str:
        """Remove only clearly identified metadata blocks/lines in place."""
        lines = text.splitlines()
        out: list[str] = []
        i = 0
        while i < len(lines):
            heading = cls._plain_heading_text(lines[i])
            normalized = heading.casefold().strip(" :") if heading else ""
            if normalized in LOCAL_METADATA_HEADINGS:
                # Remove this metadata section only until the next recognized heading.
                i += 1
                while i < len(lines) and cls._plain_heading_text(lines[i]) is None:
                    i += 1
                continue

            line = lines[i]
            stripped = UNDERLINE_TAG.sub("", line).strip()
            if any(pattern.search(stripped) for pattern in METADATA_LINE_PATTERNS):
                i += 1
                continue
            out.append(line)
            i += 1
        return "\n".join(out)

    @classmethod
    def _remove_named_sections(cls, text: str, names: Iterable[str]) -> tuple[str, list[str]]:
        """Remove explicitly named sections in place, regardless of their order.

        Crucially, this does *not* truncate the rest of the document. If another
        recognized section follows, processing resumes there.
        """
        targets = {str(name).casefold().strip() for name in names if str(name).strip()}
        if not targets:
            return text, []

        lines = text.splitlines()
        out: list[str] = []
        removed: list[str] = []
        i = 0
        while i < len(lines):
            heading = cls._plain_heading_text(lines[i])
            if heading and heading.casefold().strip(" :") in targets:
                removed.append(heading)
                i += 1
                while i < len(lines):
                    next_heading = cls._plain_heading_text(lines[i])
                    if next_heading:
                        break
                    i += 1
                continue
            out.append(lines[i])
            i += 1
        return "\n".join(out), removed

    # ------------------------------------------------------------------
    # Heading normalization
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_headings(text: str) -> str:
        out: list[str] = []

        for raw in text.splitlines():
            stripped = raw.strip()
            atx = ATX_HEADING.match(stripped)
            body = atx.group(2).strip() if atx else stripped
            body = UNDERLINE_TAG.sub("", body).strip()
            body = re.sub(r"^\*\*(.*?)\*\*$", r"\1", body)
            body = re.sub(r"^__(.*?)__$", r"\1", body).strip()

            numbered = NUMBERED_HEADING.match(body)
            named = NAMED_HEADING.match(body)

            if numbered:
                kind, number, title = numbered.groups()
                kind = kind[:1].upper() + kind[1:].lower()
                number = number.upper() if re.fullmatch(r"[ivxlcdm]+", number, re.I) else number
                out.append(f"# {kind} {number}: {title.strip()}")
                continue

            if named:
                kind, title = named.groups()
                canonical = kind[:1].upper() + kind[1:]
                if re.fullmatch(r"character\s+profiles?", kind, re.I):
                    canonical = "Character Profiles"
                if title:
                    canonical += f": {title.strip()}"
                out.append(f"# {canonical}")
                continue

            if atx:
                # Preserve existing user/source headings. We no longer demote all
                # unknown ATX headings, because doing so can destroy legitimate
                # structure in non-novel documents. Only clearly sentence-fragment
                # converter artifacts are demoted.
                if DocumentCleanupStage._looks_like_false_heading(body):
                    out.append(body)
                else:
                    level = len(atx.group(1).strip())
                    out.append(f"{'#' * level} {body}")
            else:
                out.append(raw.rstrip())

        # Collapse adjacent exact duplicate headings only.
        deduped: list[str] = []
        for line in out:
            if line.startswith("#"):
                previous_nonblank = next((x for x in reversed(deduped) if x.strip()), None)
                if previous_nonblank and previous_nonblank.startswith("#") and line.casefold() == previous_nonblank.casefold():
                    continue
            deduped.append(line)
        return "\n".join(deduped)

    @staticmethod
    def _looks_like_false_heading(body: str) -> bool:
        """Detect only high-confidence converter-created prose headings."""
        s = body.strip()
        if not s:
            return True
        if NUMBERED_HEADING.match(s) or NAMED_HEADING.match(s):
            return False
        # Dialogue/sentence fragments are not headings.
        if s.startswith(("“", '"', "‘", "'", "—")):
            return True
        if s.endswith(("—", ",", ";", ":")):
            return True
        if re.match(r"^[a-z]", s):
            return True
        if re.fullmatch(r"[A-Z][A-Za-z'’\-]*(?:\s+[A-Z][A-Za-z'’\-]*)?[.!?…]", s):
            return True
        # Timestamps produced as headings by converters.
        if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?(?:…|\.\.\.)?", s):
            return True
        return False

    # ------------------------------------------------------------------
    # Paragraph reconstruction
    # ------------------------------------------------------------------
    @staticmethod
    def _is_heading(block: str) -> bool:
        return bool(re.match(r"^\s*#{1,6}\s+", block))

    @staticmethod
    def _is_list_or_special(block: str) -> bool:
        stripped = block.lstrip()
        return bool(
            stripped.startswith(("```", "~~~", ">", "|"))
            or re.match(r"^(?:[-*+] |\d+[.)] )", stripped)
        )

    @staticmethod
    def _looks_structured_lines(lines: list[str]) -> bool:
        """Preserve blocks that look like records/cards/tables rather than prose."""
        if len(lines) < 2:
            return False
        short = sum(len(line) <= 48 for line in lines)
        labelish = sum(bool(re.match(r"^[A-Z][A-Za-z0-9 /&()'’+\-]{0,40}:?$", line)) for line in lines)
        colon_labels = sum(":" in line[:30] for line in lines)
        return (short / len(lines) >= 0.75 and labelish >= 2) or colon_labels >= 2

    @classmethod
    def _should_join(cls, left: str, right: str) -> bool:
        if not left or not right or cls._is_heading(left) or cls._is_heading(right):
            return False
        if cls._is_list_or_special(left) or cls._is_list_or_special(right):
            return False
        if SECTION_NUMBER.match(left.strip()) or SECTION_NUMBER.match(right.strip()):
            return False
        l = left.rstrip()
        r = right.lstrip()
        if not r:
            return False
        if l.endswith("-") and re.match(r"^[a-z]", r):
            return True
        if l.endswith("—") and r.startswith("—"):
            return True
        if not re.search(r"[.!?…\"'’”)]$", l) and re.match(r"^[a-z]", r):
            return True
        return False

    @classmethod
    def _reconstruct_paragraphs(cls, text: str) -> str:
        raw_blocks = re.split(r"\n\s*\n+", text.strip())
        blocks: list[str] = []
        for block in raw_blocks:
            stripped = block.strip()
            if not stripped:
                continue
            if cls._is_heading(stripped) or cls._is_list_or_special(stripped):
                blocks.append(stripped)
                continue
            lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
            if cls._looks_structured_lines(lines):
                blocks.append("\n".join(lines))
                continue
            if len(lines) > 1:
                joined = lines[0]
                for line in lines[1:]:
                    if joined.endswith("-") and re.match(r"^[a-z]", line):
                        joined = joined[:-1] + line
                    else:
                        joined += " " + line
                blocks.append(joined)
            else:
                blocks.append(stripped)

        merged: list[str] = []
        for block in blocks:
            if merged and cls._should_join(merged[-1], block):
                left = merged.pop().rstrip()
                if left.endswith("-") and re.match(r"^[a-z]", block):
                    merged.append(left[:-1] + block.lstrip())
                elif left.endswith("—") and block.lstrip().startswith("—"):
                    merged.append(left + block.lstrip()[1:])
                else:
                    merged.append(left + " " + block.lstrip())
            else:
                merged.append(block)
        return "\n\n".join(merged)

    @staticmethod
    def _strip_markdown_emphasis(text: str) -> str:
        text = re.sub(r"(?<!\w)\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\w)", r"\1", text)
        text = re.sub(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)", r"\1", text)
        text = re.sub(r"(?<!\w)\*(?=\S)(.+?)(?<=\S)\*(?!\w)", r"\1", text)
        text = re.sub(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)", r"\1", text)
        return text

    @staticmethod
    def _normalize_spacing(text: str) -> str:
        lines = []
        for line in text.splitlines():
            if re.match(r"^\s*#{1,6}\s+", line):
                lines.append(re.sub(r"\s+$", "", line))
            else:
                line = re.sub(r"[ \t]+", " ", line).strip()
                lines.append(line)
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+([,.;:!?])", r"\1", text)
        return text.strip() + "\n"
