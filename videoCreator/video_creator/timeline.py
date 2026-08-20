"""Master visual timeline and narration mix compilation."""
from __future__ import annotations
import subprocess
from pathlib import Path
from .artifacts import sha256_file

def compile_timeline(scenes: dict, storyboard: dict, assets: dict, audio: dict) -> dict:
    audio_map = {x["narration_id"]: x for x in audio["clips"]}
    asset_map = {x["asset_id"]: x for x in assets["assets"] if x.get("kind") == "shot"}
    shots_by_scene = {}
    for shot in storyboard["shots"]: shots_by_scene.setdefault(shot["scene_id"], []).append(shot)
    intervals, cursor = [], 0.0
    for scene in scenes["scenes"]:
        duration = sum(float(audio_map[x]["duration_seconds"]) for x in scene["narration_ids"])
        shots = shots_by_scene[scene["scene_id"]]; shot_duration = duration / len(shots)
        for index, shot in enumerate(shots):
            asset = asset_map[shot["shot_id"]]
            selected = next(x for x in asset["candidates"] if x["candidate_id"] == asset["selected_candidate_id"])
            end = cursor + shot_duration
            intervals.append({"shot_id": shot["shot_id"], "scene_id": scene["scene_id"],
                "start_seconds": round(cursor, 3), "end_seconds": round(end, 3),
                "image": selected["path"], "image_sha256": selected["sha256"], "motion": shot["motion"]})
            cursor = end
        cursor = intervals[-1]["end_seconds"]
    return {"schema_version": 1, "status": "auto_accepted", "fps": 30,
        "width": 1920, "height": 1080, "duration_seconds": round(cursor, 3), "intervals": intervals}

def mix_narration(audio: dict, root: Path, output: Path, executable: str = "ffmpeg") -> dict:
    concat = output.with_suffix(".concat.txt"); concat.parent.mkdir(parents=True, exist_ok=True)
    concat.write_text("\n".join(f"file '{(root / x['path']).resolve().as_posix()}'" for x in audio["clips"]), encoding="utf-8")
    completed = subprocess.run([executable, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
        "-safe", "0", "-i", str(concat), "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1", str(output)],
        capture_output=True, text=True, check=False)
    concat.unlink(missing_ok=True)
    if completed.returncode or not output.is_file(): raise RuntimeError(completed.stderr.strip() or "audio mix failed")
    return {"path": output.relative_to(root).as_posix(), "sha256": sha256_file(output),
            "strategy": "narration-only-normalized-concat-v1", "status": "auto_accepted"}

def validate_timeline(timeline: dict, root: Path) -> list[str]:
    issues, cursor = [], 0.0
    for item in timeline.get("intervals", []):
        if abs(float(item["start_seconds"]) - cursor) > 0.002: issues.append(f"timeline gap before {item['shot_id']}")
        if float(item["end_seconds"]) <= float(item["start_seconds"]): issues.append(f"invalid interval {item['shot_id']}")
        path = root / item["image"]
        if not path.is_file() or sha256_file(path) != item["image_sha256"]: issues.append(f"invalid image {item['shot_id']}")
        cursor = float(item["end_seconds"])
    if abs(cursor - float(timeline.get("duration_seconds", 0))) > 0.002: issues.append("timeline duration mismatch")
    return issues
