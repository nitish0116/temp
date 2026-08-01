# Running the Repository Tools

This is the common command reference for the runnable modules and scripts in
this repository. Run the examples from the repository root. Commands use
repository-relative paths so they work in any clone.

## Start a PowerShell session

```powershell
Set-Location "C:\path\to\repository"
& ".\.venv\Scripts\Activate.ps1"
python -c "import sys; print(sys.executable)"
```

The final command should print the Python executable inside your environment,
for example:

```text
C:\path\to\repository\.venv\Scripts\python.exe
```

Install the repository dependencies if needed:

```powershell
python -m pip install `
    -r pdf-md\requirements.txt `
    -r cleanup\markdownCleaner\requirements.txt `
    -r md-audio\requirements.txt `
    -r md_python\requirements.txt
```

MP3 and MP4 generation also requires `ffmpeg` and `ffprobe` on `PATH`.

## 1. PDF or EPUB to Markdown

Package: `pdf-md\pdf_to_markdown`

Set the package parent on `PYTHONPATH`, then run the package with `-m`:

```powershell
$env:PYTHONPATH = "$PWD\pdf-md"
python -m pdf_to_markdown `
    "Library\book.pdf" `
    -o "Library\markdown"
```

EPUB input uses the same command:

```powershell
$env:PYTHONPATH = "$PWD\pdf-md"
python -m pdf_to_markdown `
    "Library\book.epub" `
    -o "Library\markdown"
```

Process a directory recursively:

```powershell
$env:PYTHONPATH = "$PWD\pdf-md"
python -m pdf_to_markdown `
    "Library" `
    -o "Library\markdown" `
    --recursive `
    --continue-on-error
```

Use smart layout and OCR for difficult or scanned PDFs:

```powershell
$env:PYTHONPATH = "$PWD\pdf-md"
python -m pdf_to_markdown `
    "Library\scanned-book.pdf" `
    -o "Library\markdown" `
    --layout smart `
    --ocr auto `
    --ocr-language eng
```

Useful options include `--pages 1-10,15`, `--images write`,
`--keep-header`, `--keep-footer`, and `--quiet`.

Do not execute `pdf-md\pdf_to_markdown\__main__.py` directly. The supported
form is `python -m pdf_to_markdown`.

## 2. Clean extracted Markdown

Package: `cleanup\markdownCleaner`

Canonical package command:

```powershell
$env:PYTHONPATH = "$PWD\cleanup"
python -m markdownCleaner `
    "Library\markdown\book.md" `
    -o "Library\cleaned"
```

Process a directory recursively:

```powershell
$env:PYTHONPATH = "$PWD\cleanup"
python -m markdownCleaner `
    "Library\markdown" `
    -o "Library\cleaned" `
    --recursive `
    --continue-on-error
```

Use a custom configuration:

```powershell
$env:PYTHONPATH = "$PWD\cleanup"
python -m markdownCleaner `
    "Library\markdown\book.md" `
    -o "Library\cleaned" `
    --config "cleanup\markdownCleaner\config.yaml"
```

Review dictionary and glossary candidates:

```powershell
$env:PYTHONPATH = "$PWD\cleanup"
python -m markdownCleaner --approve-words "Arthur Leywin" "Ivsaar"
python -m markdownCleaner --learn-words "sitrep" "noncoms"
python -m markdownCleaner --reject-words "candidateToSuppress"
python -m markdownCleaner `
    --simplify-candidates "Library\cleaned\reports\glossary_candidates.json"
```

Compatibility entry points are available, but the canonical form above is
preferred:

```powershell
$env:PYTHONPATH = "$PWD\cleanup"
python -m markdownCleaner.cli --help
python -m markdownCleaner.pipeline --help
python cleanup\markdownCleaner\runner.py --help
```

Files beneath `cleanup\markdownCleaner\modules` and `commands` are internal
implementation modules and are not standalone CLI programs.

## 3. Markdown to audio

Entry point: `md-audio\md_to_audio.py`

Install its Python dependency if it was not installed with the combined command
above:

```powershell
python -m pip install -r md-audio\requirements.txt
```

List local Windows voices:

```powershell
python md-audio\md_to_audio.py --list-voices
```

Create an MP3 with the offline Windows SAPI backend:

```powershell
python md-audio\md_to_audio.py `
    "Library\cleaned\book.md" `
    "Library\audio\book.mp3" `
    --backend sapi `
    --voice David
