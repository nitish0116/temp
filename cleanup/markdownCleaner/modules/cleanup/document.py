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
GLOSSARY_FOOTNOTE = re.compile(r"^\s*>?\s*\d+\s+\*\*\S(?:.*?\S)?\*\*(?:\s+.*)?$")
SIGNUP_OR_NEWSLETTER = re.compile(
    r"(?:\bsign\s*up\b.*\bnewsletter\b|"
    r"\bnewsletter\s+sign\s*up\b|"
    r"(?:https?://|www\.)?(?:www\.)?yenpress\.com(?:/\S*)?|"
    r"\byen\s+(?:press\s+)?newsletter\b)",
    re.I,
)
TRAILING_TOC_ITEM = re.compile(
    r"^\s*\d+[.)]\s+(?:cover|insert|title\s+page|copyright|"
    r"chapter\b.*|afterword|appendix\b.*|yen\s+newsletter)\s*$",
    re.I,
)

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
    r"bonus\s+short\s+stories|short\s+stories|side\s+stories|extras?|"
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


# Standalone ornamental scene separators. Remove only when the entire line is
# composed of decorative glyphs (optionally separated by whitespace).
DECORATIVE_SEPARATOR_LINE = re.compile(
    r"(?m)^[ \t]*(?:[◆◇■□●○♦♢✦✧❖◈※＊*•·~_=+\-][ \t]*){3,}$"
)

# Strong indicators that text before the first real narrative section is
# publication/cover/navigation material rather than story prose.
FRONT_MATTER_SIGNALS = [
    re.compile(r"\bcopyright\b", re.I),
    re.compile(r"\btable of contents\b|\bcontents\b", re.I),
    re.compile(r"\bbegin reading\b", re.I),
    re.compile(r"\byen (?:press|on)\b", re.I),
    re.compile(r"\bj-novel club\b", re.I),
    re.compile(r"\billustration by\b|\bcover art by\b", re.I),
    re.compile(r"\btranslation by\b", re.I),
    re.compile(r"\bisbn\b|\blccn\b|cataloging-in-publication", re.I),
    re.compile(r"\ball rights reserved\b", re.I),
    re.compile(r"\bscanning, uploading\b|\bdistribution of this book\b", re.I),
    re.compile(r"\bpublisher\b", re.I),
]

