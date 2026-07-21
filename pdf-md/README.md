# PDF and EPUB to Markdown

Converts PDFs and EPUB ebooks into structured Markdown suitable for the
`markdownCleaner` pipeline. For PDFs, it preserves multi-column reading order
and tables, omits recurring headers and footers by default, and uses OCR only
when needed. EPUB content is extracted in its existing chapter order without OCR.

## Install

```powershell
.\.venv\Scripts\python.exe -m pip install -r pdf-md\requirements.txt
```

## Usage

```powershell
$env:PYTHONPATH = "pdf-md"
.\.venv\Scripts\python.exe -m pdf_to_markdown input.pdf -o pdf-md\output
```

EPUB conversion uses the same command:

```powershell
.\.venv\Scripts\python.exe -m pdf_to_markdown input.epub -o pdf-md\output
```

Convert a folder recursively:

```powershell
.\.venv\Scripts\python.exe -m pdf_to_markdown C:\Books -o pdf-md\output -r
```

Useful options:

```text
--ocr auto|off|force
--ocr-language eng
--layout fast|smart
--pages 1-10,15
--keep-header
--keep-footer
--images ignore|write|embed
--continue-on-error
```

`--layout fast` is the default and is recommended for books. Use
`--layout smart` only for difficult multi-column pages or complex tables; the
neural layout model is substantially slower on full-length documents. Fast
legacy extraction does not support OCR or header/footer classification. With
`--ocr auto`, an empty fast result is automatically retried in smart mode. Use
`--layout smart --ocr auto` for mixed scanned/digital PDFs, or `--ocr force`
when the PDF's embedded text layer is corrupt.

Batch conversion discovers both `.pdf` and `.epub` files. Each Markdown output
receives a sibling `.conversion.json` report. Run the
result through `markdownCleaner` as the next step for publication-matter,
OCR-noise, paragraph, spelling, and TTS cleanup.
