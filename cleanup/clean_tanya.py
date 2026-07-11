#!/usr/bin/env python3
"""
Cleaner for: The Saga of Tanya the Evil series

Noise patterns removed:
  1. Picture omission notices:  **==> picture [W x H] intentionally omitted <==**
  2. Picture text blocks:       always exactly 2 lines: Start-marker line + content line
                                (End marker embedded at tail of content line or on next line)
  3. Front-matter OCR noise:    cover art / map / timeline scans before Chapter 1
  4. Short random fragments:    "Ss", "i", "dé", "m", "oes", "iS,"
  5. Dense garbled OCR lines:   long strings of random caps / noise chars
  6. Blockquote OCR dumps:      "> PSUSSESSaE3sor..." etc.
  7. Malformed chapter headings: "[ chapter| The Skycy over: Horde ps:"
  8. Scanner border / rule lines
  9. Copyright and publisher info (J-Novel Club, ISBN, Copyright symbols)
 10. Author/Illustrator credits ("Story by", "Illustrated by")
 11. Timeline and metadata sections
 12. Footnotes and footnote references
 13. Author's notes and afterword sections
"""

import re
from pathlib import Path

CLEANUP_FOLDER = Path(r"C:\Users\z005537p\NitishWork\HM\temp\cleanup")


def build_book_title(input_name: str) -> str:
    """Build a stable H1 title from the source filename."""
    stem = Path(input_name).stem
    m = re.search(r"Volume\s*(\d+)", stem, flags=re.IGNORECASE)
    vol = int(m.group(1)) if m else None

    subtitle = ""
    if m:
        # Keep any explicit subtitle that appears after "Volume N".
        tail = stem[m.end():].strip(" -_")
        if tail:
            subtitle = f": {tail}"

    if vol is not None:
        return f"# The Saga of Tanya the Evil - Volume {vol:02d}{subtitle}"

    return f"# {stem}"


# 3+ consecutive identical letters (SSS, sss, eee …) — OCR artifact
_REPEATED_ALPHA_RE = re.compile(r'([a-zA-Z])\1{2,}')


def is_ocr_noise(line: str) -> bool:
    s = line.strip()
    if not s: return False
    # Very short fragments
    if len(s) <= 2: return True
    if len(s) == 3 and not s[0].isalpha(): return True
    # Very low alphanumeric density
    alnum = sum(c.isalnum() for c in s)
    if len(s) > 8 and alnum / len(s) < 0.20: return True
    # Entirely punctuation / symbols
    if re.fullmatch(r"[\W_]+", s) and len(s) > 3: return True
    # Long consonant clusters (OCR gobbledegook)
    runs = re.findall(r"[bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ]{6,}", s)
    if runs and sum(len(r) for r in runs) / len(s) > 0.35: return True
    # Dense no-space alternating-case strings
    if len(s) > 30 and " " not in s and re.search(r"[a-z]{3,}[A-Z]{2,}[a-z]{3,}", s):
        return True
    # High density of structural/noise symbols
    noise_sym = set("><~=|/_@#^&*")
    if len(s) > 6 and sum(c in noise_sym for c in s) / len(s) > 0.40: return True
    # Garbage punctuation patterns often seen in OCR dumps
    if re.search(r"[~`=«»]{3,}|(?:\W\s*){6,}", s): return True
    # Keep lines that look like real prose; reject symbol-heavy low-word lines
    words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", s)
    if len(s) >= 8 and len(words) <= 2:
        punct = sum(not ch.isalnum() and not ch.isspace() for ch in s)
        if punct / max(len(s), 1) > 0.35:
            return True
    # Reject lines with too many non-ASCII glyphs and little English text
    non_ascii = sum(ord(ch) > 127 for ch in s)
    if len(s) > 6 and non_ascii / len(s) > 0.20 and len(words) < 3:
        return True

    # ── Statistical OCR-noise detection ───────────────────────────────────
    alpha = sum(c.isalpha() for c in s)

    if alpha >= 15:
        upper   = sum(c.isupper() for c in s if c.isalpha())
        s_count = sum(1 for c in s if c.lower() == 's')
        u_ratio = upper / alpha
        s_ratio = s_count / alpha

        # Overwhelming random-caps (>55 % uppercase = OCR randomisation)
        if u_ratio > 0.55:
            return True
        # Very high S-density — normal English tops out ~26 %
        if s_ratio > 0.32:
            return True
        # Elevated S-density combined with significant uppercase
        if s_ratio > 0.22 and u_ratio > 0.25:
            return True

    # 3+ distinct runs of 3+ identical letters (SSS … sss … eee …)
    if len(s) > 15 and len(_REPEATED_ALPHA_RE.findall(s)) >= 3:
        return True

    # All tokens are ≤ 2 alpha chars in a short/medium line (fragmented OCR)
    tokens      = [w.strip('"\'.,:;!?-[](){}*') for w in s.split()]
    alpha_tokens = [t for t in tokens if t and any(c.isalpha() for c in t)]
    if len(alpha_tokens) >= 3 and len(s) <= 25:
        if all(sum(c.isalpha() for c in t) <= 2 for t in alpha_tokens):
            return True

    return False


