"""Autonomous, dependency-aware storyboard shot planning."""

from __future__ import annotations

import json
import math
import re

from .artifacts import sha256_text


MOTIONS = ("slow_push", "slow_pan", "static_hold", "slow_pull")
PLANNER = "deterministic-storyboard-v2-pov-coreference"


def _shot_fingerprint(
    scene: dict, index: int, duration: float, narrative_beat: str,
    canonical_entity_ids: list[str],
) -> str:
    payload = {
        "planner": PLANNER,
        "scene_dependency_sha256": scene["dependency_sha256"],
        "shot_index": index,
        "duration_seconds": duration,
        "mood": scene["mood"],
        "visual_intent": scene["visual_intent"],
        "narrative_beat": narrative_beat,
        "canonical_entity_ids": canonical_entity_ids,
    }
    return sha256_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def plan_storyboard(
    scenes: dict, *, target_shot_seconds: float = 15.0,
    previous_storyboard: dict | None = None, narration: dict | None = None,
    analysis: dict | None = None,
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
    narration_map = {
        item["narration_id"]: item["adapted_text"]
        for item in (narration or {}).get("blocks", [])
    }
    complete_narration = " ".join(narration_map.values())
    entity_terms = {
        item["canonical_id"]: sorted({
            str(item.get("name") or ""), str(item.get("canonical_name") or ""),
            *(str(value) for value in item.get("aliases", [])),
        } - {""}, key=lambda value: (-len(value), value.casefold()))
        for item in (analysis or {}).get("entities", [])
        if item.get("review_status") == "approved" and item.get("kind") == "character"
    }
    for identifier, terms in entity_terms.items():
        canonical_terms = [term for term in terms if " " in term or len(term) >= 4]
        if any(re.search(
            rf"\b(?:my\s+)?father\s*[,—-]?\s*{re.escape(term)}\b",
            complete_narration, re.I,
        ) for term in canonical_terms):
            entity_terms[identifier] = [*terms, "my father", "father"]
        elif any(re.search(
            rf"\b(?:my\s+)?mother\s*[,—-]?\s*{re.escape(term)}\b",
            complete_narration, re.I,
        ) for term in canonical_terms):
            entity_terms[identifier] = [*terms, "my mother", "mother"]
    viewpoint_candidates = [
        identifier for identifier, terms in entity_terms.items()
        if any(re.search(
            rf"\bmy\s+(?:full\s+)?name\b[^.!?]{{0,100}}\b{re.escape(term)}\b",
            complete_narration, re.I,
        ) for term in terms if len(term) >= 3)
    ]
    viewpoint = viewpoint_candidates[0] if len(viewpoint_candidates) == 1 else None
    for scene in scenes["scenes"]:
        if scene.get("status") != "auto_accepted":
            raise ValueError(f"storyboard planning requires accepted scene: {scene.get('scene_id')}")
        scene_duration = float(scene["estimated_narration_seconds"])
        count = max(1, min(6, math.ceil(scene_duration / target_shot_seconds)))
        duration = round(scene_duration / count, 3)
        sentences = [
            value.strip() for identifier in scene.get("narration_ids", [])
            for value in re.split(r"(?<=[.!?])\s+", narration_map.get(identifier, ""))
            if value.strip()
        ]
        if not sentences:
            sentences = [scene["story_event"]]
        resolved_entities = []
        if entity_terms:
            explicit_by_sentence = [[
                identifier for identifier in scene["canonical_entity_ids"]
                if any(re.search(rf"\b{re.escape(term)}\b", sentence, re.I)
                       for term in entity_terms.get(identifier, []))
            ] for sentence in sentences]
            last_entities = []
            for sentence, identifiers in zip(sentences, explicit_by_sentence):
                local = list(identifiers)
                first_person = bool(re.search(r"\b(?:I|me|my|mine)\b", sentence, re.I))
                third_person = bool(re.search(
                    r"\b(?:he|him|his|she|her|hers|they|them|their)\b", sentence, re.I,
                ))
                if viewpoint and first_person:
                    local = list(dict.fromkeys([viewpoint, *local]))
                if last_entities and re.search(
                    r"\b(?:he|him|his|she|her|hers|they|them|their)\b", sentence, re.I,
                ):
                    local = list(dict.fromkeys([*last_entities, *local]))
                if local:
                    last_entities = list(identifiers or local)
                elif not first_person and not third_person:
                    last_entities = []
                resolved_entities.append(local)
        else:
            resolved_entities = [list(scene["canonical_entity_ids"]) for _ in sentences]
        phases = ("Opening", "Development", "Escalation", "Revelation", "Reaction", "Transition")
        for index in range(1, count + 1):
            shot_id = f"{scene['scene_id']}-shot-{index:03d}"
            sentence_index = min(len(sentences) - 1, math.floor((index - 1) * len(sentences) / count))
            sentence = sentences[sentence_index]
            narrative_beat = f"{phases[index - 1]} beat: {sentence}"
            local_entities = resolved_entities[sentence_index]
            fingerprint = _shot_fingerprint(
                scene, index, duration, narrative_beat, local_entities,
            )
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
                "canonical_entity_ids": local_entities,
                "composition": scene["visual_intent"],
                "narrative_beat": narrative_beat,
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
        "planner": PLANNER,
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
        if not set(shot.get("canonical_entity_ids", [])).issubset(
            set(scene.get("canonical_entity_ids", []))
        ):
            issues.append(f"entity mismatch for {identifier}")
        if float(shot.get("duration_seconds", 0)) <= 0:
            issues.append(f"invalid duration for {identifier}")
        if shot.get("status") != "auto_accepted":
            issues.append(f"unaccepted shot: {identifier}")
        if not str(shot.get("narrative_beat") or "").strip():
            issues.append(f"missing narrative beat for {identifier}")
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
        beats = [shot.get("narrative_beat") for shot in scene_shots]
        if len(set(beats)) != len(beats):
            issues.append(f"shot narrative beats must be distinct for {scene_id}")
    return issues
