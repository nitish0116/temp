---
name: markdown-audio
description: 'Create WAV or MP3 narration from Markdown files. Use when a user asks to turn a .md file, ebook export, chapter file, or novel markdown into an audio file, audiobook, spoken narration, MP3, or WAV. Supports local Windows SAPI voices and Edge TTS neural voices.'
argument-hint: '[input markdown file or folder] [optional output audio path or output folder] [--backend sapi|edge] [--voice name] [--list-voices]'
user-invocable: true
---

# Markdown Audio

## When to Use
- Convert a Markdown file into spoken audio.
- Convert an entire folder of Markdown files in one run.
- Produce a smaller MP3 instead of a large WAV.
- Switch between local Windows voices and Edge TTS neural voices.
- List the voices available for the selected backend.
- Handle ebook-export Markdown that still contains fence markers, ornament lines, or hard-wrapped paragraphs.

## Requirements
- Windows PowerShell with `System.Speech` available.
- `ffmpeg` on `PATH` for MP3 output.
- A Markdown file encoded as UTF-8.
- For `--backend edge`, the project Python environment must have `edge-tts` installed.

## Procedure
1. Identify whether the input is a single Markdown file or a folder containing `.md` files.
2. Choose a backend: `sapi` for local Windows voices or `edge` for neural Edge TTS voices.
3. Use `--list-voices` with the chosen backend to inspect available voices when needed.
4. Run [the Python converter](./scripts/run_markdown_audio.py) with the input path, optional output path, and any backend or voice flags.
5. For a single file, an optional second path is the destination audio file; if omitted, the converter writes an `.mp3` beside the input file.
6. For a folder input, an optional second path must be an output folder; if omitted, the converter writes one `.mp3` per `.md` file into the input folder.
7. If the user wants lossless output for a single file, use the default `sapi` backend and pass a `.wav` output path.
8. Report the final file paths and resulting file sizes.

## Examples
- Single file: `python .\md-audio\md_to_audio.py ".\md-audio\book.md"`
- Single file with explicit MP3: `python .\md-audio\md_to_audio.py ".\md-audio\book.md" ".\md-audio\book.mp3"`
- Whole folder in place: `python .\md-audio\md_to_audio.py ".\md-audio"`
- Whole folder to another folder: `python .\md-audio\md_to_audio.py ".\md-audio" ".\output-audio"`
- List local Windows voices: `python .\md-audio\md_to_audio.py --list-voices`
- List Edge voices: `python .\md-audio\md_to_audio.py --backend edge --list-voices`
- Use a local alias voice: `python .\md-audio\md_to_audio.py --voice Dave ".\md-audio\book.md"`
- Use an Edge neural voice: `python .\md-audio\md_to_audio.py --backend edge --voice Aria ".\md-audio\book.md"`

## Notes
- The default backend is local Windows `sapi`; use `--backend edge` for the separate Edge TTS path.
- The default output is MP3 at 16 kHz mono / 32 kbps for `sapi`; Edge output stays MP3 and uses Edge TTS defaults.
- Relative output paths are resolved beside the input Markdown file, or under the chosen output folder for batch runs.
- The converter strips common ebook artifacts before narration so the speech engine reads the prose cleanly.
- The skill wrapper prefers the workspace `.venv` Python when present so Edge TTS dependencies can be resolved.