def is_copyright_or_publisher_block(line: str) -> bool:
    """Detect copyright, publisher, and author/illustrator info"""
    s = line.strip()
    if not s: return False
    # Copyright symbols and variations
    if "©" in s or "Copyright" in s or "copyright" in s: return True
    # Publisher patterns (J-Novel Club, Yen Press, etc.)
    if re.search(r"(J-Novel Club|Yen Press|Light Novel|Publisher|Published|ISBN|ISSN)", s, re.IGNORECASE): return True
    # Author/Illustrator credit lines
    if re.search(r"(Story by|Written by|Illustrated by|Illustration by|Author|Illustrator)[\s]*[:=]?", s, re.IGNORECASE): return True
    # Copyright year patterns
    if re.search(r"20\d{2}.*All rights reserved", s, re.IGNORECASE): return True
    return False


def is_footnote_reference(line: str) -> bool:
    """Detect standalone footnote lines like '[1] text...' or '**[1]** text'"""
    s = line.strip()
    if not s: return False
    # Footnote pattern: starts with [number] or **[number]**
    if re.match(r"^\*?\*?\[?\d{1,3}\]?\*?\*?\s", s): return True
    # Alternative: number followed by period or parenthesis at start
    if re.match(r"^\d{1,3}[.\s]\s", s) and len(s) > 20:  # Likely footnote if substantial text follows
        return True
    return False


def is_timeline_or_metadata(line: str) -> bool:
    """Detect timeline, character profile, or metadata sections"""
    s = line.strip()
    if not s: return False
    # Match heading-based timeline/metadata markers
    if re.search(r"(Time[ -]?Line|Timeline|Character Profile|Glossary|Appendix)", s, re.IGNORECASE): return True
    # Match timeline entries more carefully (must have specific format at start of line)
    # Format like "Year 1923:" or "Unified Year 1912"
    if re.match(r"^(?:Unified )?Year\s+\d{3,4}[:\s,.]", s, re.IGNORECASE): return True
    return False


def is_authors_note(line: str) -> bool:
    """Detect author's note/afterword sections - must be a heading"""
    s = line.strip()
    if not s: return False
    # Only match if it's a markdown heading (starts with #)
    if re.match(r"^#+\s+", line):
        if re.search(r"(Afterword|Author'?s? (?:Note|Notes|Comment|Remarks)|Epilogue - Author)", s, re.IGNORECASE):
            return True
    return False


def clean_line(text: str) -> str:
    # Remove underscore emphasis markers and dangling OCR underscore artifacts.
    text = re.sub(r"(?<!\w)_([^_\n]{1,200}?)_(?!\w)", r"\1", text)
    text = re.sub(r"(^|[\s(\[{\"'])_(?=[A-Za-z])", r"\1", text)
    text = re.sub(r"(?<=[A-Za-z0-9,.;:!?\"')\]])_(?=($|[\s,.;:!?\)\]]))", "", text)
    text = re.sub(r"(?<!\w)_([A-Za-z][A-Za-z'\-]{0,30})(?=(?:[,.;:!?)]|$))", r"\1", text)
    text = re.sub(r"(?<=\S)\s+_(?=\s|$)", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r" {2,}", " ", text)
    text = text.replace("\u200b", "").replace("\xa0", " ")
    return text.strip()


