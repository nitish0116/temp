# markdownCleaner v6

OCR/PDF-extracted novel Markdown cleanup aimed at text-to-speech preparation.

## Pipeline

1. Backup original file
2. `DocumentCleanup`
   - removes picture OCR and residual HTML comments
   - removes title/copyright/publisher/table-of-contents front matter
   - treats a standalone `Afterword` heading as a hard cutoff and removes everything from it to EOF
   - removes common back matter and footnotes
   - includes the former `NovelCleanup` behavior
   - recognizes real `Prologue`, `Chapter`, `Interlude`, and `Epilogue` headings
   - demotes false converter headings such as `## then—` back into prose
   - reconstructs PDF-wrapped paragraphs
   - strips Markdown emphasis for TTS
   - normalizes blank lines
3. `Unicode` normalization
4. `RegexOCR` conservative deterministic fixes
5. `SymSpell` safe dictionary correction
6. Export clean Markdown and reports

## Install

```bash
pip install -r requirements.txt
```

## CLI: one file

```bash
python -m markdownCleaner.cli "book.md"
```

Choose an output directory:

```bash
python -m markdownCleaner.cli "book.md" -o cleaned
```

Use another config file:

```bash
python -m markdownCleaner.cli "book.md" --config my_config.yaml
```

## CLI: every Markdown file in a folder

Process only `.md` files directly inside a folder:

```bash
python -m markdownCleaner.cli books -o cleaned
```

Process the folder and every subfolder recursively:

```bash
python -m markdownCleaner.cli books -o cleaned --recursive
```

Continue with the remaining books when one file fails:

```bash
python -m markdownCleaner.cli books -o cleaned --recursive --continue-on-error
```

Folder mode preserves relative subfolders. For example:

```text
books/
├── Volume 01.md
└── side/
    └── Volume 02.md
```

becomes:

```text
cleaned/
├── Volume_01_clean.md
├── reports/
│   └── Volume_01/
│       ├── changes.json
│       └── summary.md
└── side/
    ├── Volume_02_clean.md
    └── reports/
        └── Volume_02/
            ├── changes.json
            └── summary.md
```

Each input file also receives its own timestamped backup. Backup timestamps include microseconds so batch jobs cannot collide when multiple files start within the same second.

## SymSpell safety

SymSpell is enabled by default and uses the 82k English frequency dictionary shipped with `symspellpy` for edit-distance correction. The broader `wordfreq` English corpus supplies frequency evidence for OCR-split word merging, including inflected forms omitted by the compact dictionary. High-confidence corrections are auto-applied while glossary terms, repeated proper nouns, mixed-case names, acronyms, and ambiguous candidates are protected.

Add book-specific names to:

```text
data/custom_words.json
```

The unsafe global `rn -> m` rule remains disabled.

## Python API

```python
from markdownCleaner.pipeline import OCRPipeline

pipeline = OCRPipeline("markdownCleaner/config.yaml")
result = pipeline.run("book.md")
print(result["output"]["markdown"])
```

## v7 profile/back-matter behavior

- `Character Profiles` is retained in the final Markdown.
- Picture-text OCR is removed from the main story but preserved inside the actual Character Profiles section.
- `Afterword` and OCR variant `Aferword` are hard cutoffs; everything from that heading onward is removed.
- `Story N | Title` headings are supported in addition to Chapter/Prologue/Epilogue headings.
- False converter headings are demoted, and em-dash continuations such as `And then—` / `## —something` are rejoined.

## Generalized sequence-independent cleanup (v8)

The document cleaner does not assume a specific novel layout or section order.
It no longer discards everything before the first Chapter/Story or everything
after an Afterword. Unknown sections and existing Markdown headings are preserved
by default.

Cleanup is local and configurable:

- `cleanup.picture_ocr_mode: safe` keeps readable OCR from images (captions,
  diagrams, profile cards, maps, labels) and removes only likely gibberish.
  Set it to `keep` to preserve all picture OCR or `remove` to discard all of it.
- `cleanup.excluded_sections` removes only explicitly named sections. Removal
  stops at the next recognized section heading, so later content is preserved.
- `cleanup.remove_front_matter` removes only clearly identified local metadata
  sections/lines such as Copyright or Contents; it does not truncate the document
  based on where the first narrative heading appears.
- Existing unknown Markdown headings are preserved. Strong structural headings
  such as Chapter, Story, Part, Book, Volume, Act, Section, Prologue, Epilogue,
  Appendix, Character Profiles, Glossary, and similar headings are normalized.
- Structured multi-line blocks are preserved using content heuristics rather than
  switching into a special mode after any particular section name.

Example configuration:

```yaml
cleanup:
  enabled: true
  remove_picture_ocr: true
  picture_ocr_mode: "safe"   # safe | keep | remove
  remove_front_matter: true
  excluded_sections:
    - "Afterword"
    - "Aferword"
  remove_footnotes: true
  strip_markdown_emphasis: true
```

To retain Afterword as well, set `excluded_sections: []`.

## Combined batch summary report

When the CLI processes a folder, it now writes one aggregate report for the entire batch run in addition to the existing per-file reports:

```text
<output>/reports/batch_summary.md
```

The combined report contains:

- total files discovered, succeeded, and failed
- total changes across the complete batch
- aggregate change counts by pipeline stage
- per-file status, elapsed time, output path, and stage counts
- every logged change from every processed file, grouped by source file, including reason, confidence, before text, and after text
- partial change records for files where a later pipeline stage fails

Example:

```bash
python -m markdownCleaner.cli books -o cleaned --recursive
```

Use a custom aggregate report filename with:

```bash
python -m markdownCleaner.cli books -o cleaned --recursive \
  --batch-report-name all_changes.md
```

Single-file mode continues to create only the normal per-file reports.


## Generalized cleanup additions

The document cleanup stage now removes explicit publication metadata wherever it occurs rather than assuming a fixed novel sequence. It recognizes standalone Copyright, Contents/Table of Contents, Yen/J-Novel publisher sections, common ebook navigation blocks, and standard publisher boilerplate. It also removes high-confidence raw OCR gibberish, front-cover picture OCR when locally adjacent to metadata, and standalone decorative separators such as `◆◇◆◇◆`.

`Afterword` remains an explicitly excluded section by default, but later recognized sections such as `Bonus Short Stories`, `Character Profiles`, and appendices are preserved. Converter headings such as `[chapter] 0 Prologue` are normalized to Markdown headings.
