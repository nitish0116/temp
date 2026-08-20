"""Deterministic scene segmentation from validated narration blocks."""

from __future__ import annotations

import json
import re
from typing import Protocol

from .artifacts import sha256_text


class SceneEnrichmentProvider(Protocol):
    """Provider contract for autonomous editorial scene decisions."""

    name: str

    def enrich(self, scene: dict, narration_blocks: list[dict]) -> dict:
        """Return source-bound event, mood, and visual intent for one scene."""


class DeterministicSceneEnrichmentProvider:
    """Offline fallback that derives safe decisions from validated narration."""

    name = "deterministic-scene-enrichment-v1"

    def enrich(self, scene: dict, narration_blocks: list[dict]) -> dict:
        text = " ".join(block["adapted_text"].strip() for block in narration_blocks)
        first_sentence = text.split(". ", 1)[0].strip().rstrip(".") + "."
        tones = [block["tone"].split(",", 1)[0].strip() for block in narration_blocks]
        mood = tones[0] if tones else "neutral"
        subjects = scene.get("canonical_entity_ids") or []
        subject = ", ".join(subjects) if subjects else "environmental details"
        setting = scene.get("setting_id") or "unspecified setting"
        return {
            "story_event": first_sentence,
            "mood": mood,
            "visual_intent": (
                f"Establish {setting}; focus on {subject}; support the narrated event "
                "without adding characters, text, or chronology."
            ),
            "canonical_entity_ids": list(subjects),
        }


def scene_dependency_sha256(
    scene: dict, narration_blocks: list[dict], provider_name: str, *,
    acceptance_threshold: float, maximum_attempts: int, fallback_provider_name: str,
) -> str:
    """Fingerprint every input that can change an enriched scene decision."""
    payload = {
        "scene": {
            key: scene.get(key) for key in (
                "scene_id", "narration_ids", "source_start", "source_end",
                "setting_id", "canonical_entity_ids",
                "estimated_narration_seconds",
            )
        },
        "narration": [{
            key: block.get(key) for key in (
                "narration_id", "source_sha256", "adapted_text", "tone",
                "canonical_entity_ids",
            )
        } for block in narration_blocks],
        "provider": provider_name,
        "automatic_qa_policy": {
            "acceptance_threshold": acceptance_threshold,
            "maximum_attempts": maximum_attempts,
            "fallback_provider": fallback_provider_name,
        },
    }
    return sha256_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))


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
    setting_starts = sorted(
        (item["source_start"], item["canonical_id"])
        for item in analysis.get("settings", [])
        if item.get("review_status") == "approved"
    )
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
        scene_start = int(scene.get("source_start", -1))
        scene_end = int(scene.get("source_end", -1))
        crossings = [
            canonical_id for start, canonical_id in setting_starts
            if scene_start < start < scene_end
        ]
        if crossings:
            issues.append(f"scene crosses setting boundary for {identifier}: {crossings}")
        unknown = set(scene.get("canonical_entity_ids", [])) - valid_entities
        if unknown:
            issues.append(f"unknown entities for {identifier}: {sorted(unknown)}")
        if float(scene.get("estimated_narration_seconds", 0)) <= 0:
            issues.append(f"invalid duration estimate for {identifier}")
    return issues