```

Use an online Edge neural voice:

```powershell
python md-audio\md_to_audio.py `
    "Library\cleaned\book.md" `
    "Library\audio\book.mp3" `
    --backend edge `
    --voice Aria `
    --edge-workers 8 `
    --chapter-markers `
    --cue-file
```

`--chapter-markers` inserts silence only with the Edge backend. SAPI produces
one continuous WAV and does not expose the chunk timings needed for accurate
silence insertion. `--cue-file` works with either backend and writes a `.cue`
file plus a `_youtube_chapters.txt` file; timestamps are approximate and need
`ffprobe` on `PATH`.

Convert a directory:

```powershell
python md-audio\md_to_audio.py `
    "Library\cleaned" `
    "Library\audio" `
    --backend edge `
    --voice Aria `
    --edge-workers 8
```

Estimate duration without generating audio:

```powershell
python md-audio\md_to_audio.py `
    "Library\cleaned\book.md" `
    --estimate-duration `
    --words-per-minute 150
```

The estimate is based on speakable words after narration preparation. It is not
a measurement of generated audio. Folder inputs print one result per immediate
`.md` child and a total; audio folder conversion is also non-recursive.

### Edge hyphen-review pipeline

Edge conversion automatically loads
`md-audio\library-hyphen-review.json`. Only entries with `status: "replace"`
are applied at narration time; the Markdown source is never changed. Override
the record for one run with `--hyphen-review-json`.

Regenerate the decided, ambiguous, and cross-evidence review records with the
integrated pipeline:

```powershell
python md-audio\build_hyphen_reviews.py
```

The default pipeline recursively scans `Library` and incrementally updates the
portable content-addressed cache at
`md-audio\.hyphen-review-cache.json.gz`. Unchanged files are hashed but are not
decoded or regex-scanned. Force a full rescan after changing extraction rules:

```powershell
python md-audio\build_hyphen_reviews.py --rebuild-cache
```

Run it against another library with explicit repository-relative or absolute
paths:

```powershell
python md-audio\build_hyphen_reviews.py `
    --library "Library" `
    --main-output "md-audio\library-hyphen-review.json" `
    --ambiguous-output "md-audio\library-hyphen-review-ambiguous.json" `
    --evidence-output "md-audio\library-hyphen-review-cross-evidence.json" `
    --cache "md-audio\.hyphen-review-cache.json.gz"
```

`audit_hyphens.py` and `classify_ambiguous_hyphens.py` are lower-level tools
for focused manual work. The normal entry point is
`build_hyphen_reviews.py`. See `md-audio\README.md` for their record formats,
the refactored package layout, and narration-preparation details.

## 4. Markdown to accessible PDF

Script: `md_python\md_to_pdf.py`

```powershell
python md_python\md_to_pdf.py `
    "Library\cleaned\book.md" `
    "Library\pdf\book.pdf"
```

Display its CLI reference:

```powershell
python md_python\md_to_pdf.py --help
```

The `--all` option processes Markdown files located beside the script, not an
arbitrary input directory:

```powershell
python md_python\md_to_pdf.py --all
```

## 5. MP3 to YouTube-ready MP4

Script: `mp3ToYT\mp3_to_youtube.py`

Create a static-image video:

```powershell
python mp3ToYT\mp3_to_youtube.py `
    "Library\audio\book.mp3" `
    "Library\video\book.mp4" `
    --image "Library\cover.jpg" `
    --thumbnail "Library\cover.jpg" `
    --resolution 480p `
    --title "Book title" `
    --artist "Author name"
```

Batch-process an audio directory:

```powershell
python mp3ToYT\mp3_to_youtube.py `
    "Library\audio" `
    "Library\video" `
    --resolution 480p
```

If `--image` is omitted, the generated video uses a black background.

## 6. Folder statistics report

Script: `stats\folder_file_stats.py`

Create a Windows HTA report:

```powershell
python stats\folder_file_stats.py `
    "Library" `
    -o "folder_summary.hta"
