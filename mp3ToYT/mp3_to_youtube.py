#!/usr/bin/env python3
"""
mp3_to_youtube.py
-----------------
Converts a speech/TTS MP3 into a YouTube-ready MP4 with:
  - Black background video (static frame, looped to match audio length)
  - Audio resampled to 44100 Hz stereo AAC 128k
  - MP4 metadata (title, artist, album)
  - -movflags +faststart for YouTube streaming optimisation

Usage:
    python mp3_to_youtube.py input.mp3
    python mp3_to_youtube.py input.mp3 output.mp4
    python mp3_to_youtube.py input.mp3 --title "My Book" --artist "Author" --album "Series"
    python mp3_to_youtube.py input.mp3 --resolution 480p
    python mp3_to_youtube.py input.mp3 --thumbnail cover.jpg

Requirements:
    ffmpeg + ffprobe must be installed and on PATH.
    Windows : winget install ffmpeg
    macOS   : brew install ffmpeg
    Linux   : sudo apt install ffmpeg
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


# ── Resolution presets ─────────────────────────────────────────────────────
RESOLUTIONS = {
    "360p":  ("640x360",   33),   # ~1 GB for 10h  — smallest
    "480p":  ("854x480",   31),   # ~1.5 GB for 10h — recommended for audiobooks
    "720p":  ("1280x720",  29),   # ~2.5 GB for 10h — default
    "1080p": ("1920x1080", 29),   # ~4 GB for 10h
}
DEFAULT_RESOLUTION = "720p"
AUDIO_BITRATE      = "128k"
AUDIO_SAMPLE_RATE  = 44100
AUDIO_CHANNELS     = 2


# ── Helpers ────────────────────────────────────────────────────────────────

def die(msg: str) -> None:
    print(f"\nERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def check_tools() -> tuple[str, str]:
    ffmpeg  = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg:
        die(
            "ffmpeg not found on PATH.\n"
            "  Windows : winget install ffmpeg\n"
            "  macOS   : brew install ffmpeg\n"
            "  Linux   : sudo apt install ffmpeg\n"
            "  After installing, restart your terminal."
        )
    if not ffprobe:
        die("ffprobe not found. It is bundled with ffmpeg — reinstall ffmpeg.")
    return ffmpeg, ffprobe


def probe(mp3_path: Path, ffprobe: str) -> dict:
    """Return audio metadata via ffprobe."""
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-print_format", "json",
                "-show_format", "-show_streams",
                str(mp3_path),
            ],
            capture_output=True, text=True, check=True,
        )
        data    = json.loads(result.stdout)
        fmt     = data.get("format", {})
        tags    = fmt.get("tags", {})
        streams = data.get("streams", [])
        audio   = next((s for s in streams if s.get("codec_type") == "audio"), {})
        return {
            "duration_s":    float(fmt.get("duration", 0)),
            "size_bytes":    int(fmt.get("size", 0)),
            "sample_rate":   audio.get("sample_rate", "?"),
            "channels":      audio.get("channels", "?"),
            "bit_rate_kbps": int(fmt.get("bit_rate", 0)) // 1000,
            "title":         tags.get("title", ""),
            "artist":        tags.get("artist", ""),
            "album":         tags.get("album", ""),
        }
    except Exception as exc:
        print(f"  [WARN] ffprobe failed: {exc} — proceeding without audio info.")
        return {}


def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    return f"{s//3600}h {(s%3600)//60:02d}m {s%60:02d}s"


def fmt_bytes(n: int) -> str:
    if n >= 1024 * 1024 * 1024:
        return f"{n / (1024**3):.2f} GB"
    elif n >= 1024 * 1024:
        return f"{n / (1024**2):.1f} MB"
    elif n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} bytes"


def clean_stem(path: Path) -> str:
    stem = re.sub(r"[\[\(].*?[\]\)]", "", path.stem)
    stem = stem.replace("_", " ")
    return re.sub(r"\s+", " ", stem).strip(" -_.") or "output"


def estimate_size(duration_s: float, resolution: str) -> str:
    # Static black frame compresses to ~0.1-0.3 Mbps with CRF encoding
    # Audio at 128k = 0.128 Mbps
    video_mbps = 0.20
    audio_mbps = 0.128
    mb = (video_mbps + audio_mbps) * duration_s / 8
    return f"~{mb/1024:.1f} GB" if mb >= 1024 else f"~{mb:.0f} MB"


def collect_audio_inputs(path: Path) -> list[Path]:
    """Collect input audio files from a file path or folder path."""
    exts = {".mp3", ".wav", ".aac", ".flac", ".m4a", ".ogg"}
    if path.is_file():
        return [path]
    if not path.is_dir():
        die(f"Input path not found: {path}")

    files = sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() in exts)
    if not files:
        die(f"No supported audio files found in folder: {path}")
    return files


# ── Core conversion ────────────────────────────────────────────────────────

def convert(
    mp3_path:    Path,
    output_path: Path,
    duration_s:  float,
    title:       str,
    artist:      str,
    album:       str,
    resolution:  str,
    thumbnail:   Path | None,
    ffmpeg:      str,
) -> None:

    size_str, crf = RESOLUTIONS.get(resolution, RESOLUTIONS[DEFAULT_RESOLUTION])

    print(f"\n{'='*62}")
    print(f"  Input      : {mp3_path.name}  ({fmt_bytes(mp3_path.stat().st_size)})")
    print(f"  Output     : {output_path.name}")
    print(f"  Duration   : {fmt_duration(duration_s)}")
    print(f"  Video      : {resolution} ({size_str})  CRF={crf}  1fps black")
    print(f"  Audio      : AAC {AUDIO_BITRATE}  {AUDIO_SAMPLE_RATE} Hz  stereo")
    if title:     print(f"  Title      : {title}")
    if artist:    print(f"  Artist     : {artist}")
    if album:     print(f"  Album      : {album}")
    if thumbnail: print(f"  Thumbnail  : {thumbnail.name}")
    print(f"{'='*62}\n")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Build ffmpeg command ───────────────────────────────────────────
    cmd = [ffmpeg, "-y", "-v", "warning", "-stats"]

    # Input 0: MP3 audio
    cmd += ["-i", str(mp3_path)]

    # Input 1: black video — loop indefinitely, stopped by -t
    # -stream_loop -1  : loop this input forever
    # color filter     : pure black frame at 1 fps
    cmd += [
        "-stream_loop", "-1",
        "-f", "lavfi",
        "-i", f"color=c=black:s={size_str}:r=1",
    ]

    # Input 2 (optional): thumbnail image
    if thumbnail:
        cmd += ["-i", str(thumbnail)]

    # ── Stream mapping ─────────────────────────────────────────────────
    # Map video from lavfi (input 1), audio from MP3 (input 0)
    cmd += ["-map", "1:v:0"]
    cmd += ["-map", "0:a:0"]

    # ── Video codec ────────────────────────────────────────────────────
    cmd += [
        "-c:v", "libx264",
        "-preset", "ultrafast",   # fastest encode; fine for static black
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",    # broadest player/browser compatibility
    ]

    # ── Audio codec ────────────────────────────────────────────────────
    cmd += [
        "-c:a", "aac",
        "-ar",  str(AUDIO_SAMPLE_RATE),
        "-ac",  str(AUDIO_CHANNELS),
        "-b:a", AUDIO_BITRATE,
    ]

    # ── Thumbnail (attached picture stream) ────────────────────────────
    if thumbnail:
        cmd += [
            "-map", "2:v:0",
            "-c:v:1", "copy",
            "-disposition:v:1", "attached_pic",
        ]

    # ── Metadata ───────────────────────────────────────────────────────
    if title:  cmd += ["-metadata", f"title={title}"]
    if artist: cmd += ["-metadata", f"artist={artist}"]
    if album:  cmd += ["-metadata", f"album={album}"]

    # ── Duration + output flags ────────────────────────────────────────
    # Use explicit -t so ffmpeg knows exactly when to stop.
    # Do NOT use -shortest with lavfi loop — it can exit at frame 0.
    cmd += ["-t", str(duration_s)]
    cmd += ["-movflags", "+faststart"]   # moov atom at front for YT streaming
    cmd += [str(output_path)]

    # ── Run ───────────────────────────────────────────────────────────
    print("Converting... (this will take a few minutes for long files)")
    print("(ffmpeg progress shown below)\n")
    started = time.perf_counter()

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        die(f"ffmpeg exited with code {exc.returncode}. See messages above.")

    elapsed = time.perf_counter() - started

    # ── Verify output ─────────────────────────────────────────────────
    if not output_path.exists() or output_path.stat().st_size == 0:
        die(
            "ffmpeg finished but the output file is empty or missing.\n"
            "Check the ffmpeg messages above for clues.\n"
            f"Expected output at: {output_path}"
        )

    out_size = output_path.stat().st_size
    print(f"\n{'='*62}")
    print(f"  Done in   : {elapsed/60:.1f} minutes")
    print(f"  Output    : {output_path}")
    print(f"  Size      : {fmt_bytes(out_size)}")
    print(f"{'='*62}")
    print()
    print("YouTube upload checklist:")
    print("  ✓ Make sure your YouTube account is phone-VERIFIED")
    print("    (unverified accounts are capped at 15 min uploads)")
    print("  ✓ Go to studio.youtube.com → Upload → select the MP4")
    print("  ✓ Set visibility to Public/Unlisted as preferred")


# ── CLI ────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a TTS/audiobook MP3 to a YouTube-ready MP4.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python mp3_to_youtube.py book.mp3
  python mp3_to_youtube.py book.mp3 out.mp4
    python mp3_to_youtube.py ./audiobooks ./output
  python mp3_to_youtube.py book.mp3 --title "Tanya Vol 1" --artist "Carlo Zen"
  python mp3_to_youtube.py book.mp3 --resolution 480p
  python mp3_to_youtube.py book.mp3 --thumbnail cover.jpg --resolution 480p
        """,
    )
    parser.add_argument("input",  help="Input audio file OR folder containing audio files")
    parser.add_argument("output", nargs="?", help="Output MP4 file path (single input) or output folder (batch)")
    parser.add_argument("--title",      default="", help="Title metadata tag")
    parser.add_argument("--artist",     default="", help="Artist metadata tag")
    parser.add_argument("--album",      default="", help="Album metadata tag")
    parser.add_argument(
        "--resolution",
        choices=list(RESOLUTIONS.keys()),
        default=DEFAULT_RESOLUTION,
        help=f"Output resolution (default: {DEFAULT_RESOLUTION}). "
             "Use 480p for smaller files — fine for audio-only content.",
    )
    parser.add_argument(
        "--thumbnail",
        default=None,
        help="Cover image to embed as MP4 thumbnail (JPG or PNG).",
    )
    return parser.parse_args()


