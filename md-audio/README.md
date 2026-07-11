# md-audio — Markdown to Audio Converter

Convert Markdown files into spoken MP3 or WAV narration using either local Windows voices or Microsoft Edge TTS neural voices.

---

## System Requirements

| Requirement | Details |
|---|---|
| Python | 3.10 or later |
| OS | Windows (SAPI backend requires Windows; Edge backend works anywhere) |
| PowerShell | Required for the SAPI backend; built into Windows 10/11 |
| ffmpeg | Required for MP3 output and Edge TTS chunk concatenation. Can be installed via package manager or manually downloaded. The script provides installation instructions if ffmpeg is missing. |

---

## Installation

1. Clone or copy the `md-audio` folder to your machine.
2. Install the Python dependency:

```powershell
pip install -r requirements.txt
```

3. Install `ffmpeg` using your system package manager:
   - **Windows (winget):** `winget install ffmpeg`
   - **Windows (choco):** `choco install ffmpeg`
   - **macOS (brew):** `brew install ffmpeg`
   - **Linux (apt):** `sudo apt install ffmpeg`
   - **Manual:** Download from https://ffmpeg.org/download.html

> The `edge-tts` package is only required if you use `--backend edge`.
> The default SAPI backend has no external Python dependencies.
>
> If you forget to install ffmpeg, the script will print helpful installation instructions when you try to use MP3 output.

---

## Quick Start

```powershell
# Convert a single Markdown file (SAPI backend, default voice)
python md_to_audio.py "book.md"

# Convert a whole folder
python md_to_audio.py "C:\path\to\folder"

# Use Edge TTS (neural, higher quality)
python md_to_audio.py --backend edge "book.md"

# Increase Edge parallel chunk workers (faster on good network)
python md_to_audio.py --backend edge --voice Aria --edge-workers 8 "book.md"

# Quiet mode (reduced step/progress logging)
python md_to_audio.py --backend edge --voice Aria --quiet "book.md"

# Specify a voice by simple alias
python md_to_audio.py --voice Dave "book.md"
python md_to_audio.py --backend edge --voice Aria "book.md"

# Write output to a specific file
python md_to_audio.py "book.md" "output.mp3"

# Write batch output to a different folder
python md_to_audio.py ".\books" ".\audio-output"
```

---

## Command-line Reference

```
python md_to_audio.py [input_path] [output_path] [options]
```

| Argument | Description |
|---|---|
| `input_path` | Markdown file or folder. Defaults to the only `.md` file in the script directory. |
| `output_path` | Destination `.mp3` or `.wav` file, or output folder for batch conversion. Defaults to beside the input. |
| `--backend sapi` | Use local Windows SAPI voices (default, offline). |
| `--backend edge` | Use Edge TTS neural voices (online, 300+ voices). |
| `--voice NAME` | Voice alias or exact voice name (see Voice Selection below). |
| `--list-voices` | List available voices for the selected backend and exit. |
| `--all-voices` | Show the full Edge voice catalog when listing voices. |
| `--edge-workers N` | Max concurrent Edge chunk requests (Edge backend only, default `6`). |
| `--quiet` | Reduce console output by hiding step-by-step progress logs. |
| `--chapter-markers` | Insert silence markers at chapter/section endings for audiobook navigation. |
| `--chapter-marker-duration SECONDS` | Duration of silence at chapter endings (default `2.0` seconds). |
| `--keep-intermediate-wav` | Keep the temporary WAV when producing MP3 output (SAPI only). |
| `--chunk-size N` | Max characters per speech chunk (default 2500). |

---

## Voice Selection

Run `--list-voices` first to see what's available.

```powershell
# List local Windows voices
python md_to_audio.py --list-voices

# List Edge voices (aliases + recommended)
python md_to_audio.py --backend edge --list-voices

# List all 300+ Edge voices
python md_to_audio.py --backend edge --list-voices --all-voices
```

### SAPI built-in aliases

| Alias | Voice |
|---|---|
| `Dave`, `David` | Microsoft David Desktop (en-US male) |
| `Zira`, `Zee` | Microsoft Zira Desktop (en-US female) |
| `Haruka`, `Japanese` | Microsoft Haruka Desktop (ja-JP female) |

### Edge TTS aliases

| Alias | Voice |
|---|---|
| `Aria` | en-US-AriaNeural |
| `Jenny`, `Zira` | en-US-JennyNeural |
| `Dave`, `David`, `Guy` | en-US-GuyNeural |
| `Haruka`, `Nanami`, `Japanese` | ja-JP-NanamiNeural |

You can also pass any exact voice name from `--list-voices --all-voices` directly to `--voice`.

---

## Output Formats

