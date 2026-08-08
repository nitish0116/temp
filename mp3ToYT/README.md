# MP3 to YouTube-ready MP4

`mp3ToYT` converts audiobook and TTS audio into YouTube-compatible MP4 files.
The generated video uses either a black frame or a supplied cover image, while
audio is encoded as 44.1 kHz stereo AAC. Optional title, artist, album, and
attached-thumbnail metadata are supported.

## Requirements

- Python 3.10 or later
- `ffmpeg` and `ffprobe` available on `PATH`

There are no third-party Python runtime packages. The requirements file is
still provided so repository-wide installation commands can include every
module consistently:

```powershell
python -m pip install -r mp3ToYT\requirements.txt
winget install ffmpeg
```

Restart the terminal after installing FFmpeg so its executables appear on
`PATH`.

## Usage

Run the package from the repository root:

```powershell
python -m mp3ToYT "Library\audio\book.mp3"
```

The original script entry point remains supported:

```powershell
python mp3ToYT\mp3_to_youtube.py "Library\audio\book.mp3"
```

Choose an output file and add metadata:

```powershell
python -m mp3ToYT `
    "Library\audio\book.mp3" `
    "Library\video\book.mp4" `
    --title "Book title" `
    --artist "Author name" `
    --album "Series name" `
    --resolution 480p
```

Use cover artwork as both the full-frame video and attached thumbnail:

```powershell
python -m mp3ToYT `
    "Library\audio\book.mp3" `
    --image "Library\cover.jpg"
```

When `--image` is supplied without `--thumbnail`, the image is automatically
used for both purposes. Pass a separate `--thumbnail` to override it.

## Folder conversion

Pass input and output directories to convert every supported audio file in the
input directory:

```powershell
python -m mp3ToYT "Library\audio" "Library\video" --resolution 480p
```

Folder conversion uses up to four separate worker processes by default. Each
process runs one FFmpeg conversion. Set `--file-workers` from 1 through 4:

```powershell
python -m mp3ToYT "Library\audio" "Library\video" --file-workers 2
```

Use `--file-workers 1` for sequential conversion. Lower values may perform
better when CPU, memory, or disk bandwidth is limited. Output-name collisions
receive deterministic numeric suffixes such as ` (2)`.

Supported input extensions are `.mp3`, `.wav`, `.aac`, `.flac`, `.m4a`, and
`.ogg`. Folder discovery is non-recursive.

## Options

| Option | Description |
|---|---|
| `--resolution 360p|480p|720p|1080p` | Output dimensions; default is `720p` |
| `--image PATH` | Still image shown throughout the video |
| `--thumbnail PATH` | Image attached to the MP4 as cover artwork |
| `--title TEXT` | MP4 title metadata |
| `--artist TEXT` | MP4 artist metadata |
| `--album TEXT` | MP4 album metadata |
| `--file-workers 1..4` | Concurrent folder conversions; default is 4 |

If metadata arguments are omitted, available source tags are retained. The
title falls back to a cleaned version of the audio filename.

## Python API

The package exposes `convert`, `probe`, `collect_audio_inputs`, formatting
helpers, resolution constants, and `main`:

```python
from pathlib import Path
from mp3ToYT import convert

convert(
    mp3_path=Path("book.mp3"),
    output_path=Path("book.mp4"),
    duration_s=3600,
    title="Book title",
    artist="Author",
    album="Series",
    resolution="480p",
    thumbnail=None,
    ffmpeg="ffmpeg",
)
```

Call `probe()` first when the duration is not already known.

## Tests

```powershell
python -m pytest mp3ToYT\tests -q
```

