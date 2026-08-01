# md-audio

Convert Markdown files to MP3 or WAV narration with Microsoft Edge TTS or
Windows SAPI. The converter supports single files and non-recursive folder
batches, duration estimates, optional chapter silence for Edge output, and
approximate CUE/YouTube chapter metadata.

Run all examples from the repository root.

## Requirements

- Python 3.10 or later.
- `edge-tts` for the Edge backend (installed from `requirements.txt`).
- Windows and PowerShell for the SAPI backend.
- `ffmpeg` on `PATH` for MP3 encoding and Edge chunk concatenation.
- `ffprobe` on `PATH` for meaningful CUE timestamps.
- Network access while synthesizing with Edge TTS.

Install the Python dependency:

```powershell
python -m pip install -r md-audio\requirements.txt
```

## Basic conversion

List voices before choosing one:

```powershell
# Installed Windows SAPI voices
python md-audio\md_to_audio.py --list-voices

# Recommended Edge voices and aliases
python md-audio\md_to_audio.py --backend edge --list-voices

# Full Edge catalog
python md-audio\md_to_audio.py --backend edge --list-voices --all-voices
```

Create an offline SAPI MP3:

```powershell
python md-audio\md_to_audio.py `
    "Library\cleaned\book.md" `
    "Library\audio\book.mp3" `
    --backend sapi `
    --voice David
```

Create an Edge neural-voice MP3:

```powershell
python md-audio\md_to_audio.py `
    "Library\cleaned\book.md" `
    "Library\audio\book.mp3" `
    --backend edge `
    --voice Aria `
    --edge-workers 8
```

Convert every Markdown file immediately inside a folder:

```powershell
python md-audio\md_to_audio.py `
    "Library\cleaned" `
    "Library\audio" `
    --backend edge `
    --voice Aria
```

Folder conversion is intentionally non-recursive. Output filenames preserve
the source Markdown stem. Edge always writes MP3; SAPI accepts MP3 or WAV for a
single-file target and uses MP3 for folder batches.

## Duration estimation

Estimate playback time without calling a speech backend or creating audio:

```powershell
python md-audio\md_to_audio.py `
    "Library\cleaned\book.md" `
    --estimate-duration `
    --words-per-minute 150
```

A folder input prints one estimate per Markdown file and a total. The public
`estimate_mp3_duration()` function accepts either Markdown text or a
`pathlib.Path` and returns seconds. Estimates count only content that survives
narration preparation; they are not measured audio durations.

## Chapters and cue metadata

For Edge output, `--chapter-markers` inserts silence at recognized chapter or
section boundaries:

```powershell
python md-audio\md_to_audio.py `
    "Library\cleaned\book.md" `
    "Library\audio\book.mp3" `
    --backend edge `
    --chapter-markers `
    --chapter-marker-duration 2.0 `
    --cue-file
```

Recognized conventional headings include chapters, sections, parts, volumes,
acts, scenes, prologues, epilogues, interludes, Roman numerals, and numbered
headings. CUE generation treats every Markdown heading as a scene boundary, so
date and location headings are also included.

`--cue-file` writes both `book.cue` and
`book_youtube_chapters.txt`. Timestamps are estimates distributed by chunk
character count, not exact per-chunk timing. If `ffprobe` is unavailable, the
files can still be written but their timestamps resolve to zero.

SAPI creates one continuous WAV and currently cannot insert accurate silence at
chunk boundaries. Use Edge when chapter silence is required. SAPI still removes
internal chapter markers from spoken text.

## Narration preparation

Before text reaches a speech backend, the reusable narration layer:

- removes Markdown fence markers and heading hashes;
- drops ornament-only separator lines;
- strips configured trailing mixed alphanumeric OCR noise;
- rejoins hard-wrapped prose into paragraphs;
- splits long text at sentence, phrase, or word boundaries; and
- merges fragments too small for a reliable TTS request.

This is narration-specific preparation. It does not modify the source Markdown
file and is not a replacement for `markdownCleaner`.

## Edge hyphen narration review

Edge conversion always loads
`md-audio/library-hyphen-review.json` by default. Only candidates with
`"status": "replace"` and non-empty replacement text are applied. Entries
marked `review` or `genuine` are inert. Replacements occur only at the Edge TTS
boundary; source Markdown remains unchanged.