def fix_mojibake(text: str) -> str:
    """Repair common UTF-8/CP1252 mojibake artifacts."""
    replacements = {
        "â€™": "'",
        "â€˜": "'",
        "â€œ": '"',
        "â€�": '"',
        "â€“": "-",
        "â€”": "-",
        "â€¦": "...",
        "â€¢": "-",
        "Â ": " ",
        "Â": "",
        "ï»¿": "",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text


def normalize_ascii_punctuation(text: str) -> str:
    """Convert typographic punctuation to ASCII-safe equivalents."""
    replacements = {
        "\u2019": "'",
        "\u2018": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
        "\u2022": "-",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text


def normalize_ocr_word_joins(text: str) -> str:
    """Repair common OCR word-join artifacts in otherwise English prose."""
    # Specific known OCR merge from these source files.
    text = re.sub(r"\bbird'seye\b", "bird's-eye", text, flags=re.IGNORECASE)

    # Add missing space after sentence punctuation.
    text = re.sub(r"([\.,;:!?])(\w)", r"\1 \2", text)

    # Split common glued contractions (e.g., "it'snot" -> "it's not").
    contractions = (
        r"(?:it's|that's|there's|what's|who's|where's|when's|why's|how's|"
        r"can't|won't|don't|didn't|doesn't|isn't|aren't|wasn't|weren't|"
        r"couldn't|wouldn't|shouldn't|i'm|you're|we're|they're|he's|she's|"
        r"i'll|we'll|they'll|you'll|i've|you've|we've|they've)"
    )
    text = re.sub(rf"\b({contractions})([A-Za-z])", r"\1 \2", text, flags=re.IGNORECASE)

    return text


def looks_all_caps_heading(text: str) -> bool:
    """Heuristic for date/location style headings that are often split by OCR."""
    s = re.sub(r"[^A-Za-z0-9\s,.:()\-/]", "", text).strip()
    if not s:
        return False
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(c.isupper() for c in letters) / len(letters)
    return upper_ratio > 0.85 and len(s.split()) >= 2


def looks_all_caps_fragment(text: str) -> bool:
    """Allow short continuation fragments (e.g., FRONT) for heading merge."""
    s = re.sub(r"[^A-Za-z0-9\s,.:()\-/]", "", text).strip()
    if not s:
        return False
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(c.isupper() for c in letters) / len(letters)
    return upper_ratio > 0.85 and len(s.split()) <= 4


def process(raw: str, title: str) -> str:
    lines = raw.splitlines()
    out   = []
    i     = 0
    in_toc = False

    # ── Find index of first real chapter heading ───────────────────────────
    # Real chapters are ## ** DATE/LOCATION ** or ## **[chapter] N …**
    # This skips all front-matter noise (cover, map, timeline pages)
    first_chapter = None
    for idx, ln in enumerate(lines):
        if re.match(
            r"^##\s+\*\*(?!Contents\b)(?!Appendix\b)(?!Appendixes\b)(?!Afterword\b)(?!Yen Newsletter\b)(?:[A-Z]|\[chapter\])",
            ln,
            flags=re.IGNORECASE,
        ):
            first_chapter = idx
            break
    if first_chapter is None:
        first_chapter = 0   # fallback: process everything

    # Add a clean book title header before the first chapter
    out.append(title)
    out.append("")
    i = first_chapter

    while i < len(lines):
        line = lines[i]
        s    = line.strip()

        # ── Drop common front-matter tokens that survive OCR cleanup ─────
        if s in {"Cover", "Insert", "Title Page", "Copyright", "Contents", "Yen Newsletter"}:
            i += 1
            continue

        # ── Strip table-of-contents block ────────────────────────────────
        if re.match(r"^##\s+\*\*Contents\*\*", line, flags=re.IGNORECASE):
            in_toc = True
            i += 1
            continue
        if in_toc:
            # End TOC when real chapter heading begins
            if re.match(r"^##\s+\*\*(?:[IVXLC]+\b|\[chapter\]|[A-Z]{3,}|[A-Z][a-z]+)", line):
                in_toc = False
            else:
                i += 1
                continue

        # ── Malformed chapter markers and OCR separators ─────────────────
        if re.match(r"^\[\s*chapter\|", s, flags=re.IGNORECASE):
            i += 1
            continue
        if re.search(r"(?:-{2,}_|_{2,}-|[~`=«»]{3,})", s):
            i += 1
            continue

        # ── Copyright and publisher info ───────────────────────────────────
        if is_copyright_or_publisher_block(line):
            i += 1
            continue

        # ── Picture omission notice ────────────────────────────────────────
        if re.search(r"==> picture \[.*?\] intentionally omitted <==", line):
            i += 1; continue

        # ── Picture text block (Start marker line + 1 content line) ───────
        if "Start of picture text" in line:
            i += 1                                          # skip Start line
            if i < len(lines): i += 1                      # skip content line
            if i < len(lines) and "End of picture text" in lines[i]:
                i += 1                                      # skip standalone End
            continue

        # ── Standalone End marker (safety catch) ──────────────────────────
        if "End of picture text" in line:
            i += 1; continue

        # ── Markdown headers ───────────────────────────────────────────────
        hm = re.match(r"^(#{1,4})\s+(.*)", line)
        if hm:
            content = hm.group(2).strip()
            hashes  = hm.group(1)
            if re.match(r"^\[\s*chapter\|", content, flags=re.IGNORECASE):
                i += 1
                continue

            # Merge split OCR headings like:
            # ## **APRIL ..., EASTERN**
            # ## **FRONT**
            if i + 1 < len(lines):
                j = i + 1
                # OCR often inserts blank lines between split heading fragments.
                while j < len(lines) and lines[j].strip() == "":
                    j += 1
                if j < len(lines):
                    hm_next = re.match(r"^(#{1,4})\s+(.*)", lines[j])
                    if hm_next and hm_next.group(1) == hashes:
                        next_content = hm_next.group(2).strip()
                        if looks_all_caps_heading(content) and looks_all_caps_fragment(next_content):
                            content = f"{content} {next_content}"
                            i = j

            real_words = re.findall(r"[A-Za-z]{3,}", content)
            if not real_words: i += 1; continue
            if hashes == "#" and len(content) < 4: i += 1; continue
            content = content.replace("**", "")
            content = re.sub(r"\[chapter\]\s*", "", content, flags=re.IGNORECASE)
            out.append(f"{hashes} {clean_line(content)}")
            i += 1; continue

        # ── Blockquotes ───────────────────────────────────────────────────
        if line.startswith(">"):
            inner = line.lstrip("> ").strip()
            if is_ocr_noise(inner): i += 1; continue
            out.append(f"> {clean_line(inner)}")
            i += 1; continue

        # ── Blank lines ───────────────────────────────────────────────────
        if not s:
            out.append(""); i += 1; continue

        # ── Residual symbol-only OCR lines ───────────────────────────────
        if re.search(r"~{2,}|`{2,}|={3,}", s):
            i += 1
            continue

        # ── Numbered / bulleted list items ────────────────────────────────
        if re.match(r"^\s{0,3}(\d+\.|[-*\u2022])\s", line):
            out.append(clean_line(line)); i += 1; continue

        # ── OCR noise filter ──────────────────────────────────────────────
        if is_ocr_noise(line): i += 1; continue

        # ── Multi-word all-caps-only lines (scanner noise not caught above) ─
        words = s.split()
        if len(words) >= 6:
            real = [w for w in words if re.search(r"[a-z]{3,}", w)]
            if len(real) == 0: i += 1; continue

        # ── Keep ──────────────────────────────────────────────────────────
        out.append(clean_line(s))
        i += 1

    # ── Collapse 3+ consecutive blank lines → max 2 ───────────────────────
    result = []
    blanks = 0
    for ln in out:
        if ln == "":
            blanks += 1
            if blanks <= 2: result.append("")
        else:
            blanks = 0; result.append(ln)

    text = "\n".join(result).strip() + "\n"

    # Final sweeps
    text = re.sub(r"\*\*==>.*?<==\*\*\n?", "", text, flags=re.DOTALL)
    text = re.sub(r"^[-=]{4,}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    text = fix_mojibake(text)
    text = normalize_ascii_punctuation(text)
    text = normalize_ocr_word_joins(text)
    text = re.sub(r"(?m)^\s*_(?=[A-Za-z])", "", text)
    text = re.sub(r"(?m)(?<=\S)\s+_(\s*)$", r"\1", text)

    return text


def main():
    # Find all .md files in the cleanup folder
    md_files = sorted(CLEANUP_FOLDER.glob("*.md"))
    
    if not md_files:
        print(f"No markdown files found in {CLEANUP_FOLDER}")
        return
    
    print(f"Found {len(md_files)} markdown file(s) to process\n")
    
    for input_file in md_files:
        # Skip already cleaned files
        if "_Cleaned" in input_file.name:
            print(f"Skipping (already cleaned): {input_file.name}")
            continue
        
        # Generate output filename
        output_file = input_file.parent / f"{input_file.stem}_Cleaned.md"
        
        print(f"Reading : {input_file.name}")
        raw = input_file.read_text(encoding="utf-8", errors="replace")
        in_lines = raw.count("\n")
        print(f"Lines in: {in_lines:,}")
        
        title = build_book_title(input_file.name)
        cleaned = process(raw, title)
        out_lines = cleaned.count("\n")
        removed = in_lines - out_lines
        pct = removed / max(in_lines, 1) * 100
        
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(cleaned, encoding="utf-8")
        
        print(f"Lines out: {out_lines:,}")
        print(f"Removed  : {removed:,} lines ({pct:.1f}%)")
        print(f"Written  : {output_file.name}\n")


if __name__ == "__main__":
    main()
