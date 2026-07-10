# PDF Reference Guide

Advanced techniques, alternative libraries, and troubleshooting that
complement the core operations in `SKILL.md`.

## Advanced Python: pypdfium2

`pypdfium2` wraps Google's PDFium engine — the same renderer used by Chrome.
It is fast and excellent for rendering pages to images.

### Render pages to images (PNG)
```python
import pypdfium2 as pdfium

pdf = pdfium.PdfDocument("document.pdf")
for i in range(len(pdf)):
    page = pdf[i]
    bitmap = page.render(scale=2.0)  # 2.0 => ~144 DPI
    pil_image = bitmap.to_pil()
    pil_image.save(f"page_{i + 1}.png")
```

### Extract text with positions
```python
import pypdfium2 as pdfium

pdf = pdfium.PdfDocument("document.pdf")
page = pdf[0]
textpage = page.get_textpage()
print(textpage.get_text_range())          # all text on the page
print(textpage.count_chars(), "characters")
```

## Merging with bookmarks / outlines preserved

`pypdf`'s `PdfWriter.append()` keeps the source document's outline (bookmarks),
unlike manually copying pages one by one.

```python
from pypdf import PdfWriter

writer = PdfWriter()
for path in ["intro.pdf", "body.pdf", "appendix.pdf"]:
    writer.append(path)          # preserves bookmarks from each file
writer.write("combined.pdf")
writer.close()
```

## Compressing / reducing PDF size

```python
from pypdf import PdfReader, PdfWriter

reader = PdfReader("large.pdf")
writer = PdfWriter()

for page in reader.pages:
    page.compress_content_streams()   # lossless stream compression
    writer.add_page(page)

with open("smaller.pdf", "wb") as f:
    writer.write(f)
```

For image-heavy PDFs, downsample images with Ghostscript instead:
```bash
gs -sDEVICE=pdfwrite -dCompatibilityLevel=1.4 \
   -dPDFSETTINGS=/ebook -dNOPAUSE -dQUIET -dBATCH \
   -sOutputFile=smaller.pdf large.pdf
```
`/screen` (72 dpi) < `/ebook` (150 dpi) < `/printer` (300 dpi) < `/prepress`.

## Accessible / Read-Out-Loud friendly PDFs

For text-to-speech (e.g. Acrobat "Read Out Loud"):
- Keep a single-column, top-to-bottom reading order.
- Use real, selectable text — never scanned images (OCR first if needed).
- Set document language and title metadata so the correct voice is chosen.

```python
from reportlab.platypus import SimpleDocTemplate
doc = SimpleDocTemplate(
    "readable.pdf",
    title="My Document",
    author="Author Name",
    lang="en-US",     # important for narration voice selection
)
```

## JavaScript: pdf-lib

For Node.js / browser environments where Python is unavailable.

```javascript
import { PDFDocument, StandardFonts, rgb } from "pdf-lib";
import fs from "fs";

const pdfDoc = await PDFDocument.create();
const page = pdfDoc.addPage([595, 842]); // A4 in points
const font = await pdfDoc.embedFont(StandardFonts.Helvetica);

page.drawText("Hello from pdf-lib!", {
    x: 50,
    y: 780,
    size: 18,
    font,
    color: rgb(0, 0, 0),
});

const bytes = await pdfDoc.save();
fs.writeFileSync("out.pdf", bytes);
```

### Merge with pdf-lib
```javascript
import { PDFDocument } from "pdf-lib";
import fs from "fs";

const merged = await PDFDocument.create();
for (const file of ["a.pdf", "b.pdf"]) {
    const src = await PDFDocument.load(fs.readFileSync(file));
    const pages = await merged.copyPages(src, src.getPageIndices());
    pages.forEach((p) => merged.addPage(p));
}
fs.writeFileSync("merged.pdf", await merged.save());
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `extract_text()` returns empty string | PDF is a scanned image | OCR with `pytesseract` (see SKILL.md) |
| Black boxes where sub/superscripts should be | Unicode sub/superscript glyphs missing in font | Use `<sub>` / `<super>` markup in ReportLab |
| Garbled/duplicated text on extraction | Complex multi-column layout | Use `pdfplumber` with `extract_text(layout=True)` |
| `PdfReadError: EOF marker not found` | Truncated/corrupt file | Repair with `qpdf --replace-input broken.pdf` |
| Encrypted file raises on read | Password protected | `PdfReader(path, password="...")` or `qpdf --decrypt` |
| Merged PDF lost bookmarks | Pages copied individually | Use `writer.append()` instead of `add_page()` |
| Fonts look wrong / missing | Non-embedded fonts | Embed fonts, or flatten via Ghostscript `pdfwrite` |

## Installation cheatsheet

```bash
# Python libraries
pip install pypdf pdfplumber reportlab pypdfium2 pytesseract pdf2image pandas

# System tools (Windows via winget)
winget install poppler          # pdftotext, pdfimages
winget install qpdf.qpdf        # qpdf
# Ghostscript and Tesseract OCR also via winget:
winget install ArtifexSoftware.GhostScript
winget install UB-Mannheim.TesseractOCR
```

## See also
- `SKILL.md` — core operations and quick reference
- `FORMS.md` — filling and flattening PDF forms