def enrich_scenes(
    scenes: dict, narration: dict, provider: SceneEnrichmentProvider | None = None,
    *, acceptance_threshold: float = 0.8, maximum_attempts: int = 2,
    fallback_provider: SceneEnrichmentProvider | None = None,
    previous_scenes: dict | None = None,
) -> dict:
    """Enrich and automatically promote scenes using bounded QA decisions."""
    if scenes.get("status") != "draft":
        raise ValueError("scene enrichment requires a draft scene plan")
    if not 0 < acceptance_threshold <= 1:
        raise ValueError("acceptance_threshold must be between zero and one")
    if maximum_attempts < 1:
        raise ValueError("maximum_attempts must be positive")
    selected = provider or DeterministicSceneEnrichmentProvider()
    fallback = fallback_provider or DeterministicSceneEnrichmentProvider()
    blocks = {item["narration_id"]: item for item in narration["blocks"]}
    previous = {
        item.get("scene_id"): item for item in (previous_scenes or {}).get("scenes", [])
    }
    enriched = []
    reused_ids = []
    regenerated_ids = []
    for scene in scenes["scenes"]:
        scene_blocks = [blocks[identifier] for identifier in scene["narration_ids"]]
        dependency_sha256 = scene_dependency_sha256(
            scene, scene_blocks, selected.name,
            acceptance_threshold=acceptance_threshold,
            maximum_attempts=maximum_attempts,
            fallback_provider_name=fallback.name,
        )
        prior = previous.get(scene["scene_id"])
        if (
            prior
            and prior.get("dependency_sha256") == dependency_sha256
            and prior.get("status") == "auto_accepted"
        ):
            enriched.append(prior)
            reused_ids.append(scene["scene_id"])
            continue
        regenerated_ids.append(scene["scene_id"])
        narration_text = " ".join(block["adapted_text"] for block in scene_blocks).casefold()
        narration_terms = set(re.findall(r"[a-z]{4,}", narration_text))
        attempts = []
        decision = {}
        checks = {}
        confidence = 0.0
        used_provider = selected
        for attempt in range(1, maximum_attempts + 2):
            used_provider = selected if attempt <= maximum_attempts else fallback
            try:
                decision = used_provider.enrich(scene, scene_blocks)
                event = str(decision.get("story_event") or "").strip()
                visual = str(decision.get("visual_intent") or "").strip().casefold()
                event_terms = set(re.findall(r"[a-z]{4,}", event.casefold()))
                overlap = len(event_terms & narration_terms) / max(1, len(event_terms))
                expected_entities = set(scene["canonical_entity_ids"])
                supplied_entities = set(decision.get("canonical_entity_ids", []))
                checks = {
                    "story_event_present": bool(event),
                    "story_event_supported": overlap >= 0.5,
                    "mood_present": bool(str(decision.get("mood") or "").strip()),
                    "visual_intent_present": bool(visual),
                    "setting_grounded": str(scene.get("setting_id") or "").casefold() in visual,
                    "entities_preserved": supplied_entities == expected_entities,
                    "entities_grounded": all(entity.casefold() in visual for entity in expected_entities),
                }
                confidence = sum(checks.values()) / len(checks)
                error = None
            except Exception as exc:  # provider failures are retryable pipeline data
                checks = {"provider_succeeded": False}
                confidence = 0.0
                error = str(exc)
            accepted = confidence >= acceptance_threshold and all(checks.values())
            attempts.append({
                "attempt": attempt,
                "provider": used_provider.name,
                "confidence": confidence,
                "checks": checks,
                "error": error,
                "decision": "accept" if accepted else "retry",
            })
            if accepted or attempt > maximum_attempts:
                break
        item = dict(scene)
        item.update({
            "dependency_sha256": dependency_sha256,
            "story_event": str(decision.get("story_event") or "").strip(),
            "mood": str(decision.get("mood") or "").strip(),
            "visual_intent": str(decision.get("visual_intent") or "").strip(),
            "status": "auto_accepted" if accepted else "retry_required",
            "automatic_qa": {
                "provider": used_provider.name,
                "confidence": confidence,
                "acceptance_threshold": acceptance_threshold,
                "checks": checks,
                "attempt": attempts[-1]["attempt"],
                "maximum_attempts": maximum_attempts,
                "decision": "accept" if accepted else "retry",
                "used_fallback": used_provider.name == fallback.name and provider is not None,
                "attempts": attempts,
            },
        })
        enriched.append(item)
    accepted = all(item["status"] == "auto_accepted" for item in enriched)
    return {
        **{key: value for key, value in scenes.items() if key != "scenes"},
        "status": "auto_accepted" if accepted else "retry_required",
        "release_usable": False,
        "provider": selected.name,
        "scenes": enriched,
        "exception_report": [] if accepted else [
            {
                "scene_id": item["scene_id"],
                "reason": "automatic scene QA did not meet its acceptance threshold",
                "next_action": "retry",
            }
            for item in enriched if item["status"] == "retry_required"
        ],
        "regeneration": {
            "reused_scene_ids": reused_ids,
            "regenerated_scene_ids": regenerated_ids,
        },
    }


def validate_enriched_scenes(scenes: dict, narration: dict, analysis: dict) -> list[str]:
    """Validate autonomous enrichment, QA evidence, and promotion decisions."""
    draft = {**scenes, "status": "draft", "release_usable": False}
    issues = validate_scenes(draft, narration, analysis)
    expected_status = "auto_accepted" if all(
        scene.get("status") == "auto_accepted" for scene in scenes.get("scenes", [])
    ) else "retry_required"
    if scenes.get("status") != expected_status:
        issues.append("scene plan status does not match automatic scene decisions")
    for scene in scenes.get("scenes", []):
        identifier = scene.get("scene_id")
        for field in ("story_event", "mood", "visual_intent"):
            if not str(scene.get(field) or "").strip():
                issues.append(f"missing {field} for {identifier}")
        qa = scene.get("automatic_qa", {})
        if not re.fullmatch(r"[0-9a-f]{64}", str(scene.get("dependency_sha256") or "")):
            issues.append(f"invalid dependency fingerprint for {identifier}")
        checks = qa.get("checks", {})
        if not checks or not all(checks.values()):
            if scene.get("status") != "retry_required":
                issues.append(f"failed automatic QA must require retry for {identifier}")
        elif scene.get("status") != "auto_accepted":
            issues.append(f"passing automatic QA must accept {identifier}")
    return issues
