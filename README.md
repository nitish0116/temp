# HM Document-to-Audio Toolkit

A collection of Python and PowerShell tools for turning PDF and EPUB documents
into cleaned Markdown, accessible PDFs, narrated audio, and static-image video.
The repository also contains reporting utilities and tests for the complete
conversion workflow.

## Workflow

```text
PDF / EPUB
    |
    v
Markdown extraction (pdf-md)
    |
    v
Text and OCR cleanup (cleanup/markdownCleaner)
    |
    +----------------------+
    |                      |
    v                      v
Accessible PDF         MP3 narration (md-audio)
 (md_python)                    |
                               v
                        YouTube-ready MP4
                          (mp3ToYT)
```

## Repository layout

| Path | Purpose |
|---|---|
| `pdf-md/` | Extract PDF or EPUB content into structured Markdown |
| `cleanup/markdownCleaner/` | Clean OCR artifacts and prepare Markdown for text-to-speech |
| `md-audio/` | Convert Markdown into MP3 or WAV narration using SAPI or Edge TTS |
| `md_python/` | Convert Markdown into accessible, selectable-text PDF |
| `mp3ToYT/` | Turn MP3 narration into a static-image MP4 |
| `stats/` | Generate a Windows HTA folder-size and file statistics report |
| `Library/` | Local source and working library; ignored by Git |
| `lib_to_up/` | External staging area for generated MP3/MP4 files; media is ignored by Git |
| `move-library-media.ps1` | Move generated media from `Library` to `lib_to_up` while preserving paths |

Each major tool has a README in its own directory with its full command-line
reference.

## Requirements

- Python 3.10 or later
- PowerShell on Windows
- `ffmpeg` and `ffprobe` for MP3 and MP4 generation
- A Windows SAPI voice for offline narration, or internet access for Edge TTS

Create a virtual environment from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install `
    -r pdf-md\requirements.txt `
    -r cleanup\markdownCleaner\requirements.txt `
    -r md-audio\requirements.txt `
    -r md_python\requirements.txt
```

Install FFmpeg if it is not already available:

```powershell
winget install ffmpeg
```

## Quick start

The examples below are intended to be run from the repository root with the
virtual environment activated.

### 1. Extract a document

```powershell
$env:PYTHONPATH = "pdf-md"
python -m pdf_to_markdown "Library\book.epub" -o "Library\markdown"
```

PDF input uses the same command. Add `-r` when the input is a directory that
should be searched recursively.

### 2. Clean the extracted Markdown

```powershell
$env:PYTHONPATH = "cleanup"
python -m markdownCleaner.cli "Library\markdown" `
    -o "Library\cleaned" `
    --recursive `
    --continue-on-error
```

The cleaner preserves relative subdirectories and writes detailed per-file and
batch reports.

### 3. Generate narration

Use local Windows voices:

```powershell
python md-audio\md_to_audio.py `
    "Library\cleaned" `
    "Library\audio" `
    --backend sapi
```

Or use Microsoft Edge neural voices:

```powershell
python md-audio\md_to_audio.py `
    "Library\cleaned" `
    "Library\audio" `
    --backend edge `
    --voice Aria `
    --edge-workers 8
```

List the voices available for either backend:

```powershell
python md-audio\md_to_audio.py --list-voices
python md-audio\md_to_audio.py --backend edge --list-voices --all-voices
```

### 4. Create an MP4

```powershell
python mp3ToYT\mp3_to_youtube.py `
    "Library\audio\book.mp3" `
    "Library\video\book.mp4" `
    --image "Library\cover.jpg" `
    --thumbnail "Library\cover.jpg" `
    --resolution 480p
```

The tool can also accept an input directory for batch conversion.

### 5. Move generated media to staging

Preview the operation:

```powershell
.\move-library-media.ps1 -WhatIf
```

Move all MP3 and MP4 files from `Library` into `lib_to_up`, preserving the
directory hierarchy:

```powershell
.\move-library-media.ps1
```

If the destination already contains an identical file, the source duplicate is
removed. A different file at the same destination is reported as a conflict and
is never overwritten.

## Other utilities

Convert cleaned Markdown to PDF:

```powershell
python md_python\md_to_pdf.py `
    "Library\cleaned\book.md" `
    "Library\pdf\book.pdf"
```

Create a folder statistics report:

```powershell
python stats\folder_file_stats.py "Library" -o "folder_summary.hta"
```

## Tests

Run the complete test suite from the repository root:

```powershell
python -m pytest
```

Run one component:

```powershell
python -m pytest md-audio\tests
```

## Storage and Git policy

Large source documents, generated outputs, media, reports, and local working
files are excluded through `.gitignore`. Keep source code and configuration in
Git; keep large assets in external storage such as OneDrive.

Do not use `git add --force` to add ignored media. MP3 and MP4 files are already
compressed and make Git history expensive to clone and maintain.

## Content rights

Only process, store, publish, or share material for which you have the necessary
rights. Creating a derivative audio or video file does not grant distribution
rights to the underlying document.
