#!/usr/bin/env python3
"""Convert a Markdown file into an accessible, text-based PDF.

The output is a clean single-column PDF whose text is real, selectable text
(never images) laid out in natural reading order, which is exactly what
Adobe Acrobat's "Read Out Loud" feature needs to narrate a document.

Usage:
    python md_to_pdf.py                 # converts the .md file in this folder
    python md_to_pdf.py input.md        # converts a specific file
    python md_to_pdf.py input.md out.pdf

Dependencies (installed automatically if missing):
    markdown   - parses Markdown into HTML
    reportlab  - renders the text into a tagged, selectable PDF
"""

from __future__ import annotations

import argparse
import glob
import html
import os
import re
import subprocess
import sys
from html.parser import HTMLParser
from typing import List, Optional, Tuple


def _ensure_dependencies() -> None:
    """Install the required third-party packages if they are not importable."""
    required = {"markdown": "markdown", "reportlab": "reportlab"}
    missing = []
    for module_name, package_name in required.items():
        try:
            __import__(module_name)
        except ImportError:
            missing.append(package_name)

    if missing:
        print(f"Installing missing packages: {', '.join(missing)}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", *missing]
        )


class _FlowableBuilder(HTMLParser):
    """Turn the HTML produced from Markdown into ReportLab flowables.

    Only the subset of tags that Markdown emits for prose is handled:
    headings, paragraphs, emphasis, lists, block quotes and rules. Inline
    formatting is converted into ReportLab's mini-markup so the text stays
    selectable (and therefore readable by Read Out Loud).
    """

    _HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    _BLOCK_TAGS = _HEADINGS | {"p", "li", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: List[Tuple[str, str]] = []
        self._buffer: List[str] = []
        self._current_tag: Optional[str] = None
        self._list_stack: List[str] = []
        self._list_counters: List[int] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._BLOCK_TAGS:
            self._flush(tag)
            self._current_tag = tag
            if tag == "li":
                # Prefix list items with a bullet or number marker.
                if self._list_stack and self._list_stack[-1] == "ol":
                    self._list_counters[-1] += 1
                    self._buffer.append(f"{self._list_counters[-1]}.&nbsp;")
                else:
                    self._buffer.append("&bull;&nbsp;")
        elif tag in ("ul", "ol"):
            self._list_stack.append(tag)
            self._list_counters.append(0)
        elif tag in ("strong", "b"):
            self._buffer.append("<b>")
        elif tag in ("em", "i"):
            self._buffer.append("<i>")
        elif tag == "br":
            self._buffer.append("<br/>")
        elif tag == "hr":
            self._flush(None)
            self.blocks.append(("hr", ""))

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK_TAGS:
            self._flush(None)
        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
                self._list_counters.pop()
        elif tag in ("strong", "b"):
            self._buffer.append("</b>")
        elif tag in ("em", "i"):
            self._buffer.append("</i>")

    def handle_data(self, data: str) -> None:
        if self._current_tag is None:
            return
        # Escape characters that are special to ReportLab's mini-markup.
        text = html.escape(data, quote=False)
        self._buffer.append(text)

    def _flush(self, next_tag: Optional[str]) -> None:
        """Emit the buffered inline text as a finished block."""
        if self._current_tag is not None:
            content = "".join(self._buffer).strip()
            if content:
                self.blocks.append((self._current_tag, content))
        self._buffer = []
        self._current_tag = next_tag


def _build_pdf(blocks: List[Tuple[str, str]], output_path: str, title: str) -> None:
    """Render parsed blocks into a selectable, reading-order PDF."""
    from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        HRFlowable,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=12,
        leading=17,
        alignment=TA_JUSTIFY,
        spaceAfter=8,
    )
    quote_style = ParagraphStyle(
        "Quote",
        parent=body_style,
        leftIndent=20,
        textColor="#333333",
        alignment=TA_LEFT,
    )
    heading_styles = {
        "h1": ParagraphStyle("H1", parent=styles["Heading1"], fontName="Times-Bold",
                             fontSize=22, leading=26, spaceBefore=18, spaceAfter=12),
        "h2": ParagraphStyle("H2", parent=styles["Heading2"], fontName="Times-Bold",
                             fontSize=18, leading=22, spaceBefore=14, spaceAfter=10),
        "h3": ParagraphStyle("H3", parent=styles["Heading3"], fontName="Times-Bold",
                             fontSize=15, leading=19, spaceBefore=12, spaceAfter=8),
    }
    for level in ("h4", "h5", "h6"):
        heading_styles[level] = heading_styles["h3"]

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=2.2 * cm,
        rightMargin=2.2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=title,
        author="md_to_pdf",
        # A defined language lets Read Out Loud pick the right voice.
        lang="en-US",
    )

    story = []
    for tag, content in blocks:
        if tag == "hr":
            story.append(Spacer(1, 6))
            story.append(HRFlowable(width="100%", thickness=0.5, color="#999999"))
            story.append(Spacer(1, 6))
        elif tag in heading_styles:
            # Start each top-level section (chapter) on a fresh page.
            if tag == "h1" and story:
                story.append(PageBreak())
            story.append(Paragraph(content, heading_styles[tag]))
        elif tag == "blockquote":
            story.append(Paragraph(content, quote_style))
        elif tag == "li":
            story.append(Paragraph(content, body_style))
        else:
            story.append(Paragraph(content, body_style))

    doc.build(story)


