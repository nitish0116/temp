"""Autonomous, dependency-aware storyboard shot planning."""

from __future__ import annotations

import json
import math

from .artifacts import sha256_text


MOTIONS = ("slow_push", "slow_pan", "static_hold", "slow_pull")


def _shot_fingerprint(scene: dict, index: int, duration: float) -> str:
    payload = {
        "planner": "deterministic-storyboard-v1",
        "scene_dependency_sha256": scene["dependency_sha256"],
        "shot_index": index,
        "duration_seconds": duration,
        "mood": scene["mood"],
        "visual_intent": scene["visual_intent"],
    }
    return sha256_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def plan_storyboard(
    scenes: dict, *, target_shot_seconds: float = 15.0,
    previous_storyboard: dict | None = None,
) -> dict:
    """Create deterministic shots covering every automatically accepted scene."""
    if scenes.get("status") != "auto_accepted":
        raise ValueError("storyboard planning requires automatically accepted scenes")
    if not 5 <= target_shot_seconds <= 30:
        raise ValueError("target_shot_seconds must be between 5 and 30")
    shots = []
    previous = {
        shot.get("shot_id"): shot for shot in (previous_storyboard or {}).get("shots", [])
    }
    reused = []
    regenerated = []
    for scene in scenes["scenes"]:
        if scene.get("status") != "auto_accepted":
            raise ValueError(f"storyboard planning requires accepted scene: {scene.get('scene_id')}")
        scene_duration = float(scene["estimated_narration_seconds"])
        count = max(1, min(6, math.ceil(scene_duration / target_shot_seconds)))
        duration = round(scene_duration / count, 3)
        for index in range(1, count + 1):
            shot_id = f"{scene['scene_id']}-shot-{index:03d}"
            fingerprint = _shot_fingerprint(scene, index, duration)
            prior = previous.get(shot_id)
            if prior and prior.get("dependency_sha256") == fingerprint:
                shots.append(prior)
                reused.append(shot_id)
                continue
            regenerated.append(shot_id)
            shots.append({
                "shot_id": shot_id,
                "scene_id": scene["scene_id"],
                "sequence": index,
                "duration_seconds": duration,
                "setting_id": scene["setting_id"],
                "canonical_entity_ids": list(scene["canonical_entity_ids"]),
                "composition": scene["visual_intent"],
                "mood": scene["mood"],
                "motion": MOTIONS[(index - 1) % len(MOTIONS)],
                "dependency_sha256": fingerprint,
                "status": "auto_accepted",
            })
    return {
        "schema_version": 1,
        "storyboard_id": "storyboard-0001",
        "scene_plan_id": scenes["scene_plan_id"],
        "source_sha256": scenes["source_sha256"],
        "planner": "deterministic-storyboard-v1",
        "target_shot_seconds": target_shot_seconds,
        "status": "auto_accepted",
        "release_usable": False,
        "shots": shots,
        "regeneration": {
            "reused_shot_ids": reused,
            "regenerated_shot_ids": regenerated,
        },
    }


def validate_storyboard(storyboard: dict, scenes: dict) -> list[str]:
    """Validate shot coverage, timing, identity, and dependency evidence."""
    issues = []
    if storyboard.get("status") != "auto_accepted" or storyboard.get("release_usable"):
        issues.append("storyboard must be an automatically accepted non-release artifact")
    scene_map = {scene["scene_id"]: scene for scene in scenes.get("scenes", [])}
    grouped = {identifier: [] for identifier in scene_map}
    identifiers = set()
    for shot in storyboard.get("shots", []):
        identifier = shot.get("shot_id")
        scene = scene_map.get(shot.get("scene_id"))
        if not identifier or identifier in identifiers:
            issues.append("shot IDs must be nonempty and unique")
        identifiers.add(identifier)
        if scene is None:
            issues.append(f"unknown scene for {identifier}")
            continue
        grouped[scene["scene_id"]].append(shot)
        if shot.get("setting_id") != scene.get("setting_id"):
            issues.append(f"setting mismatch for {identifier}")
        if set(shot.get("canonical_entity_ids", [])) != set(scene.get("canonical_entity_ids", [])):
            issues.append(f"entity mismatch for {identifier}")
        if float(shot.get("duration_seconds", 0)) <= 0:
            issues.append(f"invalid duration for {identifier}")
        if shot.get("status") != "auto_accepted":
            issues.append(f"unaccepted shot: {identifier}")
        fingerprint = str(shot.get("dependency_sha256") or "")
        if len(fingerprint) != 64 or any(value not in "0123456789abcdef" for value in fingerprint):
            issues.append(f"invalid dependency fingerprint for {identifier}")
    for scene_id, scene_shots in grouped.items():
        if not scene_shots:
            issues.append(f"scene has no shots: {scene_id}")
            continue
        expected = list(range(1, len(scene_shots) + 1))
        if [shot.get("sequence") for shot in scene_shots] != expected:
            issues.append(f"invalid shot sequence for {scene_id}")
        total = sum(float(shot["duration_seconds"]) for shot in scene_shots)
        if abs(total - float(scene_map[scene_id]["estimated_narration_seconds"])) > 0.01:
            issues.append(f"shot duration coverage mismatch for {scene_id}")
    return issues
