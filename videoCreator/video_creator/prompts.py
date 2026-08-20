"""Deterministic image prompts and optional character-reference requirements."""

from __future__ import annotations

import json
import re

from .artifacts import sha256_text


COMPILER = "deterministic-prompt-compiler-v1"
NEGATIVE_PROMPT = (
    "text, watermark, logo, duplicate character, extra limbs, identity drift, "
    "anachronistic objects, inconsistent costume"
)


def _fingerprint(shot: dict, prompt: str, reference_ids: list[str], style: str) -> str:
    return sha256_text(json.dumps({
        "compiler": COMPILER,
        "shot_dependency_sha256": shot["dependency_sha256"],
        "prompt": prompt,
        "negative_prompt": NEGATIVE_PROMPT,
        "reference_ids": reference_ids,
        "style": style,
    }, sort_keys=True, ensure_ascii=False))


def compile_prompts(
    storyboard: dict, analysis: dict, *, style: str = "cinematic illustrated realism",
    previous: dict | None = None, source_text: str | None = None,
) -> dict:
    """Compile every accepted shot without requiring a visual decision prompt."""
    if storyboard.get("status") != "auto_accepted":
        raise ValueError("prompt compilation requires an accepted storyboard")
    if not style.strip():
        raise ValueError("style must be nonempty")
    entities = {
        item["canonical_id"]: item for item in analysis.get("entities", [])
        if item.get("review_status") == "approved"
    }
    character_ids = {
        identifier for identifier, item in entities.items() if item.get("kind") == "character"
    }
    requirements = []
    for identifier in sorted(character_ids):
        entity = entities[identifier]
        name = entity.get("canonical_name") or entity["name"]
        aliases = [name, *entity.get("aliases", [])]
        evidence = _character_evidence(source_text, aliases) if source_text else []
        brief = (
            f"Source-evidenced character design for {name}. "
            + (" Evidence: " + " | ".join(evidence) if evidence else "No visual evidence available.")
        )
        requirements.append({
            "reference_id": f"character-{identifier}",
            "canonical_entity_id": identifier, "canonical_name": name,
            "aliases": sorted(set(aliases), key=str.casefold),
            "source_evidence": evidence, "reference_prompt": brief,
            "selection_mode": "optional_user_override",
            "default_action": "generate_and_auto_rank", "status": "default_ready",
        })
    prior = {item.get("shot_id"): item for item in (previous or {}).get("prompts", [])}
    prompts = []
    reused = []
    regenerated = []
    for shot in storyboard["shots"]:
        names = [
            entities[identifier].get("canonical_name") or entities[identifier]["name"]
            for identifier in shot["canonical_entity_ids"] if identifier in entities
        ]
        references = [
            f"character-{identifier}" for identifier in shot["canonical_entity_ids"]
            if identifier in character_ids
        ]
        subjects = ", ".join(names) if names else "environmental storytelling"
        prompt = (
            f"{style}. {shot['composition']} Depict {subjects}. "
            f"Mood: {shot['mood']}. Composition supports {shot['motion'].replace('_', ' ')} motion."
        )
        fingerprint = _fingerprint(shot, prompt, references, style)
        existing = prior.get(shot["shot_id"])
        if existing and existing.get("dependency_sha256") == fingerprint:
            prompts.append(existing)
            reused.append(shot["shot_id"])
            continue
        regenerated.append(shot["shot_id"])
        prompts.append({
            "shot_id": shot["shot_id"],
            "prompt": prompt,
            "negative_prompt": NEGATIVE_PROMPT,
            "reference_ids": references,
            "style": style,
            "dependency_sha256": fingerprint,
            "status": "auto_accepted",
        })
    return {
        "schema_version": 1,
        "prompt_set_id": "prompt-set-0001",
        "storyboard_id": storyboard["storyboard_id"],
        "source_sha256": storyboard["source_sha256"],
        "compiler": COMPILER,
        "status": "auto_accepted",
        "release_usable": False,
        "reference_requirements": requirements,
        "prompts": prompts,
        "regeneration": {
            "reused_shot_ids": reused,
            "regenerated_shot_ids": regenerated,
        },
    }


def _character_evidence(source_text: str, aliases: list[str]) -> list[str]:
    """Collect bounded source passages around canonical names and aliases."""
    matches = []
    for alias in sorted(set(aliases), key=lambda value: (-len(value), value.casefold())):
        if len(alias.strip()) < 3:
            continue
        for match in re.finditer(rf"\b{re.escape(alias)}\b", source_text, re.IGNORECASE):
            start = max(0, match.start() - 500)
            end = min(len(source_text), match.end() + 500)
            excerpt = " ".join(source_text[start:end].split())
            matches.append((match.start(), excerpt))
    unique = []
    for _offset, excerpt in sorted(matches):
        if excerpt not in unique:
            unique.append(excerpt)
        if len(unique) == 3:
            break
    return unique


def validate_prompts(compiled: dict, storyboard: dict, analysis: dict) -> list[str]:
    """Validate prompt coverage and constrain references to canonical characters."""
    issues = []
    if compiled.get("status") != "auto_accepted" or compiled.get("release_usable"):
        issues.append("prompts must be automatically accepted non-release artifacts")
    expected = [shot["shot_id"] for shot in storyboard.get("shots", [])]
    actual = [item.get("shot_id") for item in compiled.get("prompts", [])]
    if actual != expected:
        issues.append("prompts must cover every shot exactly once and in order")
    characters = {
        item["canonical_id"] for item in analysis.get("entities", [])
        if item.get("review_status") == "approved" and item.get("kind") == "character"
    }
    valid_references = {f"character-{identifier}" for identifier in characters}
    requirement_ids = {
        item.get("reference_id") for item in compiled.get("reference_requirements", [])
    }
    if requirement_ids != valid_references:
        issues.append("reference requirements must cover canonical characters exactly")
    for item in compiled.get("reference_requirements", []):
        if not str(item.get("reference_prompt") or "").strip():
            issues.append(f"empty reference prompt for {item.get('reference_id')}")
    for item in compiled.get("prompts", []):
        identifier = item.get("shot_id")
        if not str(item.get("prompt") or "").strip() or not str(item.get("negative_prompt") or "").strip():
            issues.append(f"empty image prompt for {identifier}")
        unknown = set(item.get("reference_ids", [])) - valid_references
        if unknown:
            issues.append(f"unknown character references for {identifier}: {sorted(unknown)}")
        fingerprint = str(item.get("dependency_sha256") or "")
        if len(fingerprint) != 64 or any(value not in "0123456789abcdef" for value in fingerprint):
            issues.append(f"invalid prompt dependency fingerprint for {identifier}")
        if item.get("status") != "auto_accepted":
            issues.append(f"unaccepted prompt: {identifier}")
    return issues