# Decorative/ornamental characters (geometric shapes, dingbats, box-drawing,
# and stars) that act as visual dividers but add nothing when read aloud.
_ORNAMENT_CHARS = (
    "\u25A0-\u25FF"   # Geometric Shapes: ■ □ ◆ ◇ ● ○ ◢ ◣ ▲ ▼ etc.
    "\u2600-\u26FF"   # Miscellaneous Symbols: stars, suns, etc.
    "\u2700-\u27BF"   # Dingbats: ✱ ✲ ✦ ✧ ❖ etc.
    "\u2500-\u257F"   # Box Drawing: ─ │ ┼ etc.
    "\u2580-\u259F"   # Block Elements: ▀ ▄ █ etc.
    "\u2b00-\u2bff"   # Misc Symbols and Arrows: ⬛ ⬥ ★ variants etc.
)
_ORNAMENT_RE = re.compile(f"[{_ORNAMENT_CHARS}]")

# Lines that name a structural division of the book. Detected so they can be
# promoted to real PDF headings (they arrive as plain text in the source).
_HEADING_RE = re.compile(
    r"^(?:"
    r"Prologue|Epilogue|Afterword|Foreword|Preface|Introduction"
    r"|Table of Contents|Color Illustrations|Copyright|Newsletter"
    r"|About J-Novel Club|J-Novel Club Membership"
    r"|(?:Chapter|Intermission|Side Story|Interlude|Part|Volume|Act)\b.*"
    r")\s*$",
    re.IGNORECASE,
)

# A trailing OCR-noise token: whitespace-delimited, at least 5 chars, and a
# mix of letters and digits (e.g. "5s69wer" left over from scanning artwork).
_OCR_JUNK_RE = re.compile(r"\s+(?=\S*[A-Za-z])(?=\S*[0-9])[A-Za-z0-9]{5,}$")

# A markdown code-fence marker line (``` optionally followed by a language).
_FENCE_RE = re.compile(r"^\s*`{3,}\w*\s*$")

# Leading markdown heading hashes on a line (e.g. "## ", "# ").
_LEADING_HASH_RE = re.compile(r"^#{1,6}\s*")

# Terminal punctuation that marks the end of a wrapped paragraph.
_SENTENCE_END_RE = re.compile(r"[.!?…”\"')]\s*$")


