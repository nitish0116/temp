# md-pdf — Markdown to PDF Converter

Convert Markdown files into clean, selectable-text PDFs suitable for Adobe Acrobat's "Read Out Loud" feature and other screen readers.

---

## System Requirements

| Requirement | Details |
|---|---|
| Python | 3.10 or later |
| OS | Windows, macOS, or Linux |
| pip packages | `markdown`, `reportlab` (auto-installed on first run, or install manually) |

---

## Installation

1. Copy the `md_python` folder to your machine.
2. Install Python dependencies:

```bash
pip install -r requirements.txt
```

> The script also installs missing packages automatically on first run if you skip this step.

---

## Quick Start

```bash
# Convert a single Markdown file
python md_to_pdf.py books/book.md output/book.pdf

# Convert all Markdown files in the books/ folder
python md_to_pdf.py --all
```

---

## Command-line Reference

```
python md_to_pdf.py [input.md] [output.pdf] [options]
```

| Argument / Flag | Description |
|---|---|
| `input.md` | Path to the source Markdown file. Defaults to the only `.md` file in the script directory. |
| `output.pdf` | Destination PDF path. Defaults to the same folder as the input, with a cleaned filename. |
| `--all` | Convert every `.md` file found in the script's folder. |

---

## Output

- The PDF uses **Times-Roman** at 12 pt with justified body text.
- Headings, lists, block quotes, and horizontal rules are all preserved.
- Each top-level heading (`# …`) starts on a new page.
- Text is real, selectable, and laid out in natural reading order — no image scans.
- The PDF's `lang` attribute is set to `en-US` so screen readers pick the right voice.

---

## What Gets Cleaned Before Conversion

The converter pre-processes Markdown to remove common ebook-export artifacts:

- Markdown code-fence markers that wrap page-break-split prose (`` ``` ``)
- Decorative Unicode ornament glyphs (scene-break symbols like ◆◇◆)
- Stray leading heading hashes on body lines
- Trailing OCR noise tokens from scanned artwork
- Hard line-wrapping is rejoined into flowing paragraphs
- Structural lines (`Prologue`, `Chapter N`, `Epilogue`, `Afterword`, etc.) are promoted to real PDF headings

---

## Files

| File | Purpose |
|---|---|
| `md_to_pdf.py` | Main script — converts Markdown to accessible PDF |
| `requirements.txt` | Python package dependencies |
| `books/` | Source Markdown files |
| `output/` | Generated PDF files |

---

## Examples

```bash
# Single file
python md_to_pdf.py "books/The Unwanted Undead Adventurer - Volume 04.md"

# Single file with explicit output path
python md_to_pdf.py "books/Volume 04.md" "output/Volume 04.pdf"

# All files in the books/ folder (output goes beside each source file by default)
python md_to_pdf.py --all
```
