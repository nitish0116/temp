"""Document-level cleanup for OCR/PDF extracted novels.

This stage intentionally runs before character-level OCR correction.  It removes
non-narrative front/back matter, picture OCR, normalizes headings, reconstructs
paragraphs split by PDF extraction, and prepares clean Markdown for TTS.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.stage import PipelineStage, StageResult


PICTURE_BLOCK = re.compile(
    r"<!--\s*Start of picture text\s*-->.*?<!--\s*End of picture text\s*-->",
    re.IGNORECASE | re.DOTALL,
)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
UNDERLINE_TAG = re.compile(r"</?u\s*>", re.IGNORECASE)

# Narrative headings used to identify where the actual book begins.
START_HEADING = re.compile(
    r"(?im)^\s*#{1,6}\s*(?:<u>)?\s*(?:"
    r"prologue|prelude|introduction|chapter\s+(?:\d+|[ivxlcdm]+|one)|"
    r"part\s+(?:\d+|[ivxlcdm]+|one)"
    r")\b.*$"
)

# Common post-book material not useful for novel TTS.
BACK_MATTER_HEADING = re.compile(
    r"(?im)^\s*#{1,6}\s*(?:<u>)?\s*(?:"
    r"afterword|author(?:'s)?\s+note|translator(?:'s)?\s+note|"
    r"character\s+profiles?|profile|yen\s+newsletter|newsletter|"
    r"about\s+the\s+author|copyright"
    r")\b.*$"
)

FOOTNOTE_DEFINITION = re.compile(r"(?m)^\s*\[\^[^\]]+\]:.*(?:\n(?: {2,}|\t).*)*\n?")
FOOTNOTE_REFERENCE = re.compile(r"\[\^[^\]]+\]")

ATX_HEADING = re.compile(r"^(\s*#{1,6})\s+(.+?)\s*$")
CHAPTER_HEADING = re.compile(
    r"^(?:chapter\s+([\divxlcdm]+))(?:\s*[|:\-–—]\s*|\s+)(.+)$",
    re.IGNORECASE,
)
SECTION_NUMBER = re.compile(r"^_?(\d+)_?$")


@dataclass
class _Change:
    reason: str
    before: str
    after: str
    confidence: float = 99.0


class DocumentCleanupStage(PipelineStage):
    """Clean the whole document before segment-level stages run."""

    name = "DocumentCleanup"
    config_section = "cleanup"

    def process(self, context) -> StageResult:
        text = context.current_markdown or context.original_markdown
        changes: list[_Change] = []

        if self.config.get("cleanup.remove_picture_ocr", True):
            new, count = PICTURE_BLOCK.subn("", text)
            if count:
                changes.append(_Change("Removed OCR extracted from pictures", f"{count} picture OCR block(s)", ""))
                text = new

        # Remove any residual comments left by converters.
        text = HTML_COMMENT.sub("", text)

        if self.config.get("cleanup.remove_front_matter", True):
            match = START_HEADING.search(text)
            if match and match.start() > 0:
                changes.append(_Change("Removed title/copyright/contents front matter", text[: min(match.start(), 500)], ""))
                text = text[match.start():]

        if self.config.get("cleanup.remove_back_matter", True):
            match = BACK_MATTER_HEADING.search(text)
            if match:
                changes.append(_Change("Removed afterword/profile/publisher back matter", text[match.start(): match.start()+500], ""))
                text = text[:match.start()]

        if self.config.get("cleanup.remove_footnotes", True):
            new, count = FOOTNOTE_DEFINITION.subn("", text)
            new = FOOTNOTE_REFERENCE.sub("", new)
            if count or new != text:
                changes.append(_Change("Removed Markdown footnotes", "footnotes present", "footnotes removed", 95.0))
                text = new

        before_headings = text
        text = self._normalize_headings(text)
        if text != before_headings:
            changes.append(_Change("Normalized Markdown chapter headings", "HTML/irregular headings", "plain Markdown headings"))

        before_paragraphs = text
        text = self._reconstruct_paragraphs(text)
        if text != before_paragraphs:
            changes.append(_Change("Reconstructed PDF/OCR wrapped paragraphs", "split paragraph lines", "joined prose paragraphs", 96.0))

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

    @staticmethod
    def _normalize_headings(text: str) -> str:
        out: list[str] = []
        for raw in text.splitlines():
            line = UNDERLINE_TAG.sub("", raw).strip()
            match = ATX_HEADING.match(line)
            if not match:
                out.append(raw.rstrip())
                continue

            body = match.group(2).strip()
            body = re.sub(r"^\*\*(.*?)\*\*$", r"\1", body)
            body = re.sub(r"^__(.*?)__$", r"\1", body)
            body = UNDERLINE_TAG.sub("", body).strip()

            chapter = CHAPTER_HEADING.match(body)
            if chapter:
                number, title = chapter.groups()
                number = number.upper() if re.fullmatch(r"[ivxlcdm]+", number, re.I) else number
                body = f"Chapter {number}: {title.strip()}"
            elif re.match(r"^(prologue|epilogue|prelude|introduction)\b", body, re.I):
                body = body[:1].upper() + body[1:]

            # Narrative headings are promoted to H1 for simple TTS Markdown.
            if re.match(r"^(chapter\b|prologue\b|epilogue\b|prelude\b|part\b|introduction\b)", body, re.I):
                out.append(f"# {body}")
            else:
                out.append(f"## {body}")
        return "\n".join(out)

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

        # Strong signal of a PDF line wrap across a blank line: the previous
        # chunk does not end a sentence and the continuation starts lower-case.
        if l.endswith("-") and re.match(r"^[a-z]", r):
            return True
        if not re.search(r"[.!?…\"'’”)]$", l) and re.match(r"^[a-z]", r):
            return True
        return False

    @classmethod
    def _reconstruct_paragraphs(cls, text: str) -> str:
        # First normalize hard line wraps inside each blank-line-delimited block.
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

        # Then repair extraction that inserted a blank line in the middle of a sentence.
        merged: list[str] = []
        for block in blocks:
            if merged and cls._should_join(merged[-1], block):
                left = merged.pop().rstrip()
                if left.endswith("-") and re.match(r"^[a-z]", block):
                    merged.append(left[:-1] + block.lstrip())
                else:
                    merged.append(left + " " + block.lstrip())
            else:
                merged.append(block)
        return "\n\n".join(merged)

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
