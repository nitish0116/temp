#!/usr/bin/env python3
"""
hellmode.py
-----------
Strips erroneous heading markers (##, ###, ######, etc.) from the cleaned
AudioPrep md files for Volumes 1-3.

A line like:
    ###### "WAAAAAHHHH! WAAAAAAHHHH!"
is body text that accidentally got hash-prefixed during OCR/conversion.

A line like:
    #### Chapter 1: I Reincarnated as a Serf
is a legitimate structural heading and is kept as-is.

Valid headings are determined by:
  1. The chapter list in chapterDetails hellmode.txt (Vols 1-3)
  2. A fixed set of always-valid structural keywords
     (Prologue, Epilogue, Afterword, Introduction, By <author>, etc.)

Output: overwrites the Vol 1-3 AudioPrep files in-place.
Log   : writes HEADING_FIX.log in the same output directory.
"""

import re
from pathlib import Path

CHAPTER_DETAILS = Path(r"C:\Users\z005537p\NitishWork\HM\temp\cleanup\hellmode\chapterDetails hellmode.txt")
AUDIO_PREP_DIR  = Path(r"C:\Users\z005537p\NitishWork\HM\temp\cleanup\cleaned_audio_prep")

TARGET_VOLUMES  = {9,10,11}

# ── Always-valid structural heading keywords (matched by prefix) ───────────
STRUCTURAL_PREFIXES = re.compile(
    r"^(Prologue|Epilogue|Afterword|Introduction"
    r"|Bonus Short Stor|Bonus Stor"
    r"|By\s+\w)"
    , re.IGNORECASE,
)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")


# ── Load chapter names from the details file ───────────────────────────────

def load_valid_headings(path: Path) -> set[str]:
    """
    Parse chapterDetails hellmode.txt for Volumes 1-3.
    Returns a set of normalised heading strings (lowercased, stripped).
    """
    valid: set[str] = set()
    current_vol = 0

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue

        vol_match = re.match(r"^Volume\s+(\d+)$", line, re.IGNORECASE)
        if vol_match:
            current_vol = int(vol_match.group(1))
            continue

        if current_vol in TARGET_VOLUMES:
            valid.add(normalise(line))

    return valid


def normalise(text: str) -> str:
    """Strip markdown formatting and lowercase for comparison."""
    t = text.strip()
    t = re.sub(r"\*+", "", t)          # bold/italic asterisks
    t = re.sub(r"</?u>", "", t)        # underline tags
    t = re.sub(r"_([^_]+)_", r"\1", t) # italic underscores
    t = re.sub(r"`", "", t)            # backticks
    t = t.strip().lower()
    return t


def is_valid_heading(text: str, valid_headings: set[str]) -> bool:
    """Return True if the heading text should be kept as a heading."""
    norm = normalise(text)
    if norm in valid_headings:
        return True
    if STRUCTURAL_PREFIXES.match(text.strip()):
        return True
    return False


# ── Per-file processor ─────────────────────────────────────────────────────

def process_file(path: Path, valid_headings: set[str]) -> list[tuple[int, str, str]]:
    """
    Process one AudioPrep md file.
    Rewrites it in-place; returns list of (line_no, original, fixed) for log.
    """
    lines   = path.read_text(encoding="utf-8").splitlines()
    out     = []
    changes = []

    for i, line in enumerate(lines, 1):
        m = HEADING_RE.match(line)
        if m:
            hashes, text = m.group(1), m.group(2)
            if is_valid_heading(text, valid_headings):
                out.append(line)          # keep as legitimate heading
            else:
                fixed = text              # strip the hash prefix entirely
                out.append(fixed)
                changes.append((i, line, fixed))
        else:
            out.append(line)

    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changes


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    valid_headings = load_valid_headings(CHAPTER_DETAILS)
    print(f"Loaded {len(valid_headings)} valid chapter headings from chapter details.")

    # Find Vol 1-3 AudioPrep files
    targets = []
    for f in sorted(AUDIO_PREP_DIR.glob("*.md")):
        if "CRITICAL" in f.name:
            continue
        vol_match = re.search(r"Volume[\s_-]*0*(\d+)", f.name, re.IGNORECASE)
        if vol_match and int(vol_match.group(1)) in TARGET_VOLUMES:
            targets.append((int(vol_match.group(1)), f))

    if not targets:
        print(f"No matching files found in {AUDIO_PREP_DIR}")
        return

    print(f"Processing {len(targets)} volume(s): {[v for v,_ in targets]}\n")

    all_changes: list[dict] = []
    total_fixed = 0

    for vol_num, path in targets:
        changes = process_file(path, valid_headings)
        total_fixed += len(changes)
        print(f"Vol {vol_num:02d} — {path.name}: {len(changes)} heading(s) stripped")
        for lineno, original, fixed in changes:
            all_changes.append({
                "volume": vol_num,
                "file":   path.name,
                "line":   lineno,
                "before": original,
                "after":  fixed,
            })

    # Write log
    log_path = AUDIO_PREP_DIR / "HEADING_FIX.log"
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("HEADING FIX LOG — Volumes 1-3\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Total heading markers stripped: {total_fixed}\n\n")

        if all_changes:
            for item in all_changes:
                f.write(f"Vol {item['volume']}, line {item['line']}:\n")
                f.write(f"  BEFORE: {repr(item['before'])}\n")
                f.write(f"  AFTER : {repr(item['after'])}\n\n")
        else:
            f.write("No erroneous headings found.\n")

    print(f"\nTotal stripped: {total_fixed}")
    print(f"Log: {log_path}")


if __name__ == "__main__":
    main()