Override the record for a run with:

```powershell
python md-audio\md_to_audio.py `
    "Library\cleaned\book.md" `
    --backend edge `
    --hyphen-review-json "md-audio\another-review.json"
```

The built-in forms `be-a -> be, a` and `be-an -> be, an` remain available.
Case is preserved for title-case and uppercase tokens.

### Integrated review pipeline

Build all review records with the cached integrated command:

```powershell
python md-audio\build_hyphen_reviews.py
```

Defaults are derived from the repository layout. The command recursively scans
`Library` and writes:

| File | Meaning |
|---|---|
| `library-hyphen-review.json` | Automatically decided genuine and replacement entries loaded by Edge conversion. |
| `library-hyphen-review-ambiguous.json` | Entries that remain unsafe to decide automatically. |
| `library-hyphen-review-cross-evidence.json` | Decisions supported by matching punctuation elsewhere in the library. |
| `.hyphen-review-cache.json.gz` | Portable content-addressed scan cache. |

The cache uses repository-relative paths and SHA-256 content digests. Unchanged
files are read for hashing but are not decoded or regex-scanned. New or modified
files are rescanned; deleted paths are removed. Force a full parse after changing
scan rules:

```powershell
python md-audio\build_hyphen_reviews.py --rebuild-cache
```

Use explicit paths when running against a different library or writing review
artifacts elsewhere:

```powershell
python md-audio\build_hyphen_reviews.py `
    --library "D:\Books\Library" `
    --main-output "md-audio\library-hyphen-review.json" `
    --ambiguous-output "md-audio\library-hyphen-review-ambiguous.json" `
    --evidence-output "md-audio\library-hyphen-review-cross-evidence.json" `
    --cache "md-audio\.hyphen-review-cache.json.gz"
```

The lower-level `audit_hyphens.py` and
`classify_ambiguous_hyphens.py` commands remain available for focused manual
work, but `build_hyphen_reviews.py` is the normal pipeline entry point.

## Important options

| Option | Purpose |
|---|---|
| `--backend {sapi,edge}` | Select the speech backend; default is SAPI. |
| `--voice NAME` | Use an alias or exact installed/catalog voice name. |
| `--edge-workers N` | Maximum concurrent Edge requests; default is 6. |
| `--chunk-size N` | Override automatic chunk sizing; minimum effective value is 400. |
| `--quiet` | Hide step-level progress messages. |
| `--keep-intermediate-wav` | Preserve SAPI's WAV when producing MP3. |
| `--chapter-markers` | Insert chapter silence for Edge output. |
| `--chapter-marker-duration N` | Set Edge chapter silence in seconds; default is 2.0. |
| `--cue-file` | Write approximate CUE and YouTube chapter files. |
| `--estimate-duration` | Estimate playback time and exit. |
| `--words-per-minute N` | Set the estimate rate; default is 150. |

Run `python md-audio\md_to_audio.py --help` for the authoritative CLI
reference.

## Architecture

| Path | Responsibility |
|---|---|
| `md_to_audio.py` | Compatibility CLI, Edge/SAPI integration, subprocess execution, and batch reporting. |
| `md_audio/narration.py` | Pure Markdown preparation, chunking, and duration estimation. |
| `md_audio/paths.py` | Input discovery and shared Edge/SAPI output resolution. |
| `md_audio/cues.py` | Duration allocation plus CUE/YouTube metadata writing. |
| `md_audio/review_records.py` | Shared portable paths, timestamps, and validated review JSON I/O. |
| `audit_hyphens.py` | Initial token audit and conservative classification. |
| `classify_ambiguous_hyphens.py` | Cross-library punctuation-evidence classification. |
| `build_hyphen_reviews.py` | Integrated incremental review pipeline and cache. |

The top-level functions re-exported by `md_to_audio.py` are retained for
compatibility. New backend-independent code should import from `md_audio`.

## Testing

Run the module tests from the repository root:

```powershell
python -m pytest md-audio\tests -q
```

The tests mock speech services and media subprocesses, so they do not synthesize
real audio or require network access.
