"""Final encoded-media quality audit."""
from __future__ import annotations
import re, subprocess
from pathlib import Path
from .artifacts import sha256_file
from .render import probe_video

def evaluate_video(path: Path, expected_duration: float, rights: dict, ffmpeg: str = "ffmpeg") -> dict:
    probe = probe_video(path); streams = probe.get("streams", [])
    video = next((x for x in streams if x.get("codec_type") == "video"), {})
    audio = next((x for x in streams if x.get("codec_type") == "audio"), {})
    subtitle = next((x for x in streams if x.get("codec_type") == "subtitle"), {})
    duration = float(probe.get("format", {}).get("duration", 0)); issues = []
    if video.get("codec_name") != "h264" or video.get("width") != 1920 or video.get("height") != 1080: issues.append("video contract is not H.264 1920x1080")
    if video.get("avg_frame_rate") != "30/1": issues.append("video frame rate is not 30 fps")
    if audio.get("codec_name") != "aac": issues.append("audio stream is not AAC")
    if subtitle.get("codec_name") not in {"mov_text", "srt"}: issues.append("English subtitle stream is missing")
    if abs(duration - expected_duration) > 0.75: issues.append("encoded duration differs from authoritative timeline")
    black = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path), "-vf", "blackdetect=d=1:pic_th=0.98:pix_th=0.02", "-an", "-f", "null", "-"], capture_output=True, text=True, check=False)
    black_events = re.findall(r"black_start:[^\r\n]+", black.stderr)
    if black_events: issues.append("sustained black frames detected")
    volume = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True, check=False)
    peak_match = re.search(r"max_volume:\s*(-?[0-9.]+) dB", volume.stderr)
    peak_db = float(peak_match.group(1)) if peak_match else None
    if peak_db is None or peak_db > 0: issues.append("audio peak could not be validated")
    rights_blocked = rights.get("status") == "unverified" or bool(rights.get("release_blocked"))
    return {"schema_version": 1, "status": "passed" if not issues else "failed", "release_status": "blocked_rights" if rights_blocked else "release_ready",
        "issues": issues, "video": {"path": path.as_posix(), "sha256": sha256_file(path), "duration_seconds": duration,
        "codec": video.get("codec_name"), "width": video.get("width"), "height": video.get("height"), "fps": video.get("avg_frame_rate")},
        "audio": {"codec": audio.get("codec_name"), "peak_db": peak_db}, "subtitle_codec": subtitle.get("codec_name"),
        "black_frame_events": black_events, "rights": rights}