def _clean_markdown(text: str) -> str:
    """Clean ebook-export noise and reflow the text into tidy paragraphs.

    Handles the artifacts produced by ebook-to-Markdown conversion:
      * Removes spurious ``` code-fence markers that were wrapped around
        page-break-split prose, so the sentences rejoin into flowing text.
      * Strips decorative scene-break glyphs (e.g. ◆◇◆◇◆) that Read Out Loud
        would otherwise vocalise.
      * Strips stray leading markdown heading hashes from body lines.
      * Promotes structural lines (Prologue, Chapter N, Epilogue, Afterword,
        Intermission, Side Story, ...) to real ``#`` headings.
      * Removes trailing OCR-noise tokens left over from scanned artwork.
      * Reflows hard-wrapped lines into paragraphs, breaking only where a
        line ends with sentence-terminating punctuation.

    Returns Markdown with paragraphs separated by blank lines.
    """
    out: List[str] = []
    paragraph: List[str] = []
    # Holds a stray leading word that a heading line accidentally swallowed,
    # to be prepended to the next paragraph.
    pending_prefix = ""

    def flush() -> None:
        if paragraph:
            out.append(" ".join(paragraph).strip())
            out.append("")  # blank line => paragraph separator
            paragraph.clear()

    for raw in text.splitlines():
        # Drop code-fence marker lines entirely (keep any text they wrapped).
        if _FENCE_RE.match(raw):
            continue

        # Remove ornamental glyphs, then normalise whitespace.
        line = _ORNAMENT_RE.sub("", raw).strip()

        # A line that was only ornaments/whitespace is a paragraph break.
        if not line:
            flush()
            continue

        # Normalise away any leading markdown heading hashes before testing.
        line = _LEADING_HASH_RE.sub("", line).strip()
        if not line:
            flush()
            continue

        # Promote structural lines to their own heading block.
        if _HEADING_RE.match(line):
            flush()
            # A chapter title that ends with a lone capital letter (e.g. "...
            # New Moon I") has swallowed the first word of the next sentence;
            # move that letter down to the following paragraph.
            tail = re.search(r"\s+([IA])$", line)
            if tail:
                pending_prefix = tail.group(1) + " "
                line = line[: tail.start()].rstrip()
            out.append(f"# {line}")
            out.append("")
            continue

        # Drop trailing OCR-noise tokens (repeat in case several are stacked).
        prev = None
        while prev != line:
            prev = line
            line = _OCR_JUNK_RE.sub("", line).rstrip()

        if not line:
            flush()
            continue

        # Reattach any word a preceding heading swallowed.
        if pending_prefix and not paragraph:
            line = pending_prefix + line
            pending_prefix = ""

        paragraph.append(line)

        # End the paragraph when the line finishes a sentence: this is the
        # signal that distinguishes a real break from a soft word-wrap.
        if _SENTENCE_END_RE.search(line):
            flush()

    flush()
    return "\n".join(out)


def convert(md_path: str, pdf_path: str) -> None:
    """Convert a Markdown file at md_path into a PDF at pdf_path."""
    import markdown

    with open(md_path, "r", encoding="utf-8") as handle:
        md_text = handle.read()

    md_text = _clean_markdown(md_text)

    html_text = markdown.markdown(
        md_text,
        extensions=["extra", "sane_lists"],
    )

    parser = _FlowableBuilder()
    parser.feed(html_text)
    parser.close()

    if not parser.blocks:
        raise ValueError("No readable content was found in the Markdown file.")

    title = os.path.splitext(os.path.basename(md_path))[0]
    _build_pdf(parser.blocks, pdf_path, title)



def _default_input() -> Optional[str]:
    """Return the single .md file in the script folder, if there is exactly one."""
    here = os.path.dirname(os.path.abspath(__file__))
    matches = sorted(glob.glob(os.path.join(here, "*.md")))
    return matches[0] if len(matches) == 1 else None


def _list_markdown_files() -> List[str]:
    """Return all .md files in the script folder, sorted by name."""
    here = os.path.dirname(os.path.abspath(__file__))
    return sorted(glob.glob(os.path.join(here, "*.md")))