NARRATIVE_SECTION = re.compile(
    r"^(?:prologue|prelude|introduction|interlude|epilogue|"
    r"(?:chapter|story|part|book|act|section)\s+(?:\d+|[ivxlcdm]+)\b)",
    re.I,
)


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

        excluded = self.config.get(
            "cleanup.excluded_sections",
            [
                "Afterword",
                "Aferword",
                "Character Profiles",
                "Character Profile",
                "Character Profles",
                "Character Profle",
            ],
        )

        picture_mode = str(self.config.get("cleanup.picture_ocr_mode", "safe")).lower()
        # Backward compatibility with the old boolean option.
        if not self.config.get("cleanup.remove_picture_ocr", True):
            picture_mode = "keep"
        text, removed_count, preserved_count = self._filter_picture_ocr(
            text,
            mode=picture_mode,
            excluded_sections=excluded,
        )
        if removed_count:
            changes.append(
                _Change(
                    "Removed likely picture-OCR noise",
                    f"{removed_count} noisy picture OCR block(s)",
                    "",
                    97.0,
                )
            )
        if preserved_count:
            changes.append(
                _Change(
                    "Preserved readable picture-OCR content",
                    f"{preserved_count} readable picture OCR block(s)",
                    "text retained",
                    99.0,
                )
            )

        # Residual non-picture comments are converter markup, not content.
        text = HTML_COMMENT.sub("", text)

        if self.config.get("cleanup.remove_front_matter", True):
            before = text
            text = self._remove_leading_front_matter(text)
            if text != before:
                changes.append(
                    _Change(
                        "Removed strongly identified leading cover/publication front matter",
                        "cover/navigation/publication prefix",
                        "",
                        98.0,
                    )
                )

        if self.config.get("cleanup.remove_front_matter", True):
            before = text
            text = self._remove_local_metadata(text)
            if text != before:
                changes.append(
                    _Change(
                        "Removed local publication metadata without sequence-based truncation",
                        "metadata blocks/lines",
                        "",
                        96.0,
                    )
                )

        if excluded:
            before = text
            text, removed_sections = self._remove_named_sections(text, excluded)
            if removed_sections:
                changes.append(
                    _Change(
                        "Removed explicitly configured sections only",
                        ", ".join(removed_sections),
                        "",
                        99.0,
                    )
                )

        if self.config.get("cleanup.remove_promotional_tail", True):
            before = text
            text = self._remove_promotional_tail(text)
            if text != before:
                changes.append(
                    _Change(
                        "Removed publisher next-volume promotional tail",
                        "coming-soon preview",
                        "",
                        98.0,
                    )
                )

        if self.config.get("cleanup.remove_glossary_footnotes", True):
            before = text
            text, removed_glossary = self._remove_bounded_glossary_footnotes(text)
            if removed_glossary:
                changes.append(
                    _Change(
                        "Removed bounded bare/blockquoted glossary footnotes",
                        f"{removed_glossary} glossary footnote block(s)",
                        "",
                        98.0,
                    )
                )

        if self.config.get("cleanup.remove_publisher_tail", True):
            before = text
            text, tail_kind = self._remove_generic_publisher_tail(text)
            if tail_kind:
                changes.append(
                    _Change(
                        f"Removed trailing {tail_kind}",
                        "publisher/navigation tail",
                        "",
                        98.0,
                    )
                )

        before_decorative = text
        text = DECORATIVE_SEPARATOR_LINE.sub("", text)
        if text != before_decorative:
            changes.append(
                _Change(
                    "Removed standalone decorative separator lines",
                    "ornamental symbol separators",
                    "",
                    99.0,
                )
            )

        if self.config.get("cleanup.remove_footnotes", True):
            new, count = FOOTNOTE_DEFINITION.subn("", text)
            new = FOOTNOTE_REFERENCE.sub("", new)
            if count or new != text:
                changes.append(
                    _Change(
                        "Removed Markdown footnotes",
                        "footnotes present",
                        "footnotes removed",
                        95.0,
                    )
                )
                text = new

        before_markup = text
        text = HTML_BREAK.sub("\n", text)
        text = UNDERLINE_TAG.sub("", text)
        text = HTML_COMMENT.sub("", text)
        text = re.sub(r"[ \t]+", " ", text)
        if text != before_markup:
            changes.append(
                _Change(
                    "Normalized residual HTML/Markdown markup",
                    "legacy inline markup",
                    "normalized markup",
                )
            )

        before_headings = text
        text = self._normalize_headings(text)
        if text != before_headings:
            changes.append(
                _Change(
                    "Normalized structural headings",
                    "irregular headings",
                    "normalized Markdown headings",
                )
            )

        before_paragraphs = text
        text = self._reconstruct_paragraphs(text)
        if text != before_paragraphs:
            changes.append(
                _Change(
                    "Reconstructed PDF/OCR wrapped paragraphs",
                    "split paragraph lines",
                    "joined prose paragraphs",
                    96.0,
                )
            )

        if self.config.get("cleanup.strip_markdown_emphasis", True):
            before_emphasis = text
            text = self._strip_markdown_emphasis(text)
            if text != before_emphasis:
                changes.append(
                    _Change(
                        "Removed Markdown emphasis markers for TTS",
                        "Markdown emphasis",
                        "plain text",
                    )
                )

        before_space = text
        text = self._normalize_spacing(text)
        if text != before_space:
            changes.append(
                _Change(
                    "Normalized whitespace and blank lines",
                    "irregular whitespace",
                    "normalized whitespace",
                )
            )

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

        noise_findings = []
        if self.config.get("cleanup.report_ocr_noise", True):
            noise_findings = self._find_conservative_ocr_noise(
                text,
                limit=int(self.config.get("cleanup.ocr_noise_report_limit", 100)),
            )
            for line_number, line, reason in noise_findings:
                context.tracker.add(
                    stage="OCRNoiseReview",
                    block_index=-1,
                    segment_index=-1,
                    line=line_number,
                    before=line,
                    after=line,
                    confidence=0.0,
                    reason=f"Report only; text was preserved: {reason}",
                )

        return StageResult(stage=self.name, changes=len(changes) + len(noise_findings))

    # ------------------------------------------------------------------
    # Picture OCR
    # ------------------------------------------------------------------
    @staticmethod
    def _clean_picture_block(block: str) -> str:
        inner = re.sub(
            r"^<!--\s*Start of picture text\s*-->", "", block, flags=re.I
        ).strip()
        inner = re.sub(
            r"<!--\s*End of picture text\s*-->$", "", inner, flags=re.I
        ).strip()
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
        nonempty_lines = [line.strip() for line in text.splitlines() if line.strip()]

        # Reject multiline OCR blocks dominated by punctuation, isolated letters,
        # and tiny fragments. A single readable phrase such as "Chapter2 Journey"
        # must not rescue an otherwise garbage-heavy image OCR block.
        if len(nonempty_lines) >= 8:
            language_like_lines = 0
            noisy_lines = 0
            for line in nonempty_lines:
                line_words = re.findall(r"[A-Za-z][A-Za-z'’\-]{1,}", line)
                line_chars = [c for c in line if not c.isspace()]
                line_alpha = sum(c.isalpha() for c in line_chars)
                line_alpha_ratio = line_alpha / len(line_chars) if line_chars else 0.0
                if len(line_words) >= 3 and line_alpha_ratio >= 0.55:
                    language_like_lines += 1
                if len(line_words) <= 1 or line_alpha_ratio < 0.35:
                    noisy_lines += 1

            if noisy_lines / len(nonempty_lines) >= 0.50:
                return False
            if language_like_lines / len(nonempty_lines) < 0.25:
                return False

        # A small caption or label can still be useful.
        if len(meaningful) >= 3 and alpha_ratio >= 0.45:
            return True
        # Larger text blocks need a reasonable amount of language-like content.
        if len(meaningful) >= 6 and alnum_ratio >= 0.55:
            return True
        return False

    @classmethod
    def _filter_picture_ocr(
        cls,
        text: str,
        mode: str = "safe",
        excluded_sections: Iterable[str] | None = None,
    ) -> tuple[str, int, int]:
        """Filter picture OCR without relying on document sequence.

        Modes:
        * ``keep``: preserve every picture OCR block as cleaned text.
        * ``remove``: remove every picture OCR block.
        * ``safe`` (default): preserve readable blocks, remove likely gibberish.
        """
        mode = mode.lower()
        if mode not in {"keep", "remove", "safe"}:
            mode = "safe"

        def section_key(value: str) -> str:
            value = UNDERLINE_TAG.sub("", str(value))
            value = re.sub(r"^#{1,6}\\s*", "", value.strip())
            value = re.sub(r"[*_`]+", "", value)
            value = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
            value = re.sub(r"\\bprofles\\b", "profiles", value)
            value = re.sub(r"\\bprofle\\b", "profile", value)
            value = re.sub(
                r"^[a-z0-9 ]+\\s+character profiles$", "character profiles", value
            )
            return re.sub(r"\\s+", " ", value)

        excluded_keys = {
            section_key(name) for name in (excluded_sections or []) if str(name).strip()
        }

        removed = 0
        preserved = 0
        pieces: list[str] = []
        cursor = 0
        for match in PICTURE_BLOCK.finditer(text):
            pieces.append(text[cursor : match.start()])
            cleaned = cls._clean_picture_block(match.group(0))
            if cleaned:
                cleaned = cls._normalize_headings(cleaned)
            # Some excluded sections (notably image-heavy Character Profiles)
            # announce themselves inside a picture-OCR block. Preserve only a
            # synthetic section marker so the later local section remover can
            # discard the whole section, including prose between image blocks.
            marker = None
            if cleaned and excluded_keys:
                cleaned_lines = [
                    line.strip() for line in cleaned.splitlines() if line.strip()
                ]
                for line in cleaned_lines:
                    key = section_key(line)
                    if key in excluded_keys:
                        marker = line
                        break
                if marker is None and len(cleaned_lines) >= 2:
                    combined = section_key(" ".join(cleaned_lines[:2]))
                    if combined in excluded_keys:
                        marker = " ".join(cleaned_lines[:2])

            if marker:
                preserved += 1
                pieces.append("\n\n# " + marker + "\n\n")
            else:
                keep = mode == "keep" or (
                    mode == "safe" and cls._picture_text_is_readable(cleaned)
                )
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

        # Image/OCR conversions often prefix the profile section with the series
        # title, e.g. "OVERLORD Character Profiles", or misspell "Profiles".
        if re.fullmatch(
            r"(?:[A-Z][A-Z0-9 _'’:\-]{2,}\s+)?character\s+prof(?:iles?|les?)",
            body,
            flags=re.I,
        ):
            return "Character Profiles"

        if atx or NAMED_HEADING.match(body) or NUMBERED_HEADING.match(body):
            return body
        # Generic short title-like plain headings are deliberately not assumed;
        # preserving content is safer than over-classification.
        return None

    @classmethod
    def _remove_leading_front_matter(cls, text: str) -> str:
        """Remove a leading cover/publication prefix only when evidence is strong.

        This never truncates content after the first real narrative section. It
        exists for OCR/ebook conversions where cover text, reader navigation,
        copyright boilerplate, and publisher data appear before the first
        Prologue/Chapter/Story heading without clean Markdown section markers.
        """
        lines = text.splitlines()
        first_narrative: int | None = None

        # Some ebook conversions use explicit markers such as
        # "[chapter] 0 Prologue". These are much stronger evidence than chapter
        # names listed inside a preceding table of contents.
        for idx, line in enumerate(lines):
            if re.match(r"^\s*\[chapter\]\s*\S+", line, flags=re.I):
                first_narrative = idx
                break

        if first_narrative is None:
            narrative_iter = enumerate(lines)
        else:
            narrative_iter = ()

        for idx, line in narrative_iter:
            heading = cls._plain_heading_text(line)
            candidate = heading or UNDERLINE_TAG.sub("", line).strip()
            candidate = re.sub(r"^[*_`]+|[*_`]+$", "", candidate).strip()
            candidate = re.sub(r"^#{1,6}\s*", "", candidate).strip()
            if NARRATIVE_SECTION.match(candidate):
                section_mentions = re.findall(
                    r"\b(?:chapter|story|part|book|act|section)\s+(?:\d+|[ivxlcdm]+)\b",
                    candidate,
                    flags=re.I,
                )
                # TOC lines often concatenate several entries or list a single
                # plain "Chapter 8 Title" item. Treat a numbered line as a strong
                # narrative start only when it is an actual Markdown heading or
                # uses an explicit title delimiter (:, |, dash).
                raw_stripped = line.strip()
                is_atx = bool(ATX_HEADING.match(raw_stripped))
                has_title_delimiter = bool(
                    re.match(
                        r"^(?:chapter|story|part|book|act|section)\s+"
                        r"(?:\d+|[ivxlcdm]+)\s*[|:\-–—]\s*\S",
                        candidate,
                        flags=re.I,
                    )
                )
                is_named = bool(
                    re.match(
                        r"^(?:prologue|prelude|introduction|interlude|epilogue)\s*$",
                        candidate,
                        flags=re.I,
                    )
                )
                if (
                    len(section_mentions) > 1
                    or len(candidate) > 180
                    or (not is_named and not is_atx and not has_title_delimiter)
                ):
                    continue
                first_narrative = idx
                break

        if first_narrative is None or first_narrative == 0:
            return text

        prefix = "\n".join(lines[:first_narrative])
        signal_count = sum(
            bool(pattern.search(prefix)) for pattern in FRONT_MATTER_SIGNALS
        )

        # Two independent publication signals are enough. This is deliberately
        # stricter than relying on position alone, so ordinary prefaces/epigraphs
        # are preserved unless they also look like publication metadata.
        if signal_count < 2:
            return text

        return "\n".join(lines[first_narrative:]).lstrip()

    @classmethod
    def _remove_local_metadata(cls, text: str) -> str:
        """Remove clearly identified metadata sections/lines wherever they occur.

        Plain headings such as ``Copyright`` and ``Contents`` are recognized even
        when the converter did not emit Markdown heading markers.
        """
        lines = text.splitlines()
        out: list[str] = []
        i = 0

        def normalized_label(line: str) -> str:
            value = UNDERLINE_TAG.sub("", line).strip()
            value = re.sub(r"^#{1,6}\s*", "", value)
            value = re.sub(r"^[*_`]+|[*_`]+$", "", value).strip()
            return re.sub(r"\s+", " ", value).casefold().strip(" :")

        while i < len(lines):
            heading = cls._plain_heading_text(lines[i])
            normalized = (
                heading.casefold().strip(" :")
                if heading
                else normalized_label(lines[i])
            )

            if normalized in LOCAL_METADATA_HEADINGS:
                # Remove the metadata section locally. Resume at the next genuine
                # structural section, whether Markdown-marked or plain text.
                i += 1
                while i < len(lines):
                    next_heading = cls._plain_heading_text(lines[i])
                    next_plain = normalized_label(lines[i])
                    structural = bool(
                        next_heading
                        and normalized_label(next_heading)
                        not in LOCAL_METADATA_HEADINGS
                    )
                    if not structural and NARRATIVE_SECTION.match(next_plain):
                        structural = True
                    if structural:
                        break
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
    def _remove_named_sections(
        cls, text: str, names: Iterable[str]
    ) -> tuple[str, list[str]]:
        """Remove explicitly named sections locally, regardless of document order."""

        def section_key(value: str) -> str:
            value = UNDERLINE_TAG.sub("", str(value))
            value = re.sub(r"^#{1,6}\s*", "", value.strip())
            value = re.sub(r"[*_`]+", "", value)
            value = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
            value = re.sub(r"\bprofles\b", "profiles", value)
            value = re.sub(r"\bprofle\b", "profile", value)
            value = re.sub(
                r"^[a-z0-9 ]+\s+character profiles$", "character profiles", value
            )
            return re.sub(r"\s+", " ", value)

        targets = {section_key(name) for name in names if str(name).strip()}
        if not targets:
            return text, []

        lines = text.splitlines()
        out: list[str] = []
        removed: list[str] = []
        i = 0

        while i < len(lines):
            heading = cls._plain_heading_text(lines[i])
            if heading and section_key(heading) in targets:
                removed.append(heading)
                i += 1
                while i < len(lines):
                    next_heading = cls._plain_heading_text(lines[i])
                    if next_heading:
                        # Duplicate/profile page headers are still part of the
                        # excluded section; only stop at a different section.
                        if section_key(next_heading) in targets:
                            i += 1
                            continue
                        break
                    i += 1
                continue

            out.append(lines[i])
            i += 1

        return "\n".join(out), removed

    @classmethod
    def _remove_promotional_tail(cls, text: str) -> str:
        """Remove a next-volume promotional tail identified by strong local evidence.

        A normal use of the phrase "coming soon" in story prose is untouched.
        Removal only happens when a Volume heading is followed within a few lines
        by publisher-style "Coming soon" text, which is characteristic of ebook
        previews appended after the actual novel.
        """
        lines = text.splitlines()
        for i, line in enumerate(lines):
            heading = cls._plain_heading_text(line)
            candidate = heading or UNDERLINE_TAG.sub("", line).strip()
            candidate = re.sub(r"^#{1,6}\s*", "", candidate).strip()
            if not re.match(r"^volume\s+\d+\b", candidate, flags=re.I):
                continue

            window = "\n".join(lines[i : min(len(lines), i + 5)])
            if re.search(r"\bcoming\s+soon\b", window, flags=re.I):
                return "\n".join(lines[:i]).rstrip()

        # Some converters merge the volume title and Coming soon onto one line
        # without producing a clean heading.
        for i, line in enumerate(lines):
            if re.search(r"\bvolume\s+\d+\b", line, flags=re.I) and re.search(
                r"\bcoming\s+soon\b", line, flags=re.I
            ):
                return "\n".join(lines[:i]).rstrip()

        return text

    @staticmethod
    def _remove_bounded_glossary_footnotes(text: str) -> tuple[str, int]:
        """Remove only paragraphs explicitly shaped like numbered glossary notes."""
        paragraphs = re.split(r"(\n\s*\n+)", text)
        removed = 0
        out: list[str] = []
        for part in paragraphs:
            if not part.strip() or re.fullmatch(r"\n\s*\n+", part):
                out.append(part)
                continue
            lines = [line for line in part.splitlines() if line.strip()]
            if lines and GLOSSARY_FOOTNOTE.match(lines[0]):
                # The first line supplies a strong boundary. Continuation lines
                # remain within this paragraph and are removed with that note.
                removed += 1
                continue
            out.append(part)
        return "".join(out), removed

    @staticmethod
    def _remove_generic_publisher_tail(text: str) -> tuple[str, str | None]:
        """Remove strongly identified newsletter or numbered-TOC material at EOF."""
        lines = text.splitlines()
        if not lines:
            return text, None
        search_start = max(0, len(lines) - max(300, len(lines) // 5))

        for i in range(search_start, len(lines)):
            if SIGNUP_OR_NEWSLETTER.search(lines[i]):
                prefix = "\n".join(lines[:i]).rstrip()
                return prefix, "publisher signup/newsletter tail"

        matches = [
            i
            for i in range(search_start, len(lines))
            if TRAILING_TOC_ITEM.match(lines[i])
        ]
        # Require at least two nearby entries so an ordinary numbered sentence
        # cannot trigger truncation.
        for first, second in zip(matches, matches[1:]):
            if second - first <= 4:
                return "\n".join(lines[:first]).rstrip(), "numbered contents appendix"

        return text, None

    @staticmethod
    def _find_conservative_ocr_noise(
        text: str,
        *,
        limit: int = 100,
    ) -> list[tuple[int, str, str]]:
        """Find high-signal OCR garbage without changing document text."""
        findings: list[tuple[int, str, str]] = []
        if limit <= 0:
            return findings
        consonants = re.compile(r"[bcdfghjklmnpqrstvwxyz]{8,}", re.I)
        symbols = re.compile(r"[><~=|/@#^&\\]{4,}")
        for number, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line or re.match(r"^#{1,6}\s", line):
                continue
            alpha = sum(char.isalpha() for char in line)
            reason = None
            if len(line) >= 10 and alpha / len(line) < 0.12 and symbols.search(line):
                reason = "very low alphabetic content with dense symbol noise"
            else:
                clusters = consonants.findall(line)
                if clusters and sum(map(len, clusters)) / len(line) > 0.50:
                    reason = "line is dominated by an improbable consonant cluster"
            if reason:
                findings.append((number, line, reason))
                if len(findings) >= limit:
                    break
        return findings

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

            chapter_marker = re.match(
                r"^\[chapter\]\s*([\divxlcdm]+)(?:\s*[|:\-–—]?\s*)(.*)$",
                body,
                flags=re.I,
            )
            if chapter_marker:
                number, title = chapter_marker.groups()
                number = (
                    number.upper()
                    if re.fullmatch(r"[ivxlcdm]+", number, re.I)
                    else number
                )
                title = title.strip()
                out.append(f"# Chapter {number}" + (f": {title}" if title else ""))
                continue

            numbered = NUMBERED_HEADING.match(body)
            named = NAMED_HEADING.match(body)

            if numbered:
                kind, number, title = numbered.groups()
                kind = kind[:1].upper() + kind[1:].lower()
                number = (
                    number.upper()
                    if re.fullmatch(r"[ivxlcdm]+", number, re.I)
                    else number
                )
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
                previous_nonblank = next(
                    (x for x in reversed(deduped) if x.strip()), None
                )
                if (
                    previous_nonblank
                    and previous_nonblank.startswith("#")
                    and line.casefold() == previous_nonblank.casefold()
                ):
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
        labelish = sum(
            bool(re.match(r"^[A-Z][A-Za-z0-9 /&()'’+\-]{0,40}:?$", line))
            for line in lines
        )
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
        if (
            len(l) <= 320
            and len(r) <= 1200
            and len(l) + len(r) <= 1400
            and not re.search(r"[.!?…\"'’”)]$", l)
            and re.match(r"^[a-z]", r)
        ):
            return True
        return False

    @staticmethod
    def _split_overlong_paragraph(text: str, max_chars: int = 1800) -> list[str]:
        """Split exceptionally long reconstructed prose at sentence boundaries.

        This is a safety valve for OCR sources that contain thousands of words
        with no blank paragraph separators. Normal-sized paragraphs are left
        untouched. No text is discarded.
        """
        if len(text) <= max_chars or text.startswith("#"):
            return [text]

        sentences = re.split(r'(?<=[.!?…])\s+(?=["\'“‘(A-Z0-9])', text)
        if len(sentences) <= 1:
            # Fall back to a hard whitespace boundary rather than emitting one
            # enormous line, but never split inside a word.
            chunks: list[str] = []
            remaining = text
            while len(remaining) > max_chars:
                cut = remaining.rfind(" ", 0, max_chars)
                if cut < max_chars // 2:
                    cut = max_chars
                chunks.append(remaining[:cut].strip())
                remaining = remaining[cut:].strip()
            if remaining:
                chunks.append(remaining)
            return chunks

        chunks: list[str] = []
        current = ""
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            proposed = sentence if not current else current + " " + sentence
            if current and len(proposed) > max_chars:
                chunks.append(current)
                current = sentence
            else:
                current = proposed
        if current:
            chunks.append(current)
        return chunks

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
        bounded: list[str] = []
        for block in merged:
            if cls._is_heading(block) or cls._is_list_or_special(block):
                bounded.append(block)
            else:
                bounded.extend(cls._split_overlong_paragraph(block))
        return "\n\n".join(bounded)

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