| Format | SAPI backend | Edge backend |
|---|---|---|
| MP3 | Yes (via ffmpeg, 16 kHz mono 32 kbps) | Yes (native from Edge, 24 kHz mono) |
| WAV | Yes | No |

---

## Performance Tuning (Edge Backend)

The Edge backend now supports parallel chunk narration via `--edge-workers`.

- Higher values usually reduce total conversion time.
- Very high values may be limited by network quality, API throttling, or local CPU.
- Start with `6` to `8`, then adjust if needed.

### Example

```powershell
python md_to_audio.py --backend edge --voice Aria --edge-workers 8 ".\\books" ".\\output"
```

Use `--quiet` when running long batch jobs and you want less console noise:

```powershell
python md_to_audio.py --backend edge --voice Aria --edge-workers 8 --quiet ".\\books" ".\\output"
```

### Measured results on this machine (tiny excerpt benchmark)

| Workers | Time (s) | Output bytes |
|---|---:|---:|
| 1 | 30.25 | 3,116,397 |
| 4 | 9.70 | 3,116,397 |
| 8 | 5.40 | 3,116,397 |

Recommended starting point: `--edge-workers 8`.

---

## Chapter Markers for Audiobook Navigation

Add silence markers at chapter and section endings to help listeners navigate audiobooks.

### How It Works

The script automatically detects chapter headings (Chapter, Section, Part, Volume, Act, etc.) in your Markdown and can insert brief silence gaps at those boundaries. This makes audiobooks easier to pause and resume between chapters.

### Usage

```powershell
# Add chapter markers (default 2 seconds of silence)
python md_to_audio.py --chapter-markers "book.md" "output.mp3"

# Customize marker duration (e.g., 1.5 seconds)
python md_to_audio.py --chapter-markers --chapter-marker-duration 1.5 "book.md" "output.mp3"

# Use with Edge backend
python md_to_audio.py --backend edge --voice Aria --chapter-markers --chapter-marker-duration 2.0 "book.md" "output.mp3"
```

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--chapter-markers` | Flag | Off | Enable chapter ending markers |
| `--chapter-marker-duration` | Float | 2.0 | Silence duration in seconds at chapter endings |

### Supported Heading Formats

The script recognizes the following as chapter/section markers:

- `# Chapter 1`, `# Section 1.2`, `# Part I`
- `# Prologue`, `# Epilogue`, `# Afterword`
- `# Act 1`, `# Scene 1`, `# Episode 1`
- `# Side Story`, `# Interlude`, `# Arc 1`
- `# I`, `# II`, `# III` (Roman numerals)
- `# 1`, `# 2`, `# 3` (Regular numbering)

### Implementation Details

**Edge Backend:** Generates dedicated silence MP3 chunks and inserts them during concatenation.

**SAPI Backend:** Skips chapter markers during synthesis; silence insertion is planned for future versions.

---

## Code Documentation

All functions and methods in `md_to_audio.py` include comprehensive docstrings with:

- **Purpose**: Clear description of what the function does
- **Parameters**: Detailed explanation of each parameter and its type
- **Return values**: Description of return types and values
- **Exceptions**: Any exceptions that may be raised
- **Usage context**: When and how the function is used

To view docstrings while working with the code:

```python
import md_to_audio
help(md_to_audio.log_step)
help(md_to_audio.convert_one_edge)
help(md_to_audio.parse_args)
```

Or access them via the Python REPL or IDE's documentation viewer.

---

## Files

| File | Purpose |
|---|---|
| `md_to_audio.py` | Main script — handles both SAPI and Edge backends |
| `requirements.txt` | Python package dependencies |
| `books/` | Source Markdown files |
| `output/` | Generated audio files |

---

## What Gets Cleaned Before Narration

The converter pre-processes Markdown to remove common ebook-export artifacts before passing text to the speech engine:

- Markdown code-fence markers (`` ``` `` and `~~~`)
- Markdown heading hashes (`#`, `##`, …)
- Decorative Unicode ornament characters (scene-break glyphs, box-drawing, etc.)
- OCR noise tokens (mixed alphanumeric junk from scanned pages)
- Hard line-wrapping is rejoined into flowing paragraphs

---

## Examples

```powershell
# Single file, default voice (David, SAPI)
python md_to_audio.py "The Unwanted Undead Adventurer - Volume 04.md"

# Single file, female Edge voice
python md_to_audio.py --backend edge --voice Jenny "Volume 04.md"

# Whole folder with Edge Aria voice, output to different folder
python md_to_audio.py --backend edge --voice Aria ".\books" ".\audio"

# WAV output (SAPI only)
python md_to_audio.py "Volume 04.md" "Volume 04.wav"

# Check what voices are on this machine
python md_to_audio.py --list-voices
python md_to_audio.py --backend edge --list-voices
```
