#!/usr/bin/env python3
"""
mp3_to_youtube.py
-----------------
Converts a speech/TTS MP3 file into a YouTube-ready MP4 with:
  - Black background video (static frame)
  - Audio resampled to 44100 Hz stereo AAC 128k
  - ID3/MP4 metadata (title, artist, album)
  - Correct format for verified YouTube account uploads (up to 12 hours)

Usage:
    python mp3_to_youtube.py input.mp3
    python mp3_to_youtube.py input.mp3 output.mp4
    python mp3_to_youtube.py input.mp3 --title "My Book" --artist "Author Name" --album "Series"
    python mp3_to_youtube.py input.mp3 --resolution 480p
    python mp3_to_youtube.py input.mp3 --thumbnail cover.jpg

Requirements:
    ffmpeg must be installed and on PATH.
    Windows:  winget install ffmpeg
    macOS:    brew install ffmpeg
    Linux:    sudo apt install ffmpeg
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


# ── Resolution presets ─────────────────────────────────────────────────────
RESOLUTIONS = {
    "720p":  ("1280x720",  28),   # (size, crf)  — default, ~3-5 GB for 10h
    "480p":  ("854x480",   30),   # smaller file, ~1-2 GB for 10h
    "1080p": ("1920x1080", 28),   # larger, unnecessary for static frame
    "360p":  ("640x360",   32),   # smallest, fine for audio-only content
}

DEFAULT_RESOLUTION = "720p"
AUDIO_BITRATE      = "128k"
AUDIO_SAMPLE_RATE  = 44100
AUDIO_CHANNELS     = 2           # stereo


# ── Helpers ────────────────────────────────────────────────────────────────

def check_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ERROR: ffmpeg not found on PATH.")
        print()
        print("Install it with one of:")
        print("  Windows : winget install ffmpeg")
        print("  macOS   : brew install ffmpeg")
        print("  Linux   : sudo apt install ffmpeg")
        sys.exit(1)
    return ffmpeg


def get_ffprobe() -> str | None:
    return shutil.which("ffprobe")


def probe_audio(mp3_path: Path) -> dict:
    """Return basic info about the MP3 using ffprobe."""
    ffprobe = get_ffprobe()
    if not ffprobe:
        return {}
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
        import json
        data = json.loads(result.stdout)
        fmt  = data.get("format", {})
        tags = fmt.get("tags", {})
        streams = data.get("streams", [{}])
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
        duration_s = float(fmt.get("duration", 0))
        return {
            "duration_s":   duration_s,
            "duration_str": fmt_duration(int(duration_s)),
            "size_mb":      int(fmt.get("size", 0)) / (1024 * 1024),
            "sample_rate":  audio.get("sample_rate", "?"),
            "channels":     audio.get("channels", "?"),
            "bit_rate_kbps": int(fmt.get("bit_rate", 0)) // 1000,
            "title":        tags.get("title", ""),
            "artist":       tags.get("artist", ""),
            "album":        tags.get("album", ""),
        }
    except Exception:
        return {}


def fmt_duration(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}h {m:02d}m {s:02d}s"


def fmt_size(path: Path) -> str:
    mb = path.stat().st_size / (1024 * 1024)
    if mb >= 1024:
        return f"{mb/1024:.2f} GB"
    return f"{mb:.1f} MB"


def clean_stem(path: Path) -> str:
    """Generate a clean output filename stem from the input path."""
    stem = path.stem
    stem = re.sub(r"[\[\(].*?[\]\)]", "", stem)
    stem = stem.replace("_", " ")
    stem = re.sub(r"\s+", " ", stem).strip(" -_.")
    return stem or "output"


def guess_metadata(mp3_path: Path, probe: dict) -> tuple[str, str, str]:
    """
    Try to fill in title / artist / album from existing tags or filename.
    Returns (title, artist, album) — any may be empty string.
    """
    title  = probe.get("title", "")
    artist = probe.get("artist", "")
    album  = probe.get("album", "")

    if not title:
        title = clean_stem(mp3_path)

    return title, artist, album


def estimate_output_size(duration_s: float, resolution: str) -> str:
    """Rough estimate of output MP4 size."""
    size_str, crf = RESOLUTIONS.get(resolution, RESOLUTIONS[DEFAULT_RESOLUTION])
    w, h = map(int, size_str.split("x"))
    # Very rough: static black frame compresses incredibly well
    # Empirically: ~720p static black ≈ 0.5 Mbps video + audio
    video_mbps = 0.5 if crf >= 28 else 0.8
    audio_mbps = int(AUDIO_BITRATE.replace("k", "")) / 1000
    total_mb = (video_mbps + audio_mbps) * duration_s / 8
    if total_mb >= 1024:
        return f"~{total_mb/1024:.1f} GB"
    return f"~{total_mb:.0f} MB"


# ── Core conversion ────────────────────────────────────────────────────────

def convert(
    mp3_path: Path,
    output_path: Path,
    title: str,
    artist: str,
    album: str,
    resolution: str,
    thumbnail: Path | None,
) -> None:
    ffmpeg   = check_ffmpeg()
    size_str, crf = RESOLUTIONS.get(resolution, RESOLUTIONS[DEFAULT_RESOLUTION])
    fps      = 1   # 1 fps for a static frame — minimal video data

    print(f"\n{'='*60}")
    print(f"  Input  : {mp3_path.name}  ({fmt_size(mp3_path)})")
    print(f"  Output : {output_path.name}")
    print(f"  Video  : {resolution} ({size_str})  CRF={crf}  {fps}fps static black")
    print(f"  Audio  : AAC {AUDIO_BITRATE}  {AUDIO_SAMPLE_RATE} Hz  stereo")
    if title:  print(f"  Title  : {title}")
    if artist: print(f"  Artist : {artist}")
    if album:  print(f"  Album  : {album}")
    if thumbnail: print(f"  Thumb  : {thumbnail.name}")
    print(f"{'='*60}\n")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [ffmpeg, "-y"]

    # ── Input 0: MP3 audio ──────────────────────────────────────────────
    cmd += ["-i", str(mp3_path)]

    # ── Input 1: black video source ─────────────────────────────────────
    cmd += [
        "-f", "lavfi",
        "-i", f"color=c=black:s={size_str}:r={fps}",
    ]

    # ── Input 2 (optional): thumbnail image ─────────────────────────────
    if thumbnail:
        cmd += ["-i", str(thumbnail)]

    # ── Map streams ─────────────────────────────────────────────────────
    cmd += ["-map", "1:v:0"]    # black video
    cmd += ["-map", "0:a:0"]    # audio from MP3

    # ── Video encoding ───────────────────────────────────────────────────
    cmd += [
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-preset", "ultrafast",
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",   # broadest compatibility
    ]

    # ── Audio encoding ───────────────────────────────────────────────────
    cmd += [
        "-c:a", "aac",
        "-ar",  str(AUDIO_SAMPLE_RATE),
        "-ac",  str(AUDIO_CHANNELS),
        "-b:a", AUDIO_BITRATE,
    ]

    # ── Thumbnail (attached picture) ─────────────────────────────────────
    if thumbnail:
        cmd += [
            "-map", "2:v:0",
            "-c:v:1", "copy",
            "-disposition:v:1", "attached_pic",
        ]

    # ── Metadata ─────────────────────────────────────────────────────────
    if title:  cmd += ["-metadata", f"title={title}"]
    if artist: cmd += ["-metadata", f"artist={artist}"]
    if album:  cmd += ["-metadata", f"album={album}"]

    # ── Sync / output ────────────────────────────────────────────────────
    cmd += ["-shortest"]        # stop when audio ends
    cmd += [str(output_path)]

    print("Running ffmpeg... (this will take a while for long files)")
    started = time.perf_counter()
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"\nERROR: ffmpeg failed with exit code {e.returncode}")
        sys.exit(1)
    elapsed = time.perf_counter() - started

    print(f"\n{'='*60}")
    print(f"  Done in {elapsed/60:.1f} minutes")
    print(f"  Output : {output_path}")
    print(f"  Size   : {fmt_size(output_path)}")
    print(f"{'='*60}")


# ── CLI ────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a TTS/audiobook MP3 to a YouTube-ready MP4.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python mp3_to_youtube.py book.mp3
  python mp3_to_youtube.py book.mp3 out.mp4
  python mp3_to_youtube.py book.mp3 --title "Tanya Vol 1" --artist "Carlo Zen"
  python mp3_to_youtube.py book.mp3 --resolution 480p
  python mp3_to_youtube.py book.mp3 --thumbnail cover.jpg
        """,
    )
    parser.add_argument("input",  help="Input MP3 file path")
    parser.add_argument("output", nargs="?", help="Output MP4 file path (optional)")

    parser.add_argument("--title",  default="", help="Video/track title metadata")
    parser.add_argument("--artist", default="", help="Artist metadata")
    parser.add_argument("--album",  default="", help="Album metadata")

    parser.add_argument(
        "--resolution",
        choices=list(RESOLUTIONS.keys()),
        default=DEFAULT_RESOLUTION,
        help=f"Output video resolution (default: {DEFAULT_RESOLUTION}). "
             "Use 480p to reduce file size.",
    )
    parser.add_argument(
        "--thumbnail",
        default=None,
        help="Optional cover image to embed as thumbnail (JPG or PNG).",
    )
    parser.add_argument(
        "--no-probe",
        action="store_true",
        help="Skip ffprobe analysis (faster start, no pre-flight info).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    mp3_path = Path(args.input).resolve()
    if not mp3_path.exists():
        print(f"ERROR: Input file not found: {mp3_path}")
        return 1
    if mp3_path.suffix.lower() not in (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"):
        print(f"WARNING: Input extension '{mp3_path.suffix}' is unusual — proceeding anyway.")

    # Output path
    if args.output:
        output_path = Path(args.output).resolve()
        if output_path.suffix.lower() != ".mp4":
            output_path = output_path.with_suffix(".mp4")
    else:
        output_path = mp3_path.with_suffix(".mp4")

    # Thumbnail
    thumbnail = None
    if args.thumbnail:
        thumbnail = Path(args.thumbnail).resolve()
        if not thumbnail.exists():
            print(f"WARNING: Thumbnail not found: {thumbnail} — skipping.")
            thumbnail = None

    # Probe input file
    probe = {}
    if not args.no_probe:
        print(f"Analysing input: {mp3_path.name} ...")
        probe = probe_audio(mp3_path)
        if probe:
            print(f"  Duration   : {probe['duration_str']}")
            print(f"  Size       : {probe['size_mb']:.1f} MB")
            print(f"  Sample rate: {probe['sample_rate']} Hz")
            print(f"  Channels   : {probe['channels']}")
            print(f"  Bit rate   : {probe['bit_rate_kbps']} kbps")
            est = estimate_output_size(probe["duration_s"], args.resolution)
            print(f"  Est. output: {est}")

            # Warn if duration may be over YouTube's limit
            if probe["duration_s"] > 6 * 3600:
                print()
                print("  ⚠  Duration > 6 hours.")
                print("     YouTube allows up to 12 hours for VERIFIED accounts.")
                print("     Make sure your account is phone-verified before uploading.")

    # Resolve metadata — CLI args override probed tags
    guessed_title, guessed_artist, guessed_album = guess_metadata(mp3_path, probe)
    title  = args.title  or guessed_title
    artist = args.artist or guessed_artist
    album  = args.album  or guessed_album

    convert(
        mp3_path    = mp3_path,
        output_path = output_path,
        title       = title,
        artist      = artist,
        album       = album,
        resolution  = args.resolution,
        thumbnail   = thumbnail,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
