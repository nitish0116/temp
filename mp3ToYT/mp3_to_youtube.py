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
    python mp3_to_youtube.py input.mp3 --image cover.jpg
    python mp3_to_youtube.py input.mp3 --image cover.jpg --thumbnail cover.jpg

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
    "360p": ("640x360", 33),  # ~1 GB for 10h  — smallest
    "480p": ("854x480", 31),  # ~1.5 GB for 10h — recommended for audiobooks
    "720p": ("1280x720", 29),  # ~2.5 GB for 10h — default
    "1080p": ("1920x1080", 29),  # ~4 GB for 10h
}
DEFAULT_RESOLUTION = "720p"
AUDIO_BITRATE = "128k"
AUDIO_SAMPLE_RATE = 44100
AUDIO_CHANNELS = 2


# ── Helpers ────────────────────────────────────────────────────────────────


def die(msg: str) -> None:
    print(f"\nERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def check_tools() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
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
                ffprobe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(mp3_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        fmt = data.get("format", {})
        tags = fmt.get("tags", {})
        streams = data.get("streams", [])
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
        return {
            "duration_s": float(fmt.get("duration", 0)),
            "size_bytes": int(fmt.get("size", 0)),
            "sample_rate": audio.get("sample_rate", "?"),
            "channels": audio.get("channels", "?"),
            "bit_rate_kbps": int(fmt.get("bit_rate", 0)) // 1000,
            "title": tags.get("title", ""),
            "artist": tags.get("artist", ""),
            "album": tags.get("album", ""),
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
    # Static image/black frame: ~0.2-0.5 Mbps video + 0.128 Mbps audio
    video_mbps = 0.30
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

    files = sorted(
        p for p in path.iterdir() if p.is_file() and p.suffix.lower() in exts
    )
    if not files:
        die(f"No supported audio files found in folder: {path}")
    return files


# ── Core conversion ────────────────────────────────────────────────────────


def convert(
    mp3_path: Path,
    output_path: Path,
    duration_s: float,
    title: str,
    artist: str,
    album: str,
    resolution: str,
    thumbnail: Path | None,
    ffmpeg: str,
    image: Path | None = None,
) -> None:

    size_str, crf = RESOLUTIONS.get(resolution, RESOLUTIONS[DEFAULT_RESOLUTION])
    w, h = size_str.split("x")

    print(f"\n{'='*62}")
    print(f"  Input      : {mp3_path.name}  ({fmt_bytes(mp3_path.stat().st_size)})")
    print(f"  Output     : {output_path.name}")
    print(f"  Duration   : {fmt_duration(duration_s)}")
    video_mode = f"image ({image.name})" if image else "black"
    print(f"  Video      : {resolution} ({size_str})  CRF={crf}  {video_mode}")
    print(f"  Audio      : AAC {AUDIO_BITRATE}  {AUDIO_SAMPLE_RATE} Hz  stereo")
    if title:
        print(f"  Title      : {title}")
    if artist:
        print(f"  Artist     : {artist}")
    if album:
        print(f"  Album      : {album}")
    if image:
        print(f"  Image      : {image.name}  (full-frame background)")
    if thumbnail:
        print(f"  Thumbnail  : {thumbnail.name}  (attached picture)")
    print(f"{'='*62}\n")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Two-pass conversion ────────────────────────────────────────────
    # Pass 1: encode video (image/black background) + audio
    # Pass 2: attach thumbnail as attached_pic stream (separate pass avoids
    #         ffmpeg conflict between -vf filter and stream copy/codec for
    #         the thumbnail when both are in the same command)
    #
    # If no thumbnail requested, a single pass is used.

    # Determine if we need a temp file for two-pass thumbnail attachment
    needs_two_pass = thumbnail is not None
    pass1_path = output_path.with_suffix(".tmp.mp4") if needs_two_pass else output_path

    # ── PASS 1: video + audio ──────────────────────────────────────────
    cmd = [ffmpeg, "-y", "-v", "warning", "-stats"]

    # Input 0: audio
    cmd += ["-i", str(mp3_path)]

    # Input 1: video source
    if image:
        # -loop 1: loop a still image indefinitely.
        # Must be placed BEFORE -i for the image input.
        # -stream_loop -1 does NOT work for image inputs; only -loop 1 does.
        cmd += ["-loop", "1", "-i", str(image)]
    else:
        # Pure black lavfi source at 1 fps — minimum file size
        cmd += [
            "-stream_loop",
            "-1",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={size_str}:r=1",
        ]

    cmd += ["-map", "1:v:0", "-map", "0:a:0"]

    # Video filter: scale+crop image to exact target resolution
    # force_original_aspect_ratio=increase → upscale to cover (no letterbox)
    # crop=W:H                             → centre-crop to exact size
    # setsar=1                             → square pixels for H.264
    # fps=1                                → 1 fps (massive file size reduction
    #                                         for static content — no quality loss)
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1,fps=1"
    )
    cmd += ["-vf", vf]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",  # fastest encode; irrelevant for static frame
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",  # required for broadest player compatibility
    ]
    cmd += [
        "-c:a",
        "aac",
        "-ar",
        str(AUDIO_SAMPLE_RATE),
        "-ac",
        str(AUDIO_CHANNELS),
        "-b:a",
        AUDIO_BITRATE,
    ]
    if title:
        cmd += ["-metadata", f"title={title}"]
    if artist:
        cmd += ["-metadata", f"artist={artist}"]
    if album:
        cmd += ["-metadata", f"album={album}"]
    cmd += ["-t", str(duration_s)]
    cmd += ["-movflags", "+faststart"]
    cmd += [str(pass1_path)]

    print("Pass 1/2: Encoding video + audio..." if needs_two_pass else "Converting...")
    print("(ffmpeg progress shown below)\n")
    started = time.perf_counter()

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        die(f"ffmpeg (pass 1) exited with code {exc.returncode}. See messages above.")

    if not pass1_path.exists() or pass1_path.stat().st_size == 0:
        die(f"ffmpeg produced an empty file at: {pass1_path}")

    # ── PASS 2: attach thumbnail ───────────────────────────────────────
    if thumbnail:
        print("\nPass 2/2: Attaching thumbnail...\n")
        cmd2 = [
            ffmpeg,
            "-y",
            "-v",
            "warning",
            "-i",
            str(pass1_path),  # existing MP4 from pass 1
            "-i",
            str(thumbnail),  # thumbnail image (1 frame, no -loop needed)
            "-map",
            "0",  # all streams from pass 1
            "-map",
            "1:v",  # thumbnail video stream
            "-c",
            "copy",  # copy all pass-1 streams unchanged
            "-c:v:1",
            "png",  # encode thumbnail as PNG (lossless, always works)
            "-disposition:v:1",
            "attached_pic",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        try:
            subprocess.run(cmd2, check=True)
        except subprocess.CalledProcessError as exc:
            die(
                f"ffmpeg (pass 2) exited with code {exc.returncode}. See messages above."
            )
        finally:
            # Remove the pass-1 temp file whether or not pass 2 succeeded
            try:
                pass1_path.unlink()
            except OSError:
                pass

    elapsed = time.perf_counter() - started

    # ── Verify final output ────────────────────────────────────────────
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
  python mp3_to_youtube.py book.mp3 --image cover.jpg
  python mp3_to_youtube.py book.mp3 --image cover.jpg --thumbnail cover.jpg --resolution 480p
        """,
    )
    parser.add_argument(
        "input", help="Input audio file OR folder containing audio files"
    )
    parser.add_argument(
        "output",
        nargs="?",
        help="Output MP4 file path (single input) or output folder (batch)",
    )
    parser.add_argument("--title", default="", help="Title metadata tag")
    parser.add_argument("--artist", default="", help="Artist metadata tag")
    parser.add_argument("--album", default="", help="Album metadata tag")
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
        help="Cover image to embed as MP4 thumbnail/attached picture (JPG or PNG). "
        "Shown by media players in the library view. "
        "Use --image to also show it as the video background.",
    )
    parser.add_argument(
        "--image",
        default=None,
        help="Static image to display as the full-frame video background (JPG or PNG). "
        "Scaled and centre-cropped to fill the chosen resolution. "
        "Use this for cover art, book art, or any still image. "
        "If omitted, a plain black background is used. "
        "Tip: pass the same path to both --image and --thumbnail so the "
        "cover art appears both in the video and in the player library.",
    )
    return parser.parse_args()


def _resolve_outputs(
    input_path: Path, inputs: list[Path], raw_output: Path | None
) -> tuple[bool, Path | None, Path | None]:
    """Resolve either a batch directory or a single MP4 output path."""
    is_batch = len(inputs) > 1 or input_path.is_dir()
    if is_batch:
        output_dir = input_path if raw_output is None else raw_output
        if output_dir.suffix.lower() == ".mp4":
            die("When input is a folder, output must be a directory, not a .mp4 file.")
        output_dir.mkdir(parents=True, exist_ok=True)
        return True, output_dir, None

    if raw_output is None:
        output_path = inputs[0].with_suffix(".mp4")
    elif raw_output.suffix:
        output_path = raw_output.with_suffix(".mp4")
    else:
        raw_output.mkdir(parents=True, exist_ok=True)
        output_path = (raw_output / f"{inputs[0].stem}.mp4").resolve()
    return False, None, output_path


def main() -> int:
    args = parse_args()
    ffmpeg, ffprobe = check_tools()

    input_path = Path(args.input).resolve()
    inputs = collect_audio_inputs(input_path)

    raw_output = Path(args.output).resolve() if args.output else None
    is_batch, output_dir, output_path = _resolve_outputs(input_path, inputs, raw_output)

    # Background image (shown full-frame throughout the video)
    image = None
    if args.image:
        image = Path(args.image).resolve()
        if not image.exists():
            print(f"[WARN] --image file not found: {image} — using black background.")
            image = None

    # Thumbnail (attached picture shown in media player library)
    thumbnail = None
    if args.thumbnail:
        thumbnail = Path(args.thumbnail).resolve()
        if not thumbnail.exists():
            print(f"[WARN] Thumbnail not found: {thumbnail} — skipping.")
            thumbnail = None

    # Convenience: if --image given but no --thumbnail, auto-use image as thumbnail too
    if image and not thumbnail:
        thumbnail = image
        print(
            f"[INFO] Using --image as thumbnail too (pass --thumbnail separately to override)."
        )

    failed = 0
    for src in inputs:
        if is_batch:
            assert output_dir is not None
            dst = (output_dir / f"{src.stem}.mp4").resolve()
        else:
            assert output_path is not None
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
        title = args.title or info.get("title") or clean_stem(src)
        artist = args.artist or info.get("artist", "")
        album = args.album or info.get("album", "")

        try:
            convert(
                mp3_path=src,
                output_path=dst,
                duration_s=duration_s,
                title=title,
                artist=artist,
                album=album,
                resolution=args.resolution,
                thumbnail=thumbnail,
                ffmpeg=ffmpeg,
                image=image,
            )
        except SystemExit:
            failed += 1

    if failed:
        print(f"\nCompleted with {failed} failed file(s).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
