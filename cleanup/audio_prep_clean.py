#!/usr/bin/env python3
"""
audio_prep_clean.py  —  v2
--------------------------
Batch-cleans markdown volumes for Edge TTS audio conversion.
Run once; produces one *_AudioPrep.md per input volume.

What is REMOVED:
  • Footnote / glossary blocks (bare "1 **term**" or blockquote "> 1 **term**")
  • "Sign Up" / Yen Press newsletter / publisher promo lines
  • Table-of-Contents appendices (Vol 14-style numbered lists at end)
  • Back-cover OCR blurb lines after the Carlo Zen afterword sign-off
  • OCR noise lines (zero-alpha, consonant-cluster garbage, symbol soup)
  • Orphaned sentence fragments that would cause NoAudioReceived in Edge TTS

What is KEPT:
  • All narrative content
  • All chapter / section headings (read aloud as chapter announcements)
  • Afterword (great audio content)
  • Short legitimate dialogue: "Huh?", "Sir?", "Yes.", "But...", etc.

Output: /mnt/user-data/outputs/*_AudioPrep.md
"""

import re
import sys
from pathlib import Path

INPUT_DIR  = Path(r"C:\Users\z005537p\NitishWork\HM\temp\cleanup\old")
OUTPUT_DIR = Path(r"C:\Users\z005537p\NitishWork\HM\temp\cleanup\cleaned_audio_prep")
INPUT_FALLBACK_DIR = INPUT_DIR / "new"

# ── Compiled patterns ──────────────────────────────────────────────────────
OCR_CLUSTER_RE      = re.compile(r"[bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ]{6,}")
NOISE_SYM_RE        = re.compile(r"[><~=|/_@#^&*\\]{3,}")
REPEATED_ALPHA_RE   = re.compile(r'([a-zA-Z])\1{2,}')   # 3+ identical letters in a row
SENTENCE_END_RE     = re.compile(r'[.!?…;"\')}\]]\s*$')
HEADING_RE          = re.compile(r"^#{1,6}\s")
FOOTNOTE_BARE_RE    = re.compile(r"^\d+\s+\*\*")          # "1 **term**"
FOOTNOTE_QUOTE_RE   = re.compile(r"^>\s*\d+\s+\*\*")      # "> 1 **term**"
CARLO_ZEN_RE        = re.compile(r"carlo\s+zen", re.IGNORECASE)

SIGNUP_RE = re.compile(
    r"sign\s*up|yenpress\.com|www\.\s*yenpress"
    r"|booklink|https?://yenpress|newsletter\s*sign",
    re.IGNORECASE,
)
TOC_ITEM_RE = re.compile(
    r"^\d+\.\s+(Cover|Insert|Title\s+Page|Chapter|Afterword|Appendix|Yen\s+Newsletter)",
    re.IGNORECASE,
)
BACK_COVER_RE = re.compile(
    r"=SAGA|BVICATIO|BvICATIO|ViSSIONGTAR|Magicalia|OMNIA\s+PARATUS"
    r"|SAGAoF|SAGA.*EVIL.*story|ie Z \d|WViSSION",
    re.IGNORECASE,
)

# Italic noise: orphaned opener (_a, _to, _really — no closing _)
ITALIC_ORPHAN_RE = re.compile(r'^_[a-zA-Z]{1,8}[,.\s!?]*$')
# Whole-line lone italic: _really_, _word_ (standalone, not mid-sentence)
LONE_ITALIC_RE   = re.compile(r'^_[^_\n]{1,40}_[.!?,\'";\s]*$')


# ── OCR noise detection ────────────────────────────────────────────────────

