"""Resumable FFmpeg segment rendering and final mux."""
from __future__ import annotations
import json, subprocess
from pathlib import Path
from .artifacts import sha256_file

def _motion_filter(motion: str, frames: int) -> str:
    base = ("split=2[background][subject];"
            "[background]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,gblur=sigma=35[blurred];"
            "[subject]scale=1728:972:force_original_aspect_ratio=decrease[contained];"
            "[blurred][contained]overlay=(W-w)/2:(H-h)/2,setsar=1")
    if motion == "slow_pan": x, z = f"(iw-iw/zoom)*on/{max(1, frames)}", "1.02"
    elif motion == "slow_pull": x, z = "(iw-iw/zoom)/2", "max(1.0,1.02-on*0.00006)"
    elif motion == "static_hold": x, z = "(iw-iw/zoom)/2", "1.0"
    else: x, z = "(iw-iw/zoom)/2", "min(1.02,1+on*0.00006)"
    return f"{base},zoompan=z='{z}':x='{x}':y='(ih-ih/zoom)/2':d={frames}:s=1920x1080:fps=30,format=yuv420p"

def render_video(timeline: dict, root: Path, executable: str = "ffmpeg") -> dict:
    segment_dir = root / "renders" / "previews" / "segments"; segment_dir.mkdir(parents=True, exist_ok=True)
    segments = []
    for item in timeline["intervals"]:
        duration = float(item["end_seconds"]) - float(item["start_seconds"]); frames = max(1, round(duration * 30))
        output = segment_dir / f"{item['shot_id']}.mp4"
        command = [executable, "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-i", str(root / item["image"]),
            "-vf", _motion_filter(item["motion"], frames), "-frames:v", str(frames), "-an", "-c:v", "libx264",
            "-preset", "ultrafast", "-crf", "20", "-r", "30", "-pix_fmt", "yuv420p", str(output)]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode: raise RuntimeError(f"segment render failed for {item['shot_id']}: {completed.stderr.strip()}")
        segments.append(output)
    concat_file = segment_dir / "concat.txt"
    concat_file.write_text("\n".join(f"file '{path.resolve().as_posix()}'" for path in segments), encoding="utf-8")
    silent = root / "renders" / "previews" / "video.mp4"
    completed = subprocess.run([executable, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_file), "-c", "copy", str(silent)], capture_output=True, text=True, check=False)
    if completed.returncode: raise RuntimeError(completed.stderr.strip())
    final = root / "renders" / "final" / f"{root.name}.mp4"; final.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run([executable, "-hide_banner", "-loglevel", "error", "-y", "-i", str(silent),
        "-i", str(root / timeline["audio_mix"]["path"]), "-i", str(root / "subtitles" / "narration.srt"),
        "-map", "0:v:0", "-map", "1:a:0", "-map", "2:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-c:s", "mov_text", "-metadata:s:s:0", "language=eng", "-t", str(timeline["duration_seconds"]),
        "-movflags", "+faststart", str(final)],
        capture_output=True, text=True, check=False)
    if completed.returncode or not final.is_file(): raise RuntimeError(completed.stderr.strip() or "final mux failed")
    return {"schema_version": 1, "status": "rendered", "path": final.relative_to(root).as_posix(),
        "sha256": sha256_file(final), "segment_count": len(segments), "fps": 30, "width": 1920, "height": 1080,
        "safe_framing": "contained-subject-over-blurred-extension-v1"}

def probe_video(path: Path, executable: str = "ffprobe") -> dict:
    completed = subprocess.run([executable, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=False)
    if completed.returncode: raise RuntimeError(completed.stderr.strip())
    return json.loads(completed.stdout)
