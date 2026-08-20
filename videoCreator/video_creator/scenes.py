"""Deterministic scene segmentation from validated narration blocks."""

from __future__ import annotations


def segment_scenes(narration: dict, analysis: dict, *, maximum_blocks: int = 2) -> dict:
    """Group adjacent narration blocks without crossing canonical settings."""
    if narration.get("status") != "adapted_draft":
        raise ValueError("scene segmentation requires adapted narration")
    if maximum_blocks < 1:
        raise ValueError("maximum_blocks must be positive")
    settings = sorted(
        (item for item in analysis.get("settings", []) if item.get("review_status") == "approved"),
        key=lambda item: item["source_start"],
    )

    def setting_for(position: int) -> str | None:
        """Resolve the latest approved setting at a source position."""
        matching = [item for item in settings if item["source_start"] <= position]
        return matching[-1]["canonical_id"] if matching else None

    groups = []
    current = []
    current_setting = None
    for block in narration["blocks"]:
        setting = setting_for(block["source_start"])
        if current and (setting != current_setting or len(current) >= maximum_blocks):
            groups.append((current_setting, current))
            current = []
        current_setting = setting
        current.append(block)
    if current:
        groups.append((current_setting, current))
    scenes = []
    for index, (setting, blocks) in enumerate(groups, start=1):
        words = sum(len(block["adapted_text"].split()) for block in blocks)
        scenes.append({
            "scene_id": f"scene-{index:04d}",
            "narration_ids": [block["narration_id"] for block in blocks],
            "source_start": blocks[0]["source_start"],
            "source_end": blocks[-1]["source_end"],
            "setting_id": setting,
            "canonical_entity_ids": sorted({
                entity for block in blocks for entity in block["canonical_entity_ids"]
            }),
            "estimated_narration_seconds": round(words / 2.5, 2),
            "mood": None,
            "story_event": None,
            "status": "draft",
        })
    return {
        "schema_version": 1, "scene_plan_id": "scene-plan-0001",
        "source_sha256": narration["source_sha256"],
        "narration_plan_id": narration["narration_plan_id"],
        "status": "draft", "release_usable": False, "scenes": scenes,
    }


def validate_scenes(scenes: dict, narration: dict, analysis: dict) -> list[str]:
    """Return coverage, ordering, and canonical-reference failures."""
    issues = []
    if scenes.get("status") != "draft" or scenes.get("release_usable"):
        issues.append("scene plan must remain a non-release draft")
    expected = [block["narration_id"] for block in narration.get("blocks", [])]
    actual = [identifier for scene in scenes.get("scenes", []) for identifier in scene.get("narration_ids", [])]
    if actual != expected:
        issues.append("scenes must cover every narration block exactly once and in order")
    valid_settings = {
        item["canonical_id"] for item in analysis.get("settings", [])
        if item.get("review_status") == "approved"
    }
    valid_entities = {
        item["canonical_id"] for item in analysis.get("entities", [])
        if item.get("review_status") == "approved"
    }
    identifiers = set()
    for scene in scenes.get("scenes", []):
        identifier = scene.get("scene_id")
        if not identifier or identifier in identifiers:
            issues.append("scene IDs must be nonempty and unique")
        identifiers.add(identifier)
        if scene.get("setting_id") not in valid_settings:
            issues.append(f"unknown setting for {identifier}")
        unknown = set(scene.get("canonical_entity_ids", [])) - valid_entities
        if unknown:
            issues.append(f"unknown entities for {identifier}: {sorted(unknown)}")
        if float(scene.get("estimated_narration_seconds", 0)) <= 0:
            issues.append(f"invalid duration estimate for {identifier}")
    return issues
