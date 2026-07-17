#!/usr/bin/env python3
"""
audioprep_fix.py
----------------
Fixes AudioPrep markdown files to eliminate Edge TTS
"No audio was received" errors.

Fixes applied:
  1. XML brackets   < >  →  ( )
  2. Raw ampersands  &   →  and
  3. Split sentences rejoined across blank-line paragraph breaks
  4. Adjacent lines with unbalanced curly quotes " " merged
  5. All remaining curly quotes converted to straight quotes

Usage:
    python audioprep_fix.py file.md
    python audioprep_fix.py vol01.md vol02.md vol03.md
    python audioprep_fix.py *.md

    # Write fixed files to a specific output folder:
    python audioprep_fix.py *.md --output-dir ./fixed

    # Overwrite in place (careful!):
    python audioprep_fix.py *.md --inplace
"""

import re
import sys
import importlib.util
from pathlib import Path


# ── Patterns ───────────────────────────────────────────────────────────────
HEADING_RE  = re.compile(r'^#{1,6}\s')
SENT_END_RE = re.compile(r'[.!?…;\u201d\u2019"\')}\]]\s*$')


def load_md_to_audio(script_path: Path):
    """Load narration_paragraphs() from md_to_audio.py for post-fix validation."""
    _argv = sys.argv[:]
    sys.argv = [str(script_path), "--help"]
    spec = importlib.util.spec_from_file_location("md_to_audio", str(script_path))
    mod  = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    sys.argv = _argv
    return mod


def find_md_to_audio() -> Path:
    candidates = [
        Path("md_to_audio.py"),
        Path(__file__).parent / "md_to_audio.py",
    ]
    for p in candidates:
        if p.exists():
            return p
    print("ERROR: md_to_audio.py not found. Place it in the same folder as this script.")
    sys.exit(1)


# ── Fixers ─────────────────────────────────────────────────────────────────

def fix_xml_brackets(lines: list[str]) -> tuple[list[str], int]:
    """Replace < > with ( ) to prevent SSML parse errors."""
    out   = []
    count = 0
    for line in lines:
        if re.search(r'[<>]', line):
            line = line.replace('<', '(').replace('>', ')')
            count += 1
        out.append(line)
    return out, count


def fix_raw_ampersands(lines: list[str]) -> tuple[list[str], int]:
    """Replace bare & with 'and' to prevent SSML XML entity errors."""
    out   = []
    count = 0
    for line in lines:
        if '&' in line and not re.search(r'&(?:amp|lt|gt|quot|apos|#\d+);', line):
            line = re.sub(r'&', 'and', line)
            count += 1
        out.append(line)
    return out, count


def fix_split_sentences(lines: list[str]) -> tuple[list[str], int]:
    """
    Rejoin sentences split across blank-line paragraph breaks.

    Detects: content line ending without sentence-ending punctuation, followed
    by one or more blank lines, followed by a short continuation fragment.
    Merges them into a single line.
    """
    out   = []
    i     = 0
    count = 0
    while i < len(lines):
        line = lines[i]
        s    = line.strip()

        if (s
                and not HEADING_RE.match(line)
                and not SENT_END_RE.search(s)
                and any(c.isalpha() for c in s)):
            # Peek ahead past blank lines
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and j - i <= 4:
                nxt = lines[j].strip()
                if (nxt
                        and len(nxt) <= 80
                        and sum(c.isalpha() for c in nxt) >= 2
                        and not HEADING_RE.match(lines[j])):
                    out.append(s + " " + nxt)
                    count += 1
                    i = j + 1
                    continue

        out.append(line)
        i += 1

    return out, count


def fix_curly_quote_splits(lines: list[str]) -> tuple[list[str], int]:
    """
    Merge adjacent lines where a curly-quote dialogue span is split across a
    blank-line paragraph break.

    Detects: line with unmatched " or ", next non-blank line that when combined
    balances the quote count. Merges them into a single line.
    """
    out   = []
    i     = 0
    count = 0
    while i < len(lines):
        line = lines[i]
        s    = line.strip()

        if s and not HEADING_RE.match(line):
            opens  = s.count('\u201c')   # "
            closes = s.count('\u201d')   # "

            if opens != closes:
                # Find next non-blank line
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1

                if j < len(lines) and not HEADING_RE.match(lines[j]):
                    nxt = lines[j].strip()
                    no  = nxt.count('\u201c')
                    nc  = nxt.count('\u201d')
                    if (opens + no) == (closes + nc):
                        out.append(s + " " + nxt)
                        count += 1
                        i = j + 1
                        continue

        out.append(line)
        i += 1

    return out, count


def fix_curly_to_straight(text: str) -> str:
    """
    Convert all curly/smart quotes to straight ASCII quotes.

    This eliminates any remaining chunk-level curly-quote imbalances that
    survive the merge pass (e.g. multi-paragraph dialogue spans). Straight
    quotes are parsed correctly by Edge TTS in all cases.
    """
    text = text.replace('\u201c', '"').replace('\u201d', '"')  # " "
    text = text.replace('\u2018', "'").replace('\u2019', "'")  # ' '
    return text