def main() -> int:
    args    = parse_args()
    ffmpeg, ffprobe = check_tools()

    input_path = Path(args.input).resolve()
    inputs = collect_audio_inputs(input_path)

    raw_output = Path(args.output).resolve() if args.output else None
    is_batch = len(inputs) > 1 or input_path.is_dir()

    if is_batch:
        if raw_output is None:
            output_dir = input_path
        else:
            if raw_output.suffix.lower() == ".mp4":
                die("When input is a folder, output must be a directory, not a .mp4 file.")
            output_dir = raw_output
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        if raw_output is None:
            output_path = inputs[0].with_suffix(".mp4")
        elif raw_output.suffix:
            output_path = raw_output.with_suffix(".mp4")
        else:
            raw_output.mkdir(parents=True, exist_ok=True)
            output_path = (raw_output / f"{inputs[0].stem}.mp4").resolve()

    # Thumbnail (applies to all files)
    thumbnail = None
    if args.thumbnail:
        thumbnail = Path(args.thumbnail).resolve()
        if not thumbnail.exists():
            print(f"[WARN] Thumbnail not found: {thumbnail} — skipping.")
            thumbnail = None

    failed = 0
    for src in inputs:
        if is_batch:
            dst = (output_dir / f"{src.stem}.mp4").resolve()
        else:
            dst = output_path

        print(f"Analysing: {src.name} ...")
        info = probe(src, ffprobe)
        if not info or info.get("duration_s", 0) == 0:
            print("[WARN] Could not determine duration; skipping file.")
            failed += 1
            continue

        duration_s = info["duration_s"]
        print(f"  Duration   : {fmt_duration(duration_s)}")
        print(f"  Size       : {fmt_bytes(info['size_bytes'])}")
        print(f"  Sample rate: {info['sample_rate']} Hz")
        print(f"  Channels   : {info['channels']}")
        print(f"  Bit rate   : {info['bit_rate_kbps']} kbps")
        print(f"  Est. output: {estimate_size(duration_s, args.resolution)}")

        if duration_s > 6 * 3600:
            print()
            print(f"  ⚠  Duration > 6 hours ({fmt_duration(duration_s)}).")
            print("     YouTube allows up to 12 hours for VERIFIED accounts.")

        # CLI args are applied uniformly across all files; missing values fall back per file.
        title  = args.title  or info.get("title")  or clean_stem(src)
        artist = args.artist or info.get("artist", "")
        album  = args.album  or info.get("album",  "")

        try:
            convert(
                mp3_path    = src,
                output_path = dst,
                duration_s  = duration_s,
                title       = title,
                artist      = artist,
                album       = album,
                resolution  = args.resolution,
                thumbnail   = thumbnail,
                ffmpeg      = ffmpeg,
            )
        except SystemExit:
            failed += 1

    if failed:
        print(f"\nCompleted with {failed} failed file(s).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())