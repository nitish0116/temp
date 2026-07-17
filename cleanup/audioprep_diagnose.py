#!/usr/bin/env python3
"""
audioprep_diagnose.py
---------------------
Diagnoses AudioPrep markdown files for issues that cause
Edge TTS "No audio was received" errors.

Checks every TTS chunk for:
  1. LOW_ALPHA      — fewer than 4 alphabetic chars (empty/fragment chunks)
  2. XML_BRACKETS   — raw < or > chars that break SSML
  3. RAW_AMP        — raw & not encoded as &amp; (breaks SSML XML)
  4. UNBAL_CURLY    — unbalanced curly quotes " " split across chunk boundary

Usage:
    python audioprep_diagnose.py file.md
    python audioprep_diagnose.py vol01.md vol02.md vol03.md
    python audioprep_diagnose.py *.md
"""

import re
import sys
import importlib.util
from pathlib import Path


def load_md_to_audio(script_path: Path):
    """Load narration_paragraphs() from md_to_audio.py."""
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
    """Search for md_to_audio.py in common locations."""
    candidates = [
        Path("md_to_audio.py"),
        Path(__file__).parent / "md_to_audio.py",
    ]
    for p in candidates:
        if p.exists():
            return p
    print("ERROR: md_to_audio.py not found. Place it in the same folder as this script.")
    sys.exit(1)


def diagnose_chunks(chunks: list[str]) -> dict[int, tuple[list[str], str]]:
    """Return {chunk_num: ([issues], preview)} for all problem chunks."""
    problems = {}
    for i, chunk in enumerate(chunks):
        issues = []
        alpha  = sum(c.isalpha() for c in chunk)

        if alpha < 4:
            issues.append(f"LOW_ALPHA({alpha})")
        if re.search(r'[<>]', chunk):
            issues.append("XML_BRACKETS")
        if '&' in chunk and not re.search(r'&(?:amp|lt|gt|quot|apos|#\d+);', chunk):
            issues.append("RAW_AMP")
        o = chunk.count('\u201c')
        c_ = chunk.count('\u201d')
        if o != c_:
            issues.append(f"UNBAL_CURLY({o}o/{c_}c)")

        if issues:
            problems[i + 1] = (issues, chunk[:100])

    return problems


def diagnose_file(md_path: Path, mod, chunk_size: int = 2600) -> dict:
    text   = md_path.read_text(encoding="utf-8", errors="replace")
    lines  = text.splitlines()
    chunks = mod.narration_paragraphs(text, chunk_size)
    problems = diagnose_chunks(chunks)

    # Count by issue type
    type_counts: dict[str, int] = {}
    for iss_list, _ in problems.values():
        for iss in iss_list:
            key = re.sub(r'\(.*\)', '', iss)   # strip detail like (1o/0c)
            type_counts[key] = type_counts.get(key, 0) + 1

    return {
        "lines":       len(lines),
        "chunks":      len(chunks),
        "problems":    problems,
        "type_counts": type_counts,
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    md_script = find_md_to_audio()
    mod        = load_md_to_audio(md_script)
    skip_next = False
    md_files  = []
    for a in sys.argv[1:]:
        if skip_next: skip_next = False; continue
        if a.startswith("-"):
            if a in ("--output-dir", "--chunk-size"): skip_next = True
            continue
        p = Path(a)
        if p.is_dir(): continue
        md_files.append(p)
    verbose    = "--verbose" in sys.argv or "-v" in sys.argv
    chunk_size = 2600
    for a in sys.argv[1:]:
        if a.startswith("--chunk-size="):
            chunk_size = int(a.split("=")[1])

    if not md_files:
        print("No markdown files specified.")
        sys.exit(1)

    # Expand globs if shell didn't
    expanded = []
    for p in md_files:
        if "*" in str(p):
            expanded.extend(sorted(Path(".").glob(str(p))))
        elif p.exists():
            expanded.append(p)
        else:
            print(f"[WARN] Not found: {p}")
    md_files = expanded

    print(f"md_to_audio.py : {md_script}")
    print(f"Chunk size     : {chunk_size}")
    print(f"Files          : {len(md_files)}")
    print()

    # Summary table
    print(f"{'File':<50} {'Lines':>7} {'Chunks':>7} {'Issues':>7}  Types")
    print("-" * 90)

    grand_total = 0
    results = {}
    for md_path in md_files:
        r = diagnose_file(md_path, mod, chunk_size)
        results[md_path] = r
        n     = len(r["problems"])
        grand_total += n
        types = "  ".join(f"{k}={v}" for k, v in r["type_counts"].items())
        flag  = "✓" if n == 0 else "⚠"
        print(f"{flag} {md_path.name:<48} {r['lines']:>7,} {r['chunks']:>7,} {n:>7}  {types}")

    print("-" * 90)
    print(f"{'TOTAL':<50} {sum(r['lines'] for r in results.values()):>7,} "
          f"{sum(r['chunks'] for r in results.values()):>7,} {grand_total:>7}")

    # Detailed output
    if verbose or any(len(r["problems"]) > 0 for r in results.values()):
        for md_path, r in results.items():
            if not r["problems"]:
                continue
            print(f"\n{'='*70}")
            print(f"DETAILS: {md_path.name}  ({len(r['problems'])} problem chunks)")
            print(f"{'='*70}")
            for chunk_num, (iss_list, preview) in sorted(r["problems"].items()):
                print(f"  Chunk #{chunk_num:5d}  [{', '.join(iss_list)}]")
                print(f"           {repr(preview)}")

    print()
    if grand_total == 0:
        print("✓ All files are clean — ready for audio conversion.")
    else:
        print(f"⚠  {grand_total} problem chunks found across {len(md_files)} file(s).")
        print("   Run audioprep_fix.py on the affected files to fix them.")


if __name__ == "__main__":
    main()