# ── Validation ─────────────────────────────────────────────────────────────

def validate_chunks(text: str, mod, chunk_size: int = 2600) -> list[tuple[int, list[str], str]]:
    """Return list of (chunk_num, [issues], preview) for remaining problems."""
    chunks   = mod.narration_paragraphs(text, chunk_size)
    problems = []
    for i, chunk in enumerate(chunks):
        issues = []
        if sum(c.isalpha() for c in chunk) < 4:
            issues.append("LOW_ALPHA")
        if re.search(r'[<>]', chunk):
            issues.append("XML_BRACKETS")
        if '&' in chunk and not re.search(r'&(?:amp|lt|gt|quot|apos);', chunk):
            issues.append("RAW_AMP")
        o  = chunk.count('\u201c')
        c_ = chunk.count('\u201d')
        if o != c_:
            issues.append(f"UNBAL_CURLY({o}o/{c_}c)")
        if issues:
            problems.append((i + 1, issues, chunk[:80]))
    return problems


# ── Per-file processor ─────────────────────────────────────────────────────

def fix_file(
    src_path:   Path,
    dst_path:   Path,
    mod,
    chunk_size: int = 2600,
    verbose:    bool = False,
) -> dict:
    text  = src_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    counts = {}

    lines, counts["xml"]   = fix_xml_brackets(lines)
    lines, counts["amp"]   = fix_raw_ampersands(lines)
    lines, counts["split"] = fix_split_sentences(lines)
    lines, counts["curly"] = fix_curly_quote_splits(lines)

    text_out = "\n".join(lines)
    text_out = fix_curly_to_straight(text_out)
    text_out = re.sub(r'\n{4,}', '\n\n\n', text_out).strip() + "\n"

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(text_out, encoding="utf-8")

    remaining = validate_chunks(text_out, mod, chunk_size)

    if verbose and remaining:
        print(f"\n  Remaining problems after fix:")
        for num, iss, prev in remaining:
            print(f"    Chunk #{num}: [{', '.join(iss)}] {repr(prev)}")

    return {"counts": counts, "remaining": remaining, "lines_out": text_out.count('\n')}


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    args       = sys.argv[1:]
    inplace    = "--inplace" in args
    verbose    = "--verbose" in args or "-v" in args
    chunk_size = 2600
    output_dir = None

    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--output-dir="):
            output_dir = Path(a.split("=", 1)[1])
        elif a == "--output-dir" and i + 1 < len(args):
            output_dir = Path(args[i + 1]); i += 1
        elif a.startswith("--chunk-size="):
            chunk_size = int(a.split("=", 1)[1])
        elif a == "--chunk-size" and i + 1 < len(args):
            chunk_size = int(args[i + 1]); i += 1
        i += 1

    # Only include args that look like files (not flags, not the output-dir value)
    skip_next = False
    md_files  = []
    for idx, a in enumerate(args):
        if skip_next:
            skip_next = False; continue
        if a.startswith("-"):
            if a in ("--output-dir", "--chunk-size"): skip_next = True
            continue
        p = Path(a)
        if p.is_dir(): continue   # skip bare directory args
        md_files.append(p)

    # Expand globs
    expanded = []
    for p in md_files:
        if "*" in str(p):
            expanded.extend(sorted(Path(".").glob(str(p))))
        elif p.exists():
            expanded.append(p)
        else:
            print(f"[WARN] Not found: {p}")
    md_files = expanded

    if not md_files:
        print("No markdown files found.")
        sys.exit(1)

    md_script = find_md_to_audio()
    mod        = load_md_to_audio(md_script)

    print(f"md_to_audio.py : {md_script}")
    print(f"Output         : {'in-place' if inplace else output_dir or 'same folder, suffix _Fixed'}")
    print(f"Files          : {len(md_files)}")
    print()
    print(f"{'File':<50} {'xml':>5} {'amp':>5} {'split':>6} {'curly':>6}  {'Result'}")
    print("-" * 85)

    grand_remaining = 0
    for src in md_files:
        if inplace:
            dst = src
        elif output_dir:
            dst = output_dir / src.name
        else:
            dst = src.with_name(src.stem + "_Fixed.md")

        result = fix_file(src, dst, mod, chunk_size, verbose)
        c      = result["counts"]
        rem    = len(result["remaining"])
        grand_remaining += rem
        status = "✓ Clean" if rem == 0 else f"⚠ {rem} remain"
        print(f"  {src.name:<48} {c['xml']:>5} {c['amp']:>5} {c['split']:>6} "
              f"{c['curly']:>6}  {status}")
        if rem and not verbose:
            for num, iss, prev in result["remaining"][:2]:
                print(f"    #{num}: [{', '.join(iss)}] {repr(prev)}")

    print("-" * 85)
    print()
    if grand_remaining == 0:
        print("✓ All files fixed and validated — ready for audio conversion.")
    else:
        print(f"⚠  {grand_remaining} chunks still have issues.")
        print("   Re-run with --verbose for details.")


if __name__ == "__main__":
    main()