def is_ocr_noise(line: str) -> bool:
    s = line.strip()
    if not s:
        return False

    alpha = sum(c.isalpha() for c in s)

    # Zero alpha (pure symbols / numbers)
    if alpha == 0 and len(s) > 1:
        return True
    # Single non-alpha char
    if len(s) == 1 and not s.isalpha():
        return True
    # Dominant OCR consonant clusters
    clusters = OCR_CLUSTER_RE.findall(s)
    if clusters and sum(len(c) for c in clusters) / len(s) > 0.40:
        return True
    # Very low alphanumeric ratio
    if len(s) > 8 and alpha / len(s) < 0.18:
        return True
    # Dense noise-symbol runs
    if NOISE_SYM_RE.search(s):
        return True

    # ── Statistical OCR-noise detection ───────────────────────────────────
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
    if len(s) > 15 and len(REPEATED_ALPHA_RE.findall(s)) >= 3:
        return True

    # All tokens are ≤ 2 alpha chars in a short/medium line (fragmented OCR)
    _tokens      = [w.strip('"\'.,:;!?-[](){}*') for w in s.split()]
    _alpha_tokens = [t for t in _tokens if t and any(c.isalpha() for c in t)]
    if len(_alpha_tokens) >= 3 and len(s) <= 25:
        if all(sum(c.isalpha() for c in t) <= 2 for t in _alpha_tokens):
            return True

    # Short multi-token junk: "ples i", "a ae", ". \"ie", "a ;"
    if len(s) <= 8 and alpha >= 1:
        words = s.split()
        if (len(words) >= 2
                and all(len(w.strip('"\'.,:;!?-')) <= 3 for w in words)):
            return True
    return False


def is_legitimate_short(s: str) -> bool:
    """Short lines (<=12 chars) that are real dialogue / prose fragments."""
    s = s.strip()
    if not s:
        return False
    # Single quoted word with punctuation: "Huh?", "Sir?", "Yes.", '"Oh?"'
    if re.match(r'^["\']?[A-Za-z]{1,10}[.!?…"\']*["\']?$', s):
        return True
    # "But...", "And...", "My..."
    if re.match(r'^[A-Za-z]{1,8}\.{2,}$', s):
        return True
    # Short word + punct: "So.", "Why?", "How?"
    if re.match(r'^[A-Za-z]{2,8}[.!?]$', s):
        return True
    # Dialogue closer: 'duty?"', 'front."'
    if re.match(r'^[A-Za-z]{2,10}[.!?]["\']$', s):
        return True
    # Two-word short: '"Yes, sir."', 'I nod.'
    if re.match(r'^["\']?[A-Za-z]{1,6}[\s,][A-Za-z]{1,6}[.!?]["\']?$', s):
        return True
    return False


def is_italic_noise(s: str) -> bool:
    """Detect markdown italic noise fragments: _a, _to, _really_, etc."""
    s = s.strip()
    if not s or HEADING_RE.match(s):
        return False
    # Orphaned italic opener with no closing underscore: _a, _to, _really
    if ITALIC_ORPHAN_RE.match(s) and s.count('_') == 1:
        return True
    # Whole-line italic single word/short phrase: _really_, _word_
    if LONE_ITALIC_RE.match(s):
        return True
    return False


# ── End-of-book cutoff ─────────────────────────────────────────────────────