```

Enable its average-size filter:

```powershell
python stats\folder_file_stats.py `
    "Library" `
    -o "folder_summary.hta" `
    --skip-below-master-avg
```

Known issue: `python stats\folder_file_stats.py --help` currently raises a
formatting error because a help string contains an unescaped percent sign.
Normal report generation is unaffected.

## 7. Generate or verify the media manifest

Script: `misc\generate_media_manifest.py`

Generate the manifest without hashing media contents:

```powershell
python misc\generate_media_manifest.py `
    "D:\OneDrive\Library" `
    -o "media-manifest.json" `
    --root-label "OneDrive/Library"
```

Generate SHA-256 hashes as well:

```powershell
python misc\generate_media_manifest.py `
    "D:\OneDrive\Library" `
    -o "media-manifest.json" `
    --root-label "OneDrive/Library" `
    --sha256
```

Check whether an existing manifest is current:

```powershell
python misc\generate_media_manifest.py `
    "D:\OneDrive\Library" `
    -o "media-manifest.json" `
    --root-label "OneDrive/Library" `
    --check
```

## 8. Move generated media to staging

Script: `move-library-media.ps1`

Always preview first:

```powershell
.\move-library-media.ps1 -WhatIf
```

Move MP3 and MP4 files from the default `Library` directory to `lib_to_up`:

```powershell
.\move-library-media.ps1
```

Use explicit directories:

```powershell
.\move-library-media.ps1 `
    -Source "D:\Media\Library" `
    -Destination "D:\Media\Upload"
```

The script preserves relative paths, removes a source duplicate only when its
destination is byte-identical, and leaves conflicting files untouched.

## 9. Run tests

Run the complete suite:

```powershell
python -m pytest
```

Run individual components:

```powershell
python -m pytest pdf-md\tests -q
python -m pytest cleanup\markdownCleaner\tests -q
python -m pytest md-audio\tests -q
python -m pytest md_python\tests -q
python -m pytest mp3ToYT\tests -q
```

Run coverage using the repository configuration:

```powershell
python -m pytest --cov
```

`conftest.py` is pytest configuration and is not run directly.

## 10. Library-specific legacy scripts

These scripts are runnable Python files, but they currently contain hard-coded
paths for `C:\Users\z005537p\...`. Do not run them until those constants are
updated for the current repository.

### Hell Mode heading repair

Script: `Library\hell mode\hellmode.py`

It modifies selected AudioPrep Markdown files in place and writes
`HEADING_FIX.log`. After correcting `CHAPTER_DETAILS`, `AUDIO_PREP_DIR`, and
`TARGET_VOLUMES` in the script, run:

```powershell
python "Library\hell mode\hellmode.py"
```

### Tanya cleaner and chapter splitter

Script: `Library\Tanya\tanya.py`

It reads and writes files beneath its configured `CLEANUP_FOLDER`. After
correcting `CLEANUP_FOLDER` and confirming its output locations, run:

```powershell
python "Library\Tanya\tanya.py"
```

## Optional: Microsoft MarkItDown

MarkItDown is installed in the shared environment but is an external tool, not
a repository module. Convert a document locally with:

```powershell
markitdown "Library\book.pdf" -o "Library\markdown\book-markitdown.md"
```

Using the local command does not require Codex or the MarkItDown MCP server.

## Complete workflow example

```powershell
# 1. Extract
$env:PYTHONPATH = "$PWD\pdf-md"
python -m pdf_to_markdown `
    "Library\book.pdf" `
    -o "Library\markdown"

# 2. Clean
$env:PYTHONPATH = "$PWD\cleanup"
python -m markdownCleaner `
    "Library\markdown\book.md" `
    -o "Library\cleaned"

# 3. Narrate
python md-audio\md_to_audio.py `
    "Library\cleaned\book - Cleaned.md" `
    "Library\audio\book.mp3" `
    --backend edge `
    --voice Aria `
    --edge-workers 8 `
    --chapter-markers `
    --cue-file

# 4. Make video
python mp3ToYT\mp3_to_youtube.py `
    "Library\audio\book.mp3" `
    "Library\video\book.mp4" `
    --image "Library\cover.jpg" `
    --thumbnail "Library\cover.jpg" `
    --resolution 480p
```