def _crisp_stem_from_markdown(md_path: str) -> str:
    """Return a cleaner, shorter filename stem derived from a markdown path.

    Removes bracketed source tags like "[Kobo_LNWNCentral]", normalizes
    separators, and strips filename-unsafe characters.
    """
    stem = os.path.splitext(os.path.basename(md_path))[0]

    # Remove source metadata tags often embedded in ebook filenames.
    stem = re.sub(r"\[[^\]]*\]", "", stem)
    stem = re.sub(r"\([^\)]*\)", "", stem)

    # Normalize separators and whitespace.
    stem = stem.replace("_", " ")
    stem = re.sub(r"\s*-\s*", " - ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" -_.")

    # Remove filename-unsafe characters on Windows.
    stem = re.sub(r"[<>:\"/\\|?*]", "", stem).strip(" .")

    # Prefer a single canonical title + first volume marker when the source
    # filename repeats the title or embeds duplicate volume metadata.
    volume_match = re.search(r"\b(?:volume|vol\.?|book)\s*0*(\d+)\b", stem, re.IGNORECASE)
    if volume_match:
        title_part = stem[: volume_match.start()].strip(" -_.")
        volume_number = int(volume_match.group(1))
        if title_part:
            stem = f"{title_part} Volume {volume_number}"

    # Collapse any repeated adjacent words left over after cleanup.
    tokens = stem.split()
    deduped_tokens: List[str] = []
    for token in tokens:
        if not deduped_tokens or deduped_tokens[-1].lower() != token.lower():
            deduped_tokens.append(token)
    stem = " ".join(deduped_tokens).strip()

    return stem or "converted"


def _default_pdf_output_path(md_path: str, used_paths: Optional[set[str]] = None) -> str:
    """Return a crisp default PDF path for a markdown file.

    If multiple files resolve to the same name in a single --all run, append a
    numeric suffix to keep output names unique.
    """
    directory = os.path.dirname(os.path.abspath(md_path))
    base = _crisp_stem_from_markdown(md_path)
    candidate = os.path.join(directory, f"{base}.pdf")

    if not used_paths:
        return candidate

    if candidate not in used_paths:
        return candidate

    index = 2
    while True:
        alt = os.path.join(directory, f"{base} ({index}).pdf")
        if alt not in used_paths:
            return alt
        index += 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert a Markdown file into an accessible PDF for "
        "Adobe Acrobat's Read Out Loud feature."
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=None,
        help="Path to the Markdown (.md) file (defaults to the .md file next to this script).",
    )
    parser.add_argument(
        "output",
        nargs="?",
        default=None,
        help="Path for the output PDF (defaults to the input name with a .pdf extension).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Convert all .md files in the script folder into PDFs.",
    )
    args = parser.parse_args()

    if args.all:
        if args.input or args.output:
            parser.error("--all cannot be used with positional input/output arguments.")

        md_files = _list_markdown_files()
        if not md_files:
            print("Error: no Markdown (.md) files were found in this folder.")
            return 1

        _ensure_dependencies()

        failed = 0
        used_paths: set[str] = set()
        for md_path in md_files:
            pdf_path = _default_pdf_output_path(md_path, used_paths)
            used_paths.add(pdf_path)
            print(f"Converting: {md_path}")
            try:
                convert(md_path, pdf_path)
                print(f"PDF written: {pdf_path}")
            except Exception as exc:
                failed += 1
                print(f"Failed: {md_path}")
                print(f"Reason: {exc}")

        if failed:
            print(f"Done with errors: {len(md_files) - failed} succeeded, {failed} failed.")
            return 1

        print(f"Done: converted {len(md_files)} file(s).")
        return 0

    md_path = args.input or _default_input()
    if not md_path:
        md_files = _list_markdown_files()
        if not md_files:
            parser.error("No Markdown file specified and no .md files were found in this folder.")

        print("Multiple Markdown files found. Please choose one:")
        for idx, path in enumerate(md_files, start=1):
            print(f"  {idx}. {os.path.basename(path)}")

        print("\nRun one of these commands:")
        script_name = os.path.basename(__file__)
        for idx, path in enumerate(md_files, start=1):
            base = os.path.basename(path)
            print(f"  {idx}) python .\\{script_name} \"{base}\"")
        print(f"\nOr convert all at once:\n  python .\\{script_name} --all")
        return 1

    if args.input and not os.path.isabs(md_path) and not os.path.isfile(md_path):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        alt = os.path.join(script_dir, md_path)
        if os.path.isfile(alt):
            md_path = alt

    md_path = os.path.abspath(md_path)
    if not os.path.isfile(md_path):
        print(f"Error: '{md_path}' is not a file.")
        return 1

    pdf_path = os.path.abspath(args.output) if args.output else _default_pdf_output_path(md_path)

    _ensure_dependencies()

    print(f"Converting: {md_path}")
    convert(md_path, pdf_path)
    print(f"PDF written: {pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