def find_endbook_cutoff(lines: list) -> int:
    """
    Return index where end-matter begins (footnotes, signup, TOC, back-cover
    blurb after afterword).  Everything from this index onward is removed.
    """
    n = len(lines)

    # Locate the Carlo Zen afterword sign-off in last 20% of file
    carlo_idx = None
    search_start = max(0, n - max(800, n // 5))
    for i in range(search_start, n):
        s = lines[i].strip()
        if CARLO_ZEN_RE.search(s) and re.search(r'\d{4}', s):
            carlo_idx = i

    cutoff = n

    # Scan last 800 lines for end-matter markers
    for i in range(max(0, n - 800), n):
        s = lines[i].strip()

        # Bare footnote block (most volumes)
        if FOOTNOTE_BARE_RE.match(s):
            cutoff = min(cutoff, i)
            break

        # Blockquote footnote block (Vol 05, 07, 08 style)
        if FOOTNOTE_QUOTE_RE.match(s):
            cutoff = min(cutoff, i)
            break

        # Sign Up block
        if re.match(r'^#+\s*sign\s*up\s*$', s, re.IGNORECASE):
            cutoff = min(cutoff, i)
        if SIGNUP_RE.search(s):
            cutoff = min(cutoff, i)

        # TOC appendix (Vol 14)
        if TOC_ITEM_RE.match(s):
            cutoff = min(cutoff, i)

    # Post-afterword OCR blurb: scan up to 30 lines after Carlo Zen sign-off
    if carlo_idx is not None:
        for i in range(carlo_idx + 1, min(cutoff, carlo_idx + 30)):
            s = lines[i].strip()
            if s and BACK_COVER_RE.search(s):
                cutoff = min(cutoff, i)
                break

    # Trim trailing blank lines before the cut point
    while cutoff > 0 and not lines[cutoff - 1].strip():
        cutoff -= 1

    return cutoff


# ── Sentence rejoiner ──────────────────────────────────────────────────────

def rejoin_split_sentences(lines: list) -> list:
    """
    Merge sentences split across blank-line breaks.
    E.g.  "...right before their"  /  blank  /  "eyes."
    becomes  "...right before their eyes."
    """
    out = []
    i   = 0
    while i < len(lines):
        line = lines[i]
        s    = line.strip()

        if (s
                and not HEADING_RE.match(line)
                and not SENTENCE_END_RE.search(s)
                and any(c.isalpha() for c in s)):

            # Peek ahead past blanks (max 3)
            j, blanks = i + 1, 0
            while j < len(lines) and not lines[j].strip():
                blanks += 1
                j += 1

            if j < len(lines) and 0 < blanks <= 3:
                nxt       = lines[j].strip()
                alpha_nxt = sum(c.isalpha() for c in nxt)
                if (nxt
                        and len(nxt) <= 35
                        and alpha_nxt >= 2
                        and not HEADING_RE.match(lines[j])
                        and not is_ocr_noise(nxt)):
                    out.append(s + " " + nxt)
                    i = j + 1
                    continue

        out.append(line)
        i += 1

    return out


# ── Inline cleanup ─────────────────────────────────────────────────────────

def clean_line(line: str) -> str:
    line = re.sub(r" {2,}", " ", line)
    line = line.replace("\u200b", "").replace("\xa0", " ")
    # Strip inline markdown italic markers: _word_ → word
    line = re.sub(r'_([^_\n]+)_', r'\1', line)
    return line.strip()


def find_source_files() -> list[Path]:
    """Find source markdown files across common cleanup locations."""
    patterns = (
        (INPUT_DIR, "*Cleaned*.md"),
        (INPUT_FALLBACK_DIR, "*Cleaned*.md"),
        (INPUT_DIR, "*AudioPrep*.md"),
        (INPUT_DIR, "*Volume*.md"),
    )
    for base, pattern in patterns:
        files = sorted(
            p for p in base.glob(pattern)
            if not p.name.startswith("audio_prep_clean")
            and not p.name.startswith("clean_")
            and "CRITICAL_CHUNKS" not in p.name
        )
        if files:
            return files
    return []


# ── Per-file processor ─────────────────────────────────────────────────────

def process_file(src: Path, dst: Path) -> dict:
    raw   = src.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()
    n_in  = len(lines)

    # Step 1: Truncate end-of-book matter
    cutoff = find_endbook_cutoff(lines)
    endbook_removed = [l.strip() for l in lines[cutoff:] if l.strip()]
    lines  = lines[:cutoff]

    # Step 2: Rejoin split sentences
    lines = rejoin_split_sentences(lines)

    # Step 3: Line-by-line filtering
    out           = []
    blanks        = 0
    removed_lines = []  # (category, text)

    for line in lines:
        s = line.strip()

        if not s:
            blanks += 1
            if blanks <= 2:
                out.append("")
            continue
        blanks = 0

        # Always keep headings
        if HEADING_RE.match(line):
            out.append(clean_line(line))
            continue

        # Drop lingering signup / newsletter content
        if SIGNUP_RE.search(s):
            removed_lines.append(("signup", s))
            continue

        # Drop TOC items
        if TOC_ITEM_RE.match(s):
            removed_lines.append(("toc_item", s))
            continue

        # Drop OCR noise
        if is_ocr_noise(s):
            removed_lines.append(("ocr_noise", s))
            continue

        # Drop italic noise fragments (_a, _to, _really_, etc.)
        if is_italic_noise(s):
            removed_lines.append(("italic_noise", s))
            continue

        # Very short: keep if legitimate dialogue, otherwise merge into previous
        if len(s) <= 10:
            if not is_legitimate_short(s):
                if out and out[-1].strip():
                    out[-1] = out[-1].rstrip() + " " + s
                continue

        out.append(clean_line(s))

    # Step 4: Collapse excess blank lines
    final  = []
    blanks = 0
    for ln in out:
        if not ln:
            blanks += 1
            if blanks <= 2:
                final.append("")
        else:
            blanks = 0
            final.append(ln)

    text  = "\n".join(final).strip() + "\n"
    n_out = text.count("\n")

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    return {
        "in": n_in,
        "out": n_out,
        "removed": n_in - n_out,
        "text": text,
        "removed_lines": removed_lines,
        "endbook_removed": endbook_removed,
    }


# ── TTS chunk validator ────────────────────────────────────────────────────

def count_bad_tts_chunks(text: str) -> tuple:
    """
    Returns (critical, marginal, critical_chunks, marginal_chunks):
      critical        = paragraphs with < 2 alpha chars  (will crash Edge TTS)
      marginal        = paragraphs with 2–3 alpha chars  (short but usually fine)
      critical_chunks = list of (para_index, alpha_count, text_snippet)
      marginal_chunks = list of (para_index, alpha_count, text_snippet)
    """
    critical = marginal = 0
    critical_chunks = []
    marginal_chunks = []
    para_idx = 0
    for p in re.split(r"\n{2,}", text):
        p2    = re.sub(r"^#{1,6}\s+", "", p.strip())
        alpha = sum(c.isalpha() for c in p2)
        if p2:
            if alpha < 2:
                critical += 1
                snippet = p2[:100].replace("\n", " ")
                critical_chunks.append((para_idx, alpha, snippet))
            elif alpha < 4:
                marginal += 1
                snippet = p2[:100].replace("\n", " ")
                marginal_chunks.append((para_idx, alpha, snippet))
        para_idx += 1
    return critical, marginal, critical_chunks, marginal_chunks


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    sources = find_source_files()
    if not sources:
        print(
            "No files found matching source patterns in "
            f"{INPUT_DIR} or {INPUT_FALLBACK_DIR}."
        )
        sys.exit(1)

    print(f"Processing {len(sources)} volumes...\n")
    hdr = f"{'Volume':<10} {'In':>7} {'Out':>7} {'Cut':>6}  {'Crit':>5} {'Warn':>5}"
    print(hdr)
    print("-" * len(hdr))

    total_in = total_out = total_crit = total_warn = 0
    all_critical = []  # (volume, para_idx, alpha, snippet)
    all_marginal = []  # (volume, para_idx, alpha, snippet)
    all_removed  = []  # per-volume removed line info

    for src in sources:
        vol   = re.search(r'Volume[\s_-]*(\d+)', src.name, re.IGNORECASE)
        vnum  = vol.group(1) if vol else "??"

        # Output filename
        if re.search(r"_Cleaned\.md$", src.name, re.IGNORECASE):
            dst_name = re.sub(
                r"_Cleaned(\.md)$", r"_AudioPrep\1", src.name, flags=re.IGNORECASE
            )
        elif re.search(r"_AudioPrep\.md$", src.name, re.IGNORECASE):
            dst_name = src.name
        else:
            dst_name = src.stem + "_AudioPrep.md"
        dst = OUTPUT_DIR / dst_name

        stats = process_file(src, dst)
        crit, warn, crit_chunks, warn_chunks = count_bad_tts_chunks(stats["text"])

        total_in   += stats["in"]
        total_out  += stats["out"]
        total_crit += crit
        total_warn += warn

        for para_idx, alpha, snippet in crit_chunks:
            all_critical.append({"volume": vnum, "para_idx": para_idx,
                                  "alpha": alpha, "snippet": snippet})
        for para_idx, alpha, snippet in warn_chunks:
            all_marginal.append({"volume": vnum, "para_idx": para_idx,
                                  "alpha": alpha, "snippet": snippet})

        removed = stats.get("removed_lines", [])
        endbook = stats.get("endbook_removed", [])
        if removed or endbook:
            all_removed.append({
                "volume": vnum,
                "removed_lines": removed,
                "endbook_count": len(endbook),
                "endbook_sample": endbook[:5],
            })

        flag = " ⚠ CRITICAL" if crit else (" warn" if warn else "")
        print(f"Vol {vnum:<6} {stats['in']:>7,} {stats['out']:>7,} "
              f"{stats['removed']:>6,}  {crit:>5} {warn:>5}{flag}")

    print("-" * len(hdr))
    print(f"{'TOTAL':<10} {total_in:>7,} {total_out:>7,} "
          f"{total_in-total_out:>6,}  {total_crit:>5} {total_warn:>5}")
    print(f"\nOutput: {OUTPUT_DIR}")

    # Write comprehensive cleaning log
    log_path = OUTPUT_DIR / "CLEANING_LOG.log"
    with open(log_path, "w", encoding="utf-8") as f:

        f.write("AUDIO PREP CLEANING LOG\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Volumes processed : {len(sources)}\n")
        f.write(f"Total lines in    : {total_in:,}\n")
        f.write(f"Total lines out   : {total_out:,}\n")
        f.write(f"Total removed     : {total_in - total_out:,}\n")
        f.write(f"Critical chunks   : {total_crit}\n")
        f.write(f"Warning chunks    : {total_warn}\n\n")

        # ── Removed lines by volume ───────────────────────────────────────
        f.write("REMOVED LINES BY VOLUME\n")
        f.write("-" * 80 + "\n\n")
        if all_removed:
            for item in all_removed:
                f.write(f"Volume {item['volume']}:\n")
                if item["endbook_count"]:
                    f.write(f"  end_of_book: {item['endbook_count']} lines cut\n")
                    for ln in item["endbook_sample"]:
                        f.write(f"    {repr(ln)}\n")
                    if item["endbook_count"] > 5:
                        f.write(f"    ... ({item['endbook_count'] - 5} more)\n")
                by_cat: dict = {}
                for cat, text in item["removed_lines"]:
                    by_cat.setdefault(cat, []).append(text)
                for cat, texts in by_cat.items():
                    f.write(f"  {cat}: {len(texts)} line(s)\n")
                    for t in texts[:10]:
                        f.write(f"    {repr(t)}\n")
                    if len(texts) > 10:
                        f.write(f"    ... ({len(texts) - 10} more)\n")
                f.write("\n")
        else:
            f.write("No inline lines removed (beyond end-of-book cutoff).\n\n")

        # ── Critical TTS chunks ───────────────────────────────────────────
        f.write("CRITICAL TTS CHUNKS (< 2 alpha chars — WILL crash Edge TTS)\n")
        f.write("-" * 80 + "\n\n")
        if all_critical:
            for item in all_critical:
                f.write(f"Volume {item['volume']}, Paragraph {item['para_idx']}\n")
                f.write(f"  Alpha chars: {item['alpha']}\n")
                f.write(f"  Text: {repr(item['snippet'])}\n\n")
        else:
            f.write("No critical chunks found.\n\n")

        # ── Warning TTS chunks ────────────────────────────────────────────
        f.write("WARNING TTS CHUNKS (2-3 alpha chars — short dialogue, usually fine)\n")
        f.write("-" * 80 + "\n\n")
        if all_marginal:
            for item in all_marginal:
                f.write(f"Volume {item['volume']}, Paragraph {item['para_idx']}\n")
                f.write(f"  Alpha chars: {item['alpha']}\n")
                f.write(f"  Text: {repr(item['snippet'])}\n\n")
        else:
            f.write("No warning chunks.\n\n")

    print(f"Cleaning log: {log_path}")

    if total_crit:
        print(f"\n⚠  {total_crit} CRITICAL chunks (< 2 alpha chars) — these WILL"
              " crash Edge TTS without md_to_audio_fixed.py's guard.")
    elif total_warn:
        print(f"\n✓  No critical chunks.  {total_warn} short-dialogue chunks"
              " ('Huh?', 'Sir?' etc.) — Edge TTS handles these fine.")
    else:
        print("\n✓  All volumes clean and ready for audio conversion.")


if __name__ == "__main__":
    main()